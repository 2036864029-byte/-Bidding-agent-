# 功能：把命中的子块拼回完整父块，写入 metadata["context_text"]（不改 page_content）。
"""Parent chunk assembly for grouped retrieval.

设计约定（见 `docs/plan1/RAG 检索链路优化方案（修订版·可执行）.md` P2）：

- 只处理 ``chunk_type == "child"`` 的命中；独立块（atom）自身即完整语义单元，透传。
- 拼接结果写入 ``metadata["context_text"]``，**不覆盖 ``page_content``**：
  引用溯源（``citations_build`` 取 page_content）与 Milvus 回表校验依赖子块原文。
- 长度熔断：命中子块 + 前后各 1 块（缺边不取）→ 仍超阈值则只保留命中子块本身。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

_QA_PREFIX = "问："
_QUERY_LIMIT = 16384


def is_duplicate_parent(doc: Document, seen: set) -> bool:
    """同父兄弟子块去重：``seen`` 中已有该 parent 时返回 True（无 parent 的不去重）。"""
    parent_id = str(doc.metadata.get("parent_chunk_id") or "")
    if not parent_id:
        return False
    if parent_id in seen:
        return True
    seen.add(parent_id)
    return False


def dedupe_by_parent(
    docs_with_scores: Sequence[Tuple[Document, float]],
) -> List[Tuple[Document, float]]:
    """按 ``parent_chunk_id`` 去重（保留首次出现=得分最高者），无 parent 的原样保留。"""
    seen: set = set()
    return [
        (doc, score)
        for doc, score in docs_with_scores
        if not is_duplicate_parent(doc, seen)
    ]


def assemble_parent_contexts(
    docs: Sequence[Document],
    *,
    collection: Any,
    collection_name: str,
    max_chars: int,
) -> None:
    """就地补全 ``docs`` 中 child 文档的父块全文（``metadata["context_text"]``）。

    Args:
        docs: 检索结果文档（就地修改 metadata）。
        collection: pymilvus MilvusClient 实例。
        collection_name: 集合名。
        max_chars: 父块拼接长度熔断阈值。
    """
    parent_ids = {
        str(doc.metadata.get("parent_chunk_id") or "")
        for doc in docs
        if doc.metadata.get("chunk_type") == "child"
    }
    parent_ids.discard("")
    if not parent_ids:
        return

    groups = _fetch_children(collection, collection_name, sorted(parent_ids))
    for doc in docs:
        if doc.metadata.get("chunk_type") != "child":
            continue
        parent_id = str(doc.metadata.get("parent_chunk_id") or "")
        children = groups.get(parent_id)
        if not children:
            logger.warning("父块 %s 未取回子块，保留子块原文", parent_id)
            continue
        context = _assemble_one(children, doc, parent_id, max_chars)
        if context:
            doc.metadata["context_text"] = context


def _fetch_children(
    collection: Any,
    collection_name: str,
    parent_ids: Sequence[str],
) -> Dict[str, List[dict]]:
    """按 ``parent_chunk_id`` 批量取回子块并按父分组（组内按 sequence 升序）。"""
    rows = collection.query(
        collection_name,
        filter=f"parent_chunk_id in {list(parent_ids)}",
        output_fields=["parent_chunk_id", "sequence_in_parent", "text"],
        limit=_QUERY_LIMIT,
    )
    groups: Dict[str, List[dict]] = {}
    for row in rows:
        parent_id = str(row.get("parent_chunk_id") or "")
        if not parent_id:
            continue
        groups.setdefault(parent_id, []).append(row)
    for children in groups.values():
        children.sort(key=lambda item: int(item.get("sequence_in_parent") or 0))
    return groups


def _assemble_one(
    children: Sequence[dict],
    hit_doc: Document,
    parent_id: str,
    max_chars: int,
) -> str:
    """拼接单个父块；超长时按「±1 邻居 → 仅命中子块」逐级降级。"""
    full_text = _join_texts(child.get("text", "") for child in children)
    if len(full_text) <= max_chars:
        return full_text

    hit_seq = int(hit_doc.metadata.get("sequence_in_parent") or 0)
    window = _pick_window(children, hit_seq)
    window_text = _join_texts(child.get("text", "") for child in window)
    logger.info(
        "父块 %s 超长 (%d > %d)，降级为命中块±1 共 %d 块 (%d 字符)",
        parent_id, len(full_text), max_chars, len(window), len(window_text),
    )
    if len(window_text) <= max_chars:
        return window_text

    hit_text = next(
        (child.get("text", "") for child in children
         if int(child.get("sequence_in_parent") or 0) == hit_seq),
        "",
    )
    logger.info("父块 %s 的 ±1 邻居仍超阈值，仅保留命中子块 (%d 字符)", parent_id, len(hit_text))
    return hit_text


def _pick_window(children: Sequence[dict], hit_seq: int) -> List[dict]:
    """命中子块 + 前后各 1 块；命中块位于首/末时缺失的一侧不取。"""
    ordered = list(children)
    index = next(
        (i for i, child in enumerate(ordered)
         if int(child.get("sequence_in_parent") or 0) == hit_seq),
        None,
    )
    if index is None:
        return ordered
    return ordered[max(0, index - 1): index + 2]


def _join_texts(texts: Iterable[str]) -> str:
    """按序拼接；重复出现的 QA 问题前缀只保留首个。"""
    parts: List[str] = []
    prefix_kept = False
    for raw in texts:
        text = raw or ""
        if text.startswith(_QA_PREFIX):
            if prefix_kept:
                _, separator, body = text.partition("\n\n")
                text = body if separator else text
            else:
                prefix_kept = True
        stripped = text.strip()
        if stripped:
            parts.append(stripped)
    return "\n".join(parts)
