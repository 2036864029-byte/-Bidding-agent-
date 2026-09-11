"""evals.metrics — 评测指标计算（检索层 / 路由层 / 生成层 / Agent 轨迹层）。"""

from __future__ import annotations

from .agent_trace import (
    aggregate_traces,
    tool_selection,
    tool_sequence,
    trace_summary,
)
from .agent_trace import render_report as render_agent_trace_report
from .generation import (
    GENERATION_METRICS,
    build_ragas_rows,
    layer_valid,
    render_report,
    summarize,
)
from .system import parse_prometheus, summarize_system
from .system import render_report as render_system_report
from .retrieval import (
    RetrievedMap,
    RetrievedTextMap,
    evaluate_retrieval,
    evaluate_retrieval_text,
    hit_rate,
    missing_gold,
    missing_gold_text,
    mrr,
    multi_hop_recall,
    precision_at_k,
    recall_at_k,
    text_hit_rate,
    text_full_recall,
    text_mrr,
    text_multi_hop_recall,
    text_recall,
)
from .router import build_rows, check_gates, evaluate_routing, expected_route_of

__all__ = [
    "RetrievedMap",
    "RetrievedTextMap",
    "hit_rate",
    "recall_at_k",
    "mrr",
    "multi_hop_recall",
    "precision_at_k",
    "evaluate_retrieval",
    "missing_gold",
    "text_hit_rate",
    "text_full_recall",
    "text_recall",
    "text_mrr",
    "text_multi_hop_recall",
    "evaluate_retrieval_text",
    "missing_gold_text",
    "expected_route_of",
    "build_rows",
    "evaluate_routing",
    "check_gates",
    "GENERATION_METRICS",
    "layer_valid",
    "build_ragas_rows",
    "summarize",
    "render_report",
    "trace_summary",
    "tool_selection",
    "tool_sequence",
    "aggregate_traces",
    "render_agent_trace_report",
    "parse_prometheus",
    "summarize_system",
    "render_system_report",
]
