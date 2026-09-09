"""检索层指标（E2-1）— 确定性、零 LLM 成本。

用金标 `ground_truth_chunk_ids` 与检索结果 `chunk_id` 直接比对，不需要任何 judge
模型——这是整条评测链里**最硬、最便宜、最适合当 CI 门禁**的一层。

指标口径：
  hit_rate@k          top-k 是否命中任一金标块（每样本 0/1，取均值）
  recall@k            Σ命中金标数 / Σ金标总数
  mrr                 首个命中的排名倒数（未命中记 0），取均值
  multi_hop_recall@k  多跳样本中「金标块全部进 top-k」的比例
  precision@k         top-k 中属于金标块的比例（分母为实际返回条数）

**正文口径（text_* 系列）**：金标集构建于旧集合版本，其**行级 `chunk_id` 已相对
当前 `public_kb_chunked_v2` 过期**（集合重建过，同一段落 id 变了）。因此另提供按
**正文**判定的同口径指标——「检索块正文包含金标段落（归一化后）」即算命中。这在
语义上也更贴近检索评测的本意：衡量"是否召回了能作答的段落"。
待 E1 补做金标 id 重映射后，两套口径应一致。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Tuple

from evals.datasets import EvalSample

# question → 检索到的 chunk_id 有序列表（按相关性降序）
RetrievedMap = Dict[str, List[int]]
# question → 检索到的块正文有序列表（按相关性降序）
RetrievedTextMap = Dict[str, List[str]]


def _top(retrieved: Sequence[int], k: int) -> List[int]:
    return list(retrieved)[:k]


def _norm(text: str) -> str:
    """归一化：去除全部空白，消除排版差异。"""
    return re.sub(r"\s+", "", text or "")


def _passage_hit(texts: Sequence[str], gold: str) -> bool:
    """金标段落是否被某检索块覆盖（双向包含 + 长度守卫，容忍父子块粒度差异）。"""
    g = _norm(gold)
    if not g:
        return False
    for t in texts:
        nt = _norm(t)
        if not nt:
            continue
        if g in nt:
            return True
        # 检索块比金标短：仅在覆盖度足够时算命中，避免短片段误判
        if nt in g and len(nt) >= 0.6 * len(g):
            return True
    return False


def hit_rate(
    samples: Sequence[EvalSample], retrieved: RetrievedMap, *, k: int = 5
) -> float:
    """top-k 命中任一金标块的样本比例。"""
    if not samples:
        return 0.0
    hits = 0
    for s in samples:
        gold = set(s.ground_truth_chunk_ids)
        if gold and gold & set(_top(retrieved.get(s.question, []), k)):
            hits += 1
    return hits / len(samples)


def recall_at_k(
    samples: Sequence[EvalSample], retrieved: RetrievedMap, *, k: int = 5
) -> float:
    """金标块整体覆盖率（Σ命中 / Σ金标）。"""
    total_gold = sum(len(s.ground_truth_chunk_ids) for s in samples)
    if total_gold == 0:
        return 0.0
    hit = 0
    for s in samples:
        gold = set(s.ground_truth_chunk_ids)
        hit += len(gold & set(_top(retrieved.get(s.question, []), k)))
    return hit / total_gold


def mrr(samples: Sequence[EvalSample], retrieved: RetrievedMap, *, k: int = 5) -> float:
    """首个金标命中的排名倒数均值（未命中记 0）。"""
    if not samples:
        return 0.0
    total = 0.0
    for s in samples:
        gold = set(s.ground_truth_chunk_ids)
        for rank, cid in enumerate(_top(retrieved.get(s.question, []), k), start=1):
            if cid in gold:
                total += 1.0 / rank
                break
    return total / len(samples)


def multi_hop_recall(
    samples: Sequence[EvalSample], retrieved: RetrievedMap, *, k: int = 5
) -> float:
    """多跳样本中「全部金标块都进 top-k」的比例（衡量多源召回能力）。"""
    multi = [s for s in samples if s.is_multi_hop and s.ground_truth_chunk_ids]
    if not multi:
        return 0.0
    ok = sum(
        1
        for s in multi
        if set(s.ground_truth_chunk_ids) <= set(_top(retrieved.get(s.question, []), k))
    )
    return ok / len(multi)


def precision_at_k(
    samples: Sequence[EvalSample], retrieved: RetrievedMap, *, k: int = 5
) -> float:
    """top-k 中金标块的占比（分母为实际返回条数，无返回记 0）。"""
    num = den = 0
    for s in samples:
        top = _top(retrieved.get(s.question, []), k)
        if not top:
            continue
        gold = set(s.ground_truth_chunk_ids)
        num += len(gold & set(top))
        den += len(top)
    return num / den if den else 0.0


def evaluate_retrieval(
    samples: Sequence[EvalSample], retrieved: RetrievedMap, *, k: int = 5
) -> Dict[str, float]:
    """一次算出全部检索层指标（报告用）。"""
    return {
        f"hit_rate@{k}": round(hit_rate(samples, retrieved, k=k), 4),
        f"recall@{k}": round(recall_at_k(samples, retrieved, k=k), 4),
        f"mrr@{k}": round(mrr(samples, retrieved, k=k), 4),
        f"multi_hop_recall@{k}": round(multi_hop_recall(samples, retrieved, k=k), 4),
        f"precision@{k}": round(precision_at_k(samples, retrieved, k=k), 4),
    }


def missing_gold(
    samples: Sequence[EvalSample], retrieved: RetrievedMap, *, k: int = 5
) -> List[Tuple[str, List[int]]]:
    """返回「金标块一个都没进 top-k」的样本（人工归因用）。"""
    out: List[Tuple[str, List[int]]] = []
    for s in samples:
        gold = set(s.ground_truth_chunk_ids)
        if gold and not (gold & set(_top(retrieved.get(s.question, []), k))):
            out.append((s.question, sorted(gold)))
    return out


# ── 正文口径（金标行级 id 过期时的等价指标）────────────────────


def text_hit_rate(
    samples: Sequence[EvalSample], texts: RetrievedTextMap, *, k: int = 5
) -> float:
    """top-k 中至少召回一个金标段落的样本比例。"""
    if not samples:
        return 0.0
    hits = sum(
        1
        for s in samples
        if s.ground_truth_text
        and any(_passage_hit(texts.get(s.question, [])[:k], g) for g in s.ground_truth_text)
    )
    return hits / len(samples)


def text_recall(
    samples: Sequence[EvalSample], texts: RetrievedTextMap, *, k: int = 5
) -> float:
    """金标段落整体召回率（Σ召回段落 / Σ金标段落）。"""
    total = sum(len(s.ground_truth_text) for s in samples)
    if total == 0:
        return 0.0
    hit = sum(
        1
        for s in samples
        for g in s.ground_truth_text
        if _passage_hit(texts.get(s.question, [])[:k], g)
    )
    return hit / total


def text_mrr(
    samples: Sequence[EvalSample], texts: RetrievedTextMap, *, k: int = 5
) -> float:
    """首个命中金标段落的排名倒数均值（未命中记 0）。"""
    if not samples:
        return 0.0
    total = 0.0
    for s in samples:
        golds = s.ground_truth_text
        if not golds:
            continue
        for rank, chunk_text in enumerate(texts.get(s.question, [])[:k], start=1):
            if any(_passage_hit([chunk_text], g) for g in golds):
                total += 1.0 / rank
                break
    return total / len(samples)


def text_multi_hop_recall(
    samples: Sequence[EvalSample], texts: RetrievedTextMap, *, k: int = 5
) -> float:
    """多跳样本中「全部金标段落都被召回」的比例。"""
    multi = [s for s in samples if s.is_multi_hop and s.ground_truth_text]
    if not multi:
        return 0.0
    ok = sum(
        1
        for s in multi
        if all(_passage_hit(texts.get(s.question, [])[:k], g) for g in s.ground_truth_text)
    )
    return ok / len(multi)


def evaluate_retrieval_text(
    samples: Sequence[EvalSample], texts: RetrievedTextMap, *, k: int = 5
) -> Dict[str, float]:
    """正文口径的全部检索指标（金标 id 过期时使用）。"""
    return {
        f"hit_rate@{k}": round(text_hit_rate(samples, texts, k=k), 4),
        f"recall@{k}": round(text_recall(samples, texts, k=k), 4),
        f"mrr@{k}": round(text_mrr(samples, texts, k=k), 4),
        f"multi_hop_recall@{k}": round(text_multi_hop_recall(samples, texts, k=k), 4),
    }


def missing_gold_text(
    samples: Sequence[EvalSample], texts: RetrievedTextMap, *, k: int = 5
) -> List[Tuple[str, str]]:
    """返回「金标段落一条都没被召回」的 (问题, 金标段落首段)（人工归因用）。"""
    out: List[Tuple[str, str]] = []
    for s in samples:
        if not s.ground_truth_text:
            continue
        top = texts.get(s.question, [])[:k]
        if not any(_passage_hit(top, g) for g in s.ground_truth_text):
            out.append((s.question, s.ground_truth_text[0][:80]))
    return out


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
