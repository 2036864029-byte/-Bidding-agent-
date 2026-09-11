"""evals.metrics.system — 系统指标解析/汇总离线测试（E2-4）。"""

from __future__ import annotations

from evals.metrics.system import (
    _hist_quantile,
    parse_prometheus,
    render_report,
    summarize_system,
)

_PROM = """# HELP agent_branch_latency_seconds
# TYPE agent_branch_latency_seconds histogram
agent_branch_latency_seconds_bucket{branch="knowledge_qa",le="0.1"} 8.0
agent_branch_latency_seconds_bucket{branch="knowledge_qa",le="0.5"} 10.0
agent_branch_latency_seconds_bucket{branch="knowledge_qa",le="+Inf"} 10.0
agent_branch_latency_seconds_count{branch="knowledge_qa"} 10.0
agent_branch_latency_seconds_sum{branch="knowledge_qa"} 2.0
llm_latency_seconds_bucket{slot="kb_generation",model="deepseek-chat",le="1.0"} 4.0
llm_latency_seconds_bucket{slot="kb_generation",model="deepseek-chat",le="+Inf"} 4.0
llm_latency_seconds_count{slot="kb_generation",model="deepseek-chat"} 4.0
llm_latency_seconds_sum{slot="kb_generation",model="deepseek-chat"} 6.0
llm_tokens_total{slot="kb_generation",model="deepseek-chat",kind="prompt"} 1200.0
llm_tokens_total{slot="kb_generation",model="deepseek-chat",kind="completion"} 300.0
llm_errors_total{slot="kb_generation",model="deepseek-chat"} 1.0
agent_recall_fallback_total{layer="full_scan"} 2.0
agent_guardrail_fails_total{branch="knowledge_qa"} 3.0
"""


def test_parse_prometheus_basic():
    parsed = parse_prometheus(_PROM)
    assert "llm_tokens_total" in parsed
    assert parsed["llm_tokens_total"][0]["labels"]["kind"] == "prompt"
    assert parsed["agent_recall_fallback_total"][0]["value"] == 2.0


def test_parse_ignores_comments_and_garbage():
    parsed = parse_prometheus("# comment\nnot a metric line\njunk without value\n")
    assert parsed == {}


def test_parse_handles_empty_labels():
    parsed = parse_prometheus("foo{} 1\n")
    assert parsed["foo"][0] == {"labels": {}, "value": 1.0}


def test_summarize_latency_tokens_errors():
    s = summarize_system(parse_prometheus(_PROM))
    bl = s["branch_latency"]["branch=knowledge_qa"]
    assert bl["count"] == 10 and bl["mean_ms"] == 200.0
    assert bl["p50_ms"] is not None and bl["p95_ms"] is not None

    llm = s["llm_latency"]["model=deepseek-chat|slot=kb_generation"]
    assert llm["count"] == 4 and llm["mean_ms"] == 1500.0

    assert s["llm_tokens"]["model=deepseek-chat|slot=kb_generation"] == {
        "prompt": 1200, "completion": 300}
    assert s["llm_errors"]["model=deepseek-chat|slot=kb_generation"] == 1.0
    assert s["recall_fallbacks"] == {"layer=full_scan": 2.0}
    assert s["guardrail_fails"] == {"branch=knowledge_qa": 3.0}


def test_summarize_empty():
    s = summarize_system({})
    assert s["branch_latency"] == {} and s["llm_tokens"] == {}


def test_hist_quantile_inf_bucket_not_infinite():
    # 目标落在 +Inf 桶 → 报上一有限上界，不得返回 inf
    assert _hist_quantile([(1.0, 0.0), (float("inf"), 4.0)], 4.0, 0.95) == 1.0
    # 正常桶内插值
    q = _hist_quantile([(1.0, 2.0), (2.0, 4.0), (float("inf"), 4.0)], 4.0, 0.5)
    assert q == 1.0


def test_render_report_smoke():
    text = render_report(summarize_system(parse_prometheus(_PROM)))
    assert "系统指标" in text
    assert "LLM 调用耗时" in text
    assert "1200" in text            # token
    assert "knowledge_qa" in text
