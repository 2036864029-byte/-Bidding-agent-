"""evals.metrics.retrieval — 检索层指标离线测试（E2-1）。"""

from __future__ import annotations

from evals.datasets import EvalSample
from evals.metrics import (
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


def _s(q: str, gold: list[int], qt: str = "single_hop") -> EvalSample:
    return EvalSample(question=q, ground_truth_chunk_ids=gold, query_type=qt)


def test_hit_rate_and_recall():
    samples = [_s("a", [1]), _s("b", [2]), _s("c", [3])]
    retrieved = {"a": [1, 9], "b": [8, 7], "c": [3]}

    assert hit_rate(samples, retrieved, k=5) == 2 / 3
    assert recall_at_k(samples, retrieved, k=5) == 2 / 3


def test_recall_counts_partial_multi_gold():
    samples = [_s("a", [1, 2], qt="multi_hop")]
    retrieved = {"a": [1, 9, 9, 9, 9]}

    assert recall_at_k(samples, retrieved, k=5) == 0.5
    assert multi_hop_recall(samples, retrieved, k=5) == 0.0  # 未全中

    retrieved_full = {"a": [1, 2]}
    assert multi_hop_recall(samples, retrieved_full, k=5) == 1.0


def test_mrr_uses_first_hit_rank():
    samples = [_s("a", [5]), _s("b", [5])]
    retrieved = {"a": [9, 5], "b": [5]}

    # a: 1/2, b: 1/1 → 均值 0.75
    assert mrr(samples, retrieved, k=5) == 0.75


def test_mrr_miss_scores_zero():
    samples = [_s("a", [5])]
    assert mrr(samples, {"a": [1, 2, 3]}, k=5) == 0.0


def test_k_truncation():
    samples = [_s("a", [1])]
    retrieved = {"a": [9, 9, 9, 9, 1]}  # 命中排第 5

    assert hit_rate(samples, retrieved, k=5) == 1.0
    assert hit_rate(samples, retrieved, k=3) == 0.0  # 截断后未命中


def test_precision_uses_actual_returned_count():
    samples = [_s("a", [1, 2])]
    retrieved = {"a": [1, 2]}  # 只返回 2 条且都命中

    assert precision_at_k(samples, retrieved, k=5) == 1.0


def test_empty_retrieval_and_empty_gold():
    samples = [_s("a", [])]
    assert hit_rate(samples, {"a": [1]}, k=5) == 0.0
    assert recall_at_k(samples, {"a": [1]}, k=5) == 0.0

    samples2 = [_s("b", [1])]
    assert hit_rate(samples2, {}, k=5) == 0.0
    assert evaluate_retrieval(samples2, {}, k=5)["precision@5"] == 0.0


def test_evaluate_retrieval_returns_all_metrics():
    samples = [_s("a", [1]), _s("b", [2, 3], qt="multi_hop")]
    retrieved = {"a": [1], "b": [2, 3]}

    out = evaluate_retrieval(samples, retrieved, k=5)

    assert out == {
        "hit_rate@5": 1.0,
        "recall@5": 1.0,
        "mrr@5": 1.0,
        "multi_hop_recall@5": 1.0,
        "precision@5": 1.0,
    }


def test_missing_gold_lists_unhit_samples():
    samples = [_s("a", [1]), _s("b", [2])]
    retrieved = {"a": [1], "b": [9]}

    assert missing_gold(samples, retrieved, k=5) == [("b", [2])]


# ── 正文口径（金标 id 过期时的等价指标）──────────────────────

def _st(q: str, texts: list[str], qt: str = "single_hop") -> EvalSample:
    return EvalSample(question=q, ground_truth_text=texts, query_type=qt)


def test_text_hit_rate_and_recall():
    samples = [_st("a", ["第一段正文"]), _st("b", ["第二段正文"])]
    texts = {"a": ["无关内容", "这里是第一段正文的完整表述"], "b": ["完全无关"]}

    assert text_hit_rate(samples, texts, k=5) == 0.5
    assert text_recall(samples, texts, k=5) == 0.5


def test_text_match_ignores_whitespace():
    samples = [_st("a", ["第十三条 申请单位自收到通知之日起"])]
    texts = {"a": ["第十三条\n申请单位自收到通知之日起"]}

    assert text_hit_rate(samples, texts, k=5) == 1.0


def test_text_reverse_containment_needs_coverage():
    samples = [_st("a", ["一段较长的金标正文内容，用于测试覆盖度守卫"])]
    # 检索块是金标的子串但太短（覆盖度不足）→ 不算命中
    short = {"a": ["一段较长"]}
    assert text_hit_rate(samples, short, k=5) == 0.0
    # 覆盖度 ≥60% → 命中
    long_enough = {"a": ["一段较长的金标正文内容，用于测试"]}
    assert text_hit_rate(samples, long_enough, k=5) == 1.0


def test_text_mrr_and_multi_hop():
    samples = [_st("a", ["金标一", "金标二"], qt="multi_hop")]
    texts = {"a": ["无关", "含金标一的段落", "含金标二的段落"]}

    assert text_mrr(samples, texts, k=5) == 0.5  # 首个命中排第 2
    assert text_multi_hop_recall(samples, texts, k=5) == 1.0  # 两段都召回


def test_text_full_recall_counts_all_types():
    """full_recall = 全部金标段落都召回（不限 query_type，E5-3 双段全中口径）。"""
    samples = [_st("a", ["金标一", "金标二"], qt="complex"),
               _st("b", ["金标三"], qt="single_hop")]
    texts = {"a": ["含金标一的段落", "含金标二的段落"], "b": ["无关"]}

    assert text_full_recall(samples, texts, k=5) == 0.5
    # multi_hop_recall 会把 complex 过滤掉（池子为空 → 0）
    assert text_multi_hop_recall(samples, texts, k=5) == 0.0


def test_evaluate_retrieval_text_shape():
    samples = [_st("a", ["金标"])]
    out = evaluate_retrieval_text(samples, {"a": ["含金标"]}, k=5)

    assert set(out) == {"hit_rate@5", "recall@5", "mrr@5", "multi_hop_recall@5",
                        "full_recall@5"}
    assert out["hit_rate@5"] == 1.0


def test_missing_gold_text_lists_unhit():
    samples = [_st("a", ["金标段落"]), _st("b", ["另一段"])]
    texts = {"a": ["含金标段落"], "b": ["无关"]}

    out = missing_gold_text(samples, texts, k=5)
    assert len(out) == 1 and out[0][0] == "b"
