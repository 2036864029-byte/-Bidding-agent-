"""evals.metrics.router — 路由层指标离线测试（E5-2）。"""

from __future__ import annotations

from evals.datasets import EvalSample
from evals.metrics.router import (
    build_rows,
    check_gates,
    evaluate_routing,
    expected_route_of,
)


def _s(q: str, qt: str, expected: str = "") -> EvalSample:
    return EvalSample(question=q, query_type=qt, expected_route=expected)


# ── 期望路由推导 ─────────────────────────────────────────────

def test_expected_route_by_query_type():
    assert expected_route_of(_s("a", "single_hop")) == "knowledge_qa"
    assert expected_route_of(_s("b", "multi_hop")) == "deep_agent"
    assert expected_route_of(_s("c", "complex")) == "deep_agent"


def test_explicit_expected_route_wins():
    assert expected_route_of(_s("a", "single_hop", expected="doc_qa")) == "doc_qa"


def test_build_rows_marks_missing_as_error():
    samples = [_s("a", "single_hop"), _s("b", "multi_hop")]
    rows = build_rows(samples, {"a": "knowledge_qa"})

    assert rows[0]["got"] == "knowledge_qa"
    assert rows[1]["got"] == "error"
    assert rows[1]["expected"] == "deep_agent"


# ── 指标口径 ─────────────────────────────────────────────────

def _rows() -> list[dict]:
    return [
        {"question": "s1", "query_type": "single_hop", "expected": "knowledge_qa",
         "got": "knowledge_qa"},
        {"question": "s2", "query_type": "single_hop", "expected": "knowledge_qa",
         "got": "deep_agent"},
        {"question": "m1", "query_type": "multi_hop", "expected": "deep_agent",
         "got": "deep_agent"},
        {"question": "m2", "query_type": "multi_hop", "expected": "deep_agent",
         "got": "knowledge_qa"},
        {"question": "c1", "query_type": "complex", "expected": "deep_agent",
         "got": "deep_agent"},
    ]


def test_evaluate_routing_rates():
    m = evaluate_routing(_rows())

    assert m["samples"] == 5
    assert m["single_hop_misroute_rate"] == 0.5      # 1/2
    assert m["multi_hop_hit_rate"] == 0.5            # 1/2
    assert m["complex_hit_rate"] == 1.0              # 1/1
    assert m["route_accuracy"] == 0.6                # 3/5
    assert m["per_type"]["multi_hop"] == {"samples": 2, "deep_agent": 1, "correct": 1}
    assert m["got_distribution"]["knowledge_qa"] == 2


def test_check_gates_flags_failures():
    gates = check_gates(evaluate_routing(_rows()))

    assert gates["single_hop_misroute<5%"]["passed"] is False
    assert gates["multi_hop_hit>=80%"]["passed"] is False
    assert gates["complex_hit>=80%"]["passed"] is True
    assert gates["all_passed"] is False


def test_check_gates_skips_empty_groups():
    rows = [{"question": "s", "query_type": "single_hop",
             "expected": "knowledge_qa", "got": "knowledge_qa"}]
    gates = check_gates(evaluate_routing(rows))

    assert gates["single_hop_misroute<5%"]["passed"] is True
    assert gates["multi_hop_hit>=80%"]["passed"] is None   # 无样本 → 不判定
    assert gates["complex_hit>=80%"]["passed"] is None
    assert gates["all_passed"] is True
