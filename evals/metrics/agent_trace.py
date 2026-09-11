"""Agent 轨迹指标（E2-3）— 从 `data.trace` 直接算，**纯确定性、零 LLM**。

数据契约（`trace` = `core.contracts.trace.trace_payload` 输出，见 deep_agent 节点）：
  plan:   [{id, objective, tool, query, depends_on}]
  steps:  [{step_id, tool, query, status(ok/empty/error), observation_head,
            records_count, latency_ms}]
  reflection: {verdict(adequate/revised/gap), notes, revised_answer} | None
  rounds: int

gold（可选，**弱标注**）：样本可带 `expected_tools`——**期望调用的工具集合**（非精确
序列）。这是唯一需要标注的字段，且可由规则派生（见 `evals.runners.agent_trace`），
约 30 条即可，**不是前置门槛**：不带 gold 也能得到全部客观轨迹指标。
"""

from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Dict, List, Optional, Sequence


# ── 单条轨迹 ────────────────────────────────────────────────


def tool_selection(trace: dict, expected_tools: Sequence[str]) -> dict:
    """集合级工具选择指标（precision/recall/F1/是否全覆盖）。"""
    exp = {t for t in expected_tools if t}
    got = {s.get("tool") for s in (trace.get("steps") or []) if s.get("tool")}
    tp = len(exp & got)
    precision = tp / len(got) if got else (1.0 if not exp else 0.0)
    recall = tp / len(exp) if exp else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "expected": sorted(exp),
        "got": sorted(got),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "full_coverage": exp <= got,
    }


def trace_summary(trace: dict, *, expected_tools: Optional[Sequence[str]] = None) -> dict:
    """单条轨迹的客观统计（零 gold）；带 expected_tools 时附工具选择。"""
    trace = trace or {}
    plan = trace.get("plan") or []
    steps = trace.get("steps") or []
    reflection = trace.get("reflection") or {}

    status = Counter(s.get("status", "unknown") for s in steps)
    tools_used = [s.get("tool") for s in steps if s.get("tool")]
    keys = [(s.get("tool"), s.get("query")) for s in steps]
    lat = [s.get("latency_ms") for s in steps
           if isinstance(s.get("latency_ms"), (int, float))]

    out = {
        "plan_steps": len(plan),
        "step_count": len(steps),
        "tools_used": tools_used,
        "tool_set": sorted(set(tools_used)),
        "status": dict(status),
        "error_steps": status.get("error", 0),
        "empty_steps": status.get("empty", 0),
        "duplicate_calls": len(keys) - len(set(keys)),
        "step_latency_ms_sum": int(sum(lat)) if lat else None,
        "reflection_verdict": reflection.get("verdict"),
        "rounds": trace.get("rounds", 0),
    }
    if expected_tools:
        out["tool_selection"] = tool_selection(trace, expected_tools)
    return out


# ── 批量汇总 ────────────────────────────────────────────────


def _mean(vals: Sequence[float]) -> Optional[float]:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(mean(vals), 4) if vals else None


def aggregate_traces(rows: Sequence[dict]) -> dict:
    """批量汇总。

    Args:
        rows: [{question, trace, latency_ms?, expected_tools?}]——expected_tools 可选。
    """
    summaries = [trace_summary(r.get("trace") or {}, expected_tools=r.get("expected_tools"))
                 for r in rows]
    n = len(summaries)
    if n == 0:
        return {"n": 0}

    plan_steps = [s["plan_steps"] for s in summaries]
    end2end = [r.get("latency_ms") for r in rows]

    tool_usage = Counter()
    status_totals = Counter()
    reflection = Counter()
    for s in summaries:
        tool_usage.update(s["tools_used"])
        status_totals.update(s["status"])
        if s["reflection_verdict"]:
            reflection[s["reflection_verdict"]] += 1

    summary = {
        "n": n,
        "plan_steps": {
            "mean": _mean(plan_steps),
            "min": min(plan_steps),
            "max": max(plan_steps),
            "distribution": dict(sorted(Counter(plan_steps).items())),
            "multi_step_rate": round(sum(1 for x in plan_steps if x >= 2) / n, 4),
        },
        "tool_usage": dict(tool_usage.most_common()),
        "step_status": dict(status_totals),
        "reflection_verdict": dict(reflection),
        "samples_with_duplicate": sum(1 for s in summaries if s["duplicate_calls"] > 0),
        "samples_with_error": sum(1 for s in summaries if s["error_steps"] > 0),
        "end2end_latency_ms_mean": _mean(end2end),
        "step_latency_ms_mean": _mean([s["step_latency_ms_sum"] for s in summaries]),
    }

    # 工具选择（仅对带 expected_tools 的样本）
    golds = [s["tool_selection"] for s in summaries if "tool_selection" in s]
    if golds:
        summary["tool_selection"] = {
            "n_gold": len(golds),
            "precision_mean": _mean([g["precision"] for g in golds]),
            "recall_mean": _mean([g["recall"] for g in golds]),
            "f1_mean": _mean([g["f1"] for g in golds]),
            "full_coverage_rate": round(
                sum(1 for g in golds if g["full_coverage"]) / len(golds), 4),
        }
    return summary


# ── 报告 ────────────────────────────────────────────────────


def render_report(summary: dict) -> str:
    """summary → Markdown 报告。"""
    if not summary.get("n"):
        return "# Agent 轨迹评测\n\n无样本。\n"
    ps = summary["plan_steps"]
    lines = [
        "# Agent 轨迹评测（E2-3，deep_agent）",
        "",
        f"- 样本数: **{summary['n']}**",
        f"- 规划步数: 均值 {ps['mean']}（min {ps['min']} / max {ps['max']}），"
        f"多步（≥2）占比 {ps['multi_step_rate']:.1%}",
        f"- 步数分布: {ps['distribution']}",
        f"- 工具使用频次: {summary['tool_usage']}",
        f"- 每步状态: {summary['step_status']}",
        f"- 反思结论: {summary['reflection_verdict']}",
        f"- 有重复调用的样本: {summary['samples_with_duplicate']} / {summary['n']}",
        f"- 有失败步的样本: {summary['samples_with_error']} / {summary['n']}",
        f"- 端到端耗时均值: {summary['end2end_latency_ms_mean']} ms",
        "",
    ]
    ts = summary.get("tool_selection")
    if ts:
        lines += [
            "## 工具选择（对照 expected_tools，弱标注）",
            "",
            f"- 标注样本: {ts['n_gold']}",
            f"- precision 均值 {ts['precision_mean']} / recall 均值 {ts['recall_mean']} "
            f"/ F1 均值 {ts['f1_mean']}",
            f"- 全覆盖率（期望工具全被调用）: {ts['full_coverage_rate']:.1%}",
            "",
        ]
    else:
        lines += ["（未提供 expected_tools → 仅客观轨迹指标，零人工标注）", ""]
    return "\n".join(lines)


__all__ = ["trace_summary", "tool_selection", "aggregate_traces", "render_report"]
