"""路由层指标（E5-2）— 原子性判定路由的准确率口径。

判据本身落在 `agent/registry.py::_seed_deep_agent`（并入 L3 同一提示词，**零额外
LLM 调用**）；本模块只负责量准确率，三组口径：

  - `single_hop_misroute_rate`：单原子问题误入 `deep_agent` 的比例 —— **成本闸门**
    （目标 <5%：误入即多付一次 plan→execute→synthesize→reflect）。
  - `multi_hop_hit_rate` / `complex_hit_rate`：多原子问题命中 `deep_agent` 的比例
    （目标 ≥80%：命中才有分解收益，见 §9 分解实验 0.35→0.70）。
  - `route_accuracy`：与期望路由逐条一致率（含非 deep_agent 分支）。

**期望路由的来源**：`EvalSample.expected_route` 优先（complex 集显式标注）；缺省时按
金标 `query_type` 推导 —— `single_hop → knowledge_qa`、`multi_hop → deep_agent`。
即：金标的 single/multi 标注就是**多原子标注的代理真值**。
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Sequence

from ..datasets.rag import COMPLEX, MULTI_HOP, SINGLE_HOP, EvalSample

DEEP_AGENT = "deep_agent"
KNOWLEDGE_QA = "knowledge_qa"


def expected_route_of(sample: EvalSample) -> str:
    """一条样本的期望路由（显式 `expected_route` 优先，否则由 `query_type` 推导）。"""
    if sample.expected_route:
        return sample.expected_route
    if sample.query_type == MULTI_HOP:
        return DEEP_AGENT
    if sample.query_type == COMPLEX:
        return DEEP_AGENT
    return KNOWLEDGE_QA


def build_rows(samples: Sequence[EvalSample], got: Dict[str, str]) -> List[dict]:
    """把「问题 → 实际路由」拼成待评测行（缺结果的样本记为 `error`）。"""
    rows: List[dict] = []
    for s in samples:
        rows.append({
            "question": s.question,
            "query_type": s.query_type,
            "expected": expected_route_of(s),
            "got": got.get(s.question, "error"),
        })
    return rows


def _rate(hit: int, total: int) -> float:
    return round(hit / total, 4) if total else 0.0


def evaluate_routing(rows: Sequence[dict]) -> dict:
    """按 `query_type` 分组算命中率 / 误入率 + 总体准确率与混淆矩阵。"""
    by_type: Dict[str, List[dict]] = {}
    for r in rows:
        by_type.setdefault(r["query_type"], []).append(r)

    single = by_type.get(SINGLE_HOP, [])
    multi = by_type.get(MULTI_HOP, [])
    complex_ = by_type.get(COMPLEX, [])

    single_misroute = sum(1 for r in single if r["got"] == DEEP_AGENT)
    multi_hit = sum(1 for r in multi if r["got"] == DEEP_AGENT)
    complex_hit = sum(1 for r in complex_ if r["got"] == DEEP_AGENT)
    correct = sum(1 for r in rows if r["got"] == r["expected"])

    confusion = Counter(f"{r['expected']}→{r['got']}" for r in rows)
    metrics = {
        "samples": len(rows),
        "route_accuracy": _rate(correct, len(rows)),
        "single_hop_misroute_rate": _rate(single_misroute, len(single)),
        "multi_hop_hit_rate": _rate(multi_hit, len(multi)),
        "complex_hit_rate": _rate(complex_hit, len(complex_)),
        "per_type": {
            qt: {
                "samples": len(rs),
                "deep_agent": sum(1 for r in rs if r["got"] == DEEP_AGENT),
                "correct": sum(1 for r in rs if r["got"] == r["expected"]),
            }
            for qt, rs in sorted(by_type.items())
        },
        "got_distribution": dict(Counter(r["got"] for r in rows).most_common()),
        "confusion_top": dict(confusion.most_common(12)),
    }
    return metrics


def check_gates(metrics: dict, *, misroute_max: float = 0.05,
                hit_min: float = 0.80) -> dict:
    """对照 E5 验收线给出逐项判定（`None` = 该组样本为空，不判定）。"""
    misroute = metrics["single_hop_misroute_rate"]
    multi = metrics["multi_hop_hit_rate"]
    complex_ = metrics["complex_hit_rate"]
    gates = {
        "single_hop_misroute<5%": {
            "value": misroute,
            "passed": None if not metrics["per_type"].get(SINGLE_HOP) else misroute < misroute_max,
        },
        "multi_hop_hit>=80%": {
            "value": multi,
            "passed": None if not metrics["per_type"].get(MULTI_HOP) else multi >= hit_min,
        },
        "complex_hit>=80%": {
            "value": complex_,
            "passed": None if not metrics["per_type"].get(COMPLEX) else complex_ >= hit_min,
        },
    }
    graded = [g["passed"] for g in gates.values() if g["passed"] is not None]
    gates["all_passed"] = all(graded) if graded else None
    return gates
