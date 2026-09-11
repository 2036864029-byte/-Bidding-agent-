"""评测门禁（E3）— 基线阈值 + 不降判定。**纯函数**，读 E2 各层产物判定。

三层门禁（阈值来自 E2 实测校准，留余量；改动须同步 §6 评测门禁表）：

  检索层  holdout multi：双段全中 ≥0.60、recall ≥0.78、MRR ≥ L0（不降）
  生成层  dev_100 L1 vs L0：faithfulness/answer_relevancy/context_recall 的 Δ ≥ −0.02
          （S1 判定"扩窗不稀释"；余量防抖）
  系统层  LLM 错误率 ≤5%、knowledge_qa 分支均值 ≤15s（防链路劣化）

用法：
    from evals.gates import check_retrieval, check_generation, check_system, render_gates
    results = check_retrieval(gate_json)          # 每项 {name, value, target, kind, passed}
    all(r["passed"] for r in results)             # 门禁总判定
"""

from __future__ import annotations

from typing import Dict, List, Sequence

# 阈值（校准值见注释；绝对口径留余量，相对口径用"不降"）
RETRIEVAL_GATES = {           # multi 口径（holdout 实测：0.6000 / 0.7857 / 0.8413）
    "multi_hop_recall": 0.60,
    "recall": 0.78,
}
GENERATION_TOL = -0.02        # 生成层 Δ 下限（不稀释；S1 实测三项均为正）
SYSTEM_MAX_ERR_RATE = 0.05    # LLM 错误率上限
SYSTEM_MAX_BRANCH_MS = 15000  # knowledge_qa 分支均值上限（实测 7.6s/题）


def _res(name: str, value, target, kind: str, passed: bool) -> dict:
    return {"name": name, "value": value, "target": target, "kind": kind,
            "passed": bool(passed)}


def check_retrieval(gate: dict) -> List[dict]:
    """gate = `retrieval_layer_gate_*.json` 内容。"""
    l0m = (gate.get("l0") or {}).get("multi") or {}
    l1m = (gate.get("l1") or {}).get("multi") or {}
    out = []
    for metric, thr in RETRIEVAL_GATES.items():
        v = l1m.get(metric)
        out.append(_res(f"检索 · multi {metric}", v, thr, "abs",
                        v is not None and v >= thr))
    v1, v0 = l1m.get("mrr"), l0m.get("mrr")
    out.append(_res("检索 · multi MRR 不降", v1, v0, "nondegrade",
                    v1 is not None and v0 is not None and v1 >= v0))
    return out


def check_generation(summary: dict, tol: float = GENERATION_TOL) -> List[dict]:
    """summary = Ragas `summary.json`（paired_delta 逐指标）。"""
    out = []
    for metric, d in (summary.get("paired_delta") or {}).items():
        mean = d.get("mean")
        out.append(_res(f"生成 · Δ{metric} ≥ {tol}", mean, tol, "delta",
                        mean is not None and mean == mean and mean >= tol))
    return out


def check_system(summary: dict, max_err_rate: float = SYSTEM_MAX_ERR_RATE,
                 max_branch_ms: float = SYSTEM_MAX_BRANCH_MS) -> List[dict]:
    """summary = 系统指标 `summary.json`。"""
    n = summary.get("n") or 0
    errs = summary.get("errors") or 0
    rate = (errs / n) if n else 0.0
    out = [_res(f"系统 · LLM 错误率 ≤{max_err_rate:.0%}", round(rate, 4),
                max_err_rate, "abs", rate <= max_err_rate)]
    kq = (summary.get("branch_latency") or {}).get("branch=knowledge_qa") or {}
    mean = kq.get("mean_ms")
    out.append(_res(f"系统 · knowledge_qa 均值 ≤{max_branch_ms}ms", mean,
                    max_branch_ms, "abs", mean is not None and mean <= max_branch_ms))
    return out


def all_passed(results: Sequence[dict]) -> bool:
    return all(r["passed"] for r in results)


def render_gates(results: Sequence[dict], title: str = "评测门禁") -> str:
    lines = [f"# {title}", "", "| 门禁 | 实测 | 基准 | 判定 |", "|---|---|---|---|"]
    for r in results:
        v = r["value"]
        vstr = f"{v:.4f}" if isinstance(v, (int, float)) else str(v)
        tstr = f"{r['target']:.4f}" if isinstance(r["target"], (int, float)) else str(r["target"])
        lines.append(f"| {r['name']} | {vstr} | {tstr} | {'✅ PASS' if r['passed'] else '❌ FAIL'} |")
    lines += ["", f"**总判定: {'✅ 全部门禁通过' if all_passed(results) else '❌ 存在未达标'}**", ""]
    return "\n".join(lines)


__all__ = [
    "RETRIEVAL_GATES", "GENERATION_TOL", "SYSTEM_MAX_ERR_RATE", "SYSTEM_MAX_BRANCH_MS",
    "check_retrieval", "check_generation", "check_system", "all_passed", "render_gates",
]
