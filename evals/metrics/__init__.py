"""evals.metrics — 评测指标计算（检索层确定性指标起步）。"""

from __future__ import annotations

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
    text_mrr,
    text_multi_hop_recall,
    text_recall,
)

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
    "text_recall",
    "text_mrr",
    "text_multi_hop_recall",
    "evaluate_retrieval_text",
    "missing_gold_text",
]
