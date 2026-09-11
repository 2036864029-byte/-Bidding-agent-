"""Agent 轨迹评测跑批（E2-3）— 真实 deep_agent 调用在此层。

对样本逐条跑 `run_deep_agent`（真实规划/执行/反思），收集 `data.trace`，
用 `evals.metrics.agent_trace` 汇总。并发（`workers`，主为 I/O）。

`expected_tools` / `expected_sequence`（gold，标注真值或弱标注）来源三层，人工只在最后一层：
  ① 样本自带（`expected_tools` / `expected_sequence`）；
  ② `derive_expected=True` 时按关键词规则派生 `expected_tools`（零人工，近似值）；
  ③ 人工标注（`eval/agent_golden_set.json`，E1-4 金标真值，含有序序列）。
不带 gold 也能得到全部客观轨迹指标（步数/工具频次/状态/反思/延迟）。

并发注意：deep_agent 的工具链会调 SiliconFlow reranker，`workers` 过大会触发 429
降级（见 S5 holdout 记录）；轨迹结构指标对重排质量不敏感，默认 workers=3。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from evals.metrics.agent_trace import aggregate_traces, render_report, trace_summary

__all__ = ["derive_expected_tools", "load_agent_golden", "run_agent_trace"]

KB_TOOL = "kb_search_tool"
PRICE_TOOL = "price_query"

# 弱规则：出现价格/中标类词才期望 price_query（近似，精确值需人工标注）
_PRICE_HINTS = (
    "价格", "报价", "中标价", "中标金额", "预算", "成交", "采购金额",
    "多少钱", "单价", "总价", "费用",
)


def load_agent_golden(path: Path | str) -> list[dict]:
    """装载 E1-4 agent 金标（eval/agent_golden_set.json）→ 评测样本列表。

    每条转成 {question, query_type, expected_tools, expected_sequence}；金标里
    `expected_tools` 即为 `expected_sequence` 的集合版，评测时优先于规则派生。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    return [
        {
            "question": it["q"],
            "query_type": it.get("query_type", ""),
            "expected_tools": it["expected_tools"],
            "expected_sequence": it["expected_sequence"],
        }
        for it in items
    ]


def derive_expected_tools(question: str, query_type: str = "") -> list[str]:
    """规则派生期望工具集合（零人工，弱标注；仅作近似）。"""
    tools = [KB_TOOL]
    if any(h in question for h in _PRICE_HINTS):
        tools.append(PRICE_TOOL)
    return tools


def _run_one(case: dict) -> dict:
    """单题跑 deep_agent，收 data.trace（每个 worker 独立建 llm/tools）。"""
    from agent.nodes.deep_agent import run_deep_agent
    from core.config.settings import Settings
    from core.llm.registry import create_llm_for_slot
    from public_kb.tools import kb_search_as_tool
    from price_engine.tools import price_query_as_tool

    llm = create_llm_for_slot("deep_agent", Settings())
    tools = [kb_search_as_tool(), price_query_as_tool()]
    t0 = time.monotonic()
    try:
        result = run_deep_agent(case["question"], llm=llm, tools=tools)
        return {
            "question": case["question"],
            "query_type": case.get("query_type", ""),
            "trace": result.get("trace") or {},
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "expected_tools": case.get("expected_tools"),
            "expected_sequence": case.get("expected_sequence"),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001  单题失败降级
        return {
            "question": case["question"],
            "query_type": case.get("query_type", ""),
            "trace": {}, "latency_ms": None,
            "expected_tools": case.get("expected_tools"),
            "expected_sequence": case.get("expected_sequence"),
            "error": f"{type(e).__name__}: {e}",
        }


def run_agent_trace(
    cases: Sequence[dict],
    *,
    workers: int = 3,
    derive_expected: bool = True,
    out_dir: Path,
) -> dict:
    """跑 deep_agent → 汇总轨迹指标，产物写 out_dir。

    Args:
        cases: [{question, query_type?, expected_tools?, expected_sequence?}]。
        workers: 并发（deep_agent 工具链打 SiliconFlow，过大触发 429）。
        derive_expected: 样本无 expected_tools 时按规则派生（弱标注）。
        out_dir: 产物目录（per_sample.jsonl / summary.json / report.md）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        rows = list(pool.map(_run_one, cases))
    elapsed = round(time.monotonic() - t0, 1)

    for r, c in zip(rows, cases):
        if not r.get("expected_tools") and derive_expected:
            r["expected_tools"] = derive_expected_tools(
                c["question"], c.get("query_type", ""))

    summary = aggregate_traces(rows)
    summary["elapsed_s"] = elapsed
    summary["errors"] = sum(1 for r in rows if r["error"])

    per_sample = [
        {"question": r["question"], "error": r["error"], "latency_ms": r["latency_ms"],
         "summary": trace_summary(r["trace"], expected_tools=r["expected_tools"],
                                  expected_sequence=r["expected_sequence"])}
        for r in rows
    ]
    (out_dir / "per_sample.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in per_sample) + "\n",
        encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(summary), encoding="utf-8")
    return summary
