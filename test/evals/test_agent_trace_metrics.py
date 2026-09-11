"""evals.metrics.agent_trace — Agent 轨迹指标离线测试（E2-3 / E1-4）。"""

from __future__ import annotations

from evals.metrics.agent_trace import (
    aggregate_traces,
    render_report,
    tool_selection,
    tool_sequence,
    trace_summary,
)


def _trace(plan_tools, step_specs, verdict=None, rounds=1):
    return {
        "plan": [{"id": i + 1, "objective": f"o{i}", "tool": t, "query": f"q{i}"}
                 for i, t in enumerate(plan_tools)],
        "steps": [{"step_id": i + 1, "tool": t, "query": q, "status": st,
                   "records_count": n}
                  for i, (t, q, st, n) in enumerate(step_specs)],
        "reflection": {"verdict": verdict} if verdict else None,
        "rounds": rounds,
    }


def test_trace_summary_basic():
    t = _trace(["kb_search_tool"], [("kb_search_tool", "q", "ok", 3)])
    s = trace_summary(t)
    assert s["plan_steps"] == 1 and s["step_count"] == 1
    assert s["tool_set"] == ["kb_search_tool"]
    assert s["status"] == {"ok": 1} and s["error_steps"] == 0
    assert s["duplicate_calls"] == 0
    assert "tool_selection" not in s  # 未给 expected_tools


def test_duplicate_and_status_counting():
    t = _trace(
        ["kb_search_tool", "price_query"],
        [("kb_search_tool", "q", "ok", 1), ("kb_search_tool", "q", "empty", 0),
         ("price_query", "p", "error", 0)],
    )
    s = trace_summary(t)
    assert s["duplicate_calls"] == 1          # (kb_search_tool, q) 重复
    assert s["empty_steps"] == 1 and s["error_steps"] == 1


def test_tool_selection_metrics():
    t = _trace(["kb_search_tool"], [("kb_search_tool", "q", "ok", 1)])
    sel = tool_selection(t, ["kb_search_tool", "price_query"])
    assert sel["precision"] == 1.0 and sel["recall"] == 0.5
    assert sel["full_coverage"] is False
    sel2 = tool_selection(t, ["kb_search_tool"])
    assert sel2["f1"] == 1.0 and sel2["full_coverage"] is True


def test_trace_summary_with_expected_tools():
    t = _trace(["kb_search_tool"], [("kb_search_tool", "q", "ok", 1)])
    s = trace_summary(t, expected_tools=["kb_search_tool", "price_query"])
    assert s["tool_selection"]["recall"] == 0.5


def test_aggregate_traces():
    rows = [
        {"question": "a", "latency_ms": 100, "expected_tools": ["kb_search_tool"],
         "trace": _trace(["kb_search_tool"], [("kb_search_tool", "q", "ok", 1)])},
        {"question": "b", "latency_ms": 300,
         "expected_tools": ["kb_search_tool", "price_query"], "trace": _trace(
             ["kb_search_tool", "price_query"],
             [("kb_search_tool", "q", "ok", 1), ("price_query", "p", "empty", 0)],
             verdict="revised")},
    ]
    s = aggregate_traces(rows)
    assert s["n"] == 2
    assert s["plan_steps"]["mean"] == 1.5 and s["plan_steps"]["multi_step_rate"] == 0.5
    assert s["tool_usage"]["kb_search_tool"] == 2
    assert s["step_status"] == {"ok": 2, "empty": 1}
    assert s["reflection_verdict"] == {"revised": 1}
    assert s["samples_with_duplicate"] == 0
    assert s["tool_selection"]["n_gold"] == 2
    assert s["tool_selection"]["full_coverage_rate"] == 1.0


def test_aggregate_empty():
    assert aggregate_traces([]) == {"n": 0}


def test_render_report_smoke():
    rows = [{"question": "a", "latency_ms": 100,
             "trace": _trace(["kb_search_tool"], [("kb_search_tool", "q", "ok", 1)])}]
    text = render_report(aggregate_traces(rows))
    assert "Agent 轨迹评测" in text and "规划步数" in text
    assert "未提供 expected_tools" in text   # 无 gold 时明确标注


# ── E1-4 序列级指标 ─────────────────────────────────────────


def test_tool_sequence_ordered_subsequence():
    # 期望序列按序出现在轨迹中（允许多余步骤）
    t = _trace(["price_query", "kb_search_tool", "kb_search_tool"],
               [("price_query", "p", "ok", 1), ("kb_search_tool", "q", "ok", 1),
                ("kb_search_tool", "q2", "ok", 1)])
    s = tool_sequence(t, ["price_query", "kb_search_tool"])
    assert s["ordered_coverage"] is True
    assert s["exact_sequence"] is False          # 多了第二步 kb


def test_tool_sequence_exact_match():
    t = _trace(["kb_search_tool"], [("kb_search_tool", "q", "ok", 1)])
    s = tool_sequence(t, ["kb_search_tool"])
    assert s["ordered_coverage"] is True and s["exact_sequence"] is True


def test_tool_sequence_wrong_order_fails():
    # 顺序反了：期望 price→kb，轨迹是 kb→price
    t = _trace(["kb_search_tool", "price_query"],
               [("kb_search_tool", "q", "ok", 1), ("price_query", "p", "ok", 1)])
    s = tool_sequence(t, ["price_query", "kb_search_tool"])
    assert s["ordered_coverage"] is False and s["exact_sequence"] is False


def test_tool_sequence_missing_tool_fails():
    t = _trace(["kb_search_tool"], [("kb_search_tool", "q", "ok", 1)])
    s = tool_sequence(t, ["price_query", "kb_search_tool"])
    assert s["ordered_coverage"] is False


def test_trace_summary_with_expected_sequence():
    t = _trace(["price_query", "kb_search_tool"],
               [("price_query", "p", "ok", 1), ("kb_search_tool", "q", "ok", 1)])
    s = trace_summary(t, expected_sequence=["price_query", "kb_search_tool"])
    assert s["tool_sequence"]["ordered_coverage"] is True
    assert "tool_sequence" not in trace_summary(t)  # 未给序列时不出现


def test_aggregate_traces_tool_sequence():
    rows = [
        {"question": "a", "expected_sequence": ["price_query", "kb_search_tool"],
         "trace": _trace(["price_query", "kb_search_tool"],
                         [("price_query", "p", "ok", 1), ("kb_search_tool", "q", "ok", 1)])},
        {"question": "b", "expected_sequence": ["kb_search_tool"],
         "trace": _trace(["price_query"], [("price_query", "p", "ok", 1)])},  # 覆盖失败
    ]
    s = aggregate_traces(rows)
    tq = s["tool_sequence"]
    assert tq["n_gold"] == 2
    assert tq["ordered_coverage_rate"] == 0.5
    assert tq["exact_sequence_rate"] == 0.5


def test_render_report_sequence_section():
    rows = [{"question": "a", "expected_sequence": ["kb_search_tool"],
             "trace": _trace(["kb_search_tool"], [("kb_search_tool", "q", "ok", 1)])}]
    text = render_report(aggregate_traces(rows))
    assert "工具序列" in text and "E1-4 金标真值" in text
