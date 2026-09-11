"""evals.metrics.generation — 生成层配对指标离线测试（S1/E2-2）。"""

from __future__ import annotations

from evals.metrics.generation import (
    build_ragas_rows,
    layer_valid,
    render_report,
    summarize,
)


def _s(sid: str, q: str, qt: str = "multi_hop", **kwargs) -> dict:
    return {
        "sample_id": sid,
        "question": q,
        "reference": f"ref-{q}",
        "query_type": qt,
        "l0": {"contexts": [f"c0-{q}"], "answer": f"a0-{q}"},
        "l1": {"contexts": [f"c1-{q}"], "answer": f"a1-{q}"},
        **kwargs,
    }


def test_layer_valid_filters_failed_rows():
    samples = [
        _s("00", "q0"),
        _s("01", "q1", l0={"contexts": [], "answer": ""}),
        _s("02", "q2", l1=None),
    ]
    assert [s["sample_id"] for s in layer_valid(samples, "l0")] == ["00", "02"]
    assert [s["sample_id"] for s in layer_valid(samples, "l1")] == ["00", "01"]


def test_build_ragas_rows_columns():
    samples = [_s("00", "q0", reference="R0")]
    rows = build_ragas_rows(samples, "l0")
    assert rows == [{
        "sample_id": "00",
        "user_input": "q0",
        "response": "a0-q0",
        "retrieved_contexts": ["c0-q0"],
        "reference": "R0",
    }]


def test_summarize_means_and_paired_deltas():
    samples = [
        _s("00", "q0", "multi_hop"),
        _s("01", "q1", "single_hop"),
        _s("02", "q2", "single_hop", l1=None),   # 无 L1 → 不入配对
    ]
    rows_l0 = [
        {"user_input": "q0", "faithfulness": 0.8, "answer_relevancy": 0.5,
         "context_precision": 0.6},
        {"user_input": "q1", "faithfulness": 1.0, "answer_relevancy": 0.5,
         "context_precision": 0.6},
        {"user_input": "q2", "faithfulness": 0.9, "answer_relevancy": 0.5,
         "context_precision": 0.6},
    ]
    rows_l1 = [
        {"user_input": "q0", "faithfulness": 0.6, "answer_relevancy": 0.4,
         "context_precision": 0.3},
        {"user_input": "q1", "faithfulness": 1.0, "answer_relevancy": 0.5,
         "context_precision": 0.6},
    ]
    stats = {"empty_retrieval": 0, "gen_error": 0, "workers": 1, "time_s": 1.0}
    s = summarize(samples, rows_l0, rows_l1, stats)

    assert s["n_total"] == 3
    assert s["n_valid_l0"] == 3 and s["n_valid_l1"] == 2
    assert s["n_paired"] == 2
    # L0 faithfulness 均值 (0.8+1.0+0.9)/3；L1 只用两行
    assert s["per_layer"]["l0"]["mean"]["faithfulness"] == 0.9
    assert s["per_layer"]["l1"]["mean"]["faithfulness"] == 0.8
    # 配对 delta：q0 -0.2、q1 0.0 → mean -0.1；变差 1 / 不变 1
    d = s["paired_delta"]["faithfulness"]
    assert d["pairs"] == 2 and d["worsened"] == 1 and d["unchanged"] == 1
    assert d["improved"] == 0 and round(d["mean"], 4) == -0.1
    # by_type：multi_hop 配对 q0，single_hop 配对 q1
    assert s["by_type"]["multi_hop"]["n"] == 1
    assert s["by_type"]["single_hop"]["n"] == 1
    assert round(s["by_type"]["multi_hop"]["per_layer"]["l0"]["faithfulness"], 4) == 0.8
    assert round(s["by_type"]["single_hop"]["per_layer"]["l1"]["faithfulness"], 4) == 1.0


def test_summarize_nan_failure_rows_excluded_from_delta():
    samples = [_s("00", "q0"), _s("01", "q1")]
    rows_l0 = [
        {"user_input": "q0", "faithfulness": float("nan"),
         "answer_relevancy": 0.5, "context_precision": 0.6},
        {"user_input": "q1", "faithfulness": 0.8,
         "answer_relevancy": 0.5, "context_precision": 0.6},
    ]
    rows_l1 = [
        {"user_input": "q0", "faithfulness": 0.7,
         "answer_relevancy": 0.5, "context_precision": 0.6},
        {"user_input": "q1", "faithfulness": 0.8,
         "answer_relevancy": 0.5, "context_precision": 0.6},
    ]
    s = summarize(samples, rows_l0, rows_l1, {})
    d = s["paired_delta"]["faithfulness"]
    # q0 的 L0 判定失败（NaN）→ 该题不参与配对
    assert d["pairs"] == 1 and d["worsened"] == 0 and d["unchanged"] == 1
    # 分层均值仍含 NaN 行（只去 NaN 后取均值）：L0 = 0.8
    assert s["per_layer"]["l0"]["mean"]["faithfulness"] == 0.8


def test_render_report_smoke():
    samples = [_s("00", "q0")]
    rows_l0 = [{"user_input": "q0", "faithfulness": 0.9,
                "answer_relevancy": 0.5, "context_precision": 0.6}]
    rows_l1 = [{"user_input": "q0", "faithfulness": 0.85,
                "answer_relevancy": 0.5, "context_precision": 0.6}]
    s = summarize(samples, rows_l0, rows_l1, {"workers": 1})
    text = render_report(s)
    assert "S1 Ragas 生成层评测" in text
    assert "忠实度 faithfulness" in text
    assert "-0.0500" in text  # 0.85 - 0.90


def test_summarize_detects_present_metrics_subset():
    """增量补跑：只判了 context_recall 时，summary 只含该指标列。"""
    samples = [_s("00", "q0"), _s("01", "q1")]
    rows_l0 = [
        {"user_input": "q0", "context_recall": 0.8},
        {"user_input": "q1", "context_recall": 1.0},
    ]
    rows_l1 = [
        {"user_input": "q0", "context_recall": 0.9},
        {"user_input": "q1", "context_recall": 1.0},
    ]
    s = summarize(samples, rows_l0, rows_l1, {})
    assert s["metrics"] == ["context_recall"]
    d = s["paired_delta"]["context_recall"]
    assert d["pairs"] == 2 and d["improved"] == 1 and d["unchanged"] == 1


def test_merge_rows_incremental():
    """首跑 3 指标 + 补跑 context_recall → 合并后两列都在（evals.runners.generation）。"""
    from evals.runners.generation import _merge_rows

    existing = [
        {"user_input": "q0", "sample_id": "00", "faithfulness": 0.9,
         "answer_relevancy": 0.5, "context_precision": 0.6},
        {"user_input": "q1", "sample_id": "01", "faithfulness": 1.0,
         "answer_relevancy": 0.5, "context_precision": 0.6},
    ]
    new = [
        {"user_input": "q0", "sample_id": "00", "context_recall": 0.8},
        {"user_input": "q2", "sample_id": "02", "context_recall": 0.7},
    ]
    merged = _merge_rows(existing, new)
    by_q = {r["user_input"]: r for r in merged}
    assert len(merged) == 3
    assert by_q["q0"]["faithfulness"] == 0.9 and by_q["q0"]["context_recall"] == 0.8
    assert by_q["q1"]["faithfulness"] == 1.0 and "context_recall" not in by_q["q1"]
    assert by_q["q2"]["context_recall"] == 0.7
