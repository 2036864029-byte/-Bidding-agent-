"""evals.gates — 门禁判定离线测试（E3）。"""

from __future__ import annotations

from evals.gates import (
    all_passed,
    check_generation,
    check_retrieval,
    check_system,
    render_gates,
)


def _retrieval(l1_mhr, l1_rec, l1_mrr, l0_mrr):
    return {
        "l0": {"multi": {"mrr": l0_mrr}},
        "l1": {"multi": {"multi_hop_recall": l1_mhr, "recall": l1_rec, "mrr": l1_mrr}},
    }


def test_retrieval_gate_pass():
    # 校准值：holdout 实测 0.6000 / 0.7857 / L1 0.8413 ≥ L0 0.8390
    r = check_retrieval(_retrieval(0.6000, 0.7857, 0.8413, 0.8390))
    assert all_passed(r)


def test_retrieval_gate_fail_on_recall():
    r = check_retrieval(_retrieval(0.60, 0.70, 0.85, 0.80))
    assert not all_passed(r)
    assert any("recall" in x["name"] and not x["passed"] for x in r)


def test_retrieval_mrr_nondegrade():
    assert all_passed(check_retrieval(_retrieval(0.6, 0.78, 0.85, 0.85)))
    assert not all_passed(check_retrieval(_retrieval(0.6, 0.78, 0.80, 0.85)))


def test_generation_gate():
    ok = {"paired_delta": {"faithfulness": {"mean": 0.055},
                           "answer_relevancy": {"mean": 0.032}}}
    assert all_passed(check_generation(ok))
    bad = {"paired_delta": {"faithfulness": {"mean": -0.10}}}
    assert not all_passed(check_generation(bad))


def test_generation_gate_nan_fails():
    s = {"paired_delta": {"faithfulness": {"mean": float("nan")}}}
    assert not all_passed(check_generation(s))


def test_system_gate():
    ok = {"n": 100, "errors": 1,
          "branch_latency": {"branch=knowledge_qa": {"mean_ms": 7600}}}
    assert all_passed(check_system(ok))
    bad = {"n": 100, "errors": 10,
           "branch_latency": {"branch=knowledge_qa": {"mean_ms": 20000}}}
    assert not all_passed(check_system(bad))


def test_render_gates_smoke():
    text = render_gates(check_retrieval(_retrieval(0.6000, 0.7857, 0.8413, 0.8390)))
    assert "评测门禁" in text and "PASS" in text
