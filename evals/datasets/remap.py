"""金标 chunk_id 重映射（E1-5）— 用 `ground_truth_text` 对当前集合离线回填。**纯函数**。

集合重建后 Milvus 主键（auto_id）变了，原 `ground_truth_chunk_ids` 失效。本模块用
金标段落文本对当前集合做**归一化匹配**：
  ① 精确相等（最高置信）→ 直接用；
  ② 双向包含 + 长度守卫（容忍父子块粒度差异）→ 兜底。
回填 `ground_truth_chunk_ids`（当前集合主键 id）与 `ground_truth_chunk_uids`
（内容派生 chunk_uid，换集合不变）。

匹配口径复用 `evals.metrics.retrieval`（正文口径），故重映射后 **ID 口径与正文口径收敛**。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .rag import EvalSample

__all__ = ["build_index", "match_passage", "remap_samples"]


def _helpers():
    """延迟导入（避免 evals.metrics.retrieval ↔ evals.datasets 模块级循环）。"""
    from evals.metrics.retrieval import _norm, _passage_hit

    return _norm, _passage_hit


def build_index(chunks: Sequence[dict]) -> dict:
    """chunks: [{id, chunk_uid, text}] → {"exact": {norm: (id, uid)}, "all": [(norm, id, uid)]}。"""
    norm, _ = _helpers()
    exact: Dict[str, Tuple[int, str]] = {}
    allrows: List[Tuple[str, int, str]] = []
    for row in chunks:
        nt = norm(row.get("text", ""))
        if not nt:
            continue
        cid, uid = row.get("id"), row.get("chunk_uid", "")
        allrows.append((nt, cid, uid))
        exact.setdefault(nt, (cid, uid))
    return {"exact": exact, "all": allrows}


def match_passage(gold: str, index: dict) -> Tuple[Optional[int], Optional[str], str]:
    """金标段落 → (chunk_id, chunk_uid, how)；how ∈ exact / contained / none。"""
    norm, hit = _helpers()
    ng = norm(gold)
    if not ng:
        return None, None, "none"
    found = index["exact"].get(ng)
    if found:
        return found[0], found[1], "exact"
    for nt, cid, uid in index["all"]:
        if hit([nt], ng):
            return cid, uid, "contained"
    return None, None, "none"


def remap_samples(
    samples: Sequence[EvalSample], index: dict
) -> Tuple[List[EvalSample], dict]:
    """逐段匹配回填 ids/uids。

    Returns:
        (新样本列表, 统计 {samples, passages, exact, contained, none, samples_all_matched})
    """
    out: List[EvalSample] = []
    stats = {"samples": len(samples), "passages": 0, "exact": 0, "contained": 0,
             "none": 0, "samples_all_matched": 0}
    for s in samples:
        ids: List[int] = []
        uids: List[str] = []
        ok = True
        for gt in s.ground_truth_text:
            stats["passages"] += 1
            cid, uid, how = match_passage(gt, index)
            stats[how] += 1
            if cid is None:
                ok = False
            else:
                ids.append(cid)
                uids.append(uid)
        if ok and s.ground_truth_text:
            stats["samples_all_matched"] += 1
        out.append(s.model_copy(update={
            "ground_truth_chunk_ids": ids, "ground_truth_chunk_uids": uids}))
    return out, stats
