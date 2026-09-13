# 功能：上传文档 → 索引/上下文渲染（C1 胶水层）。
"""api.doc_context — 把「会话文件」变成「喂给 doc_qa 的上下文」。

**分层**：本模块位于 api 层，可同时 import ``public_kb``（分块）与 ``core``
（索引引擎 ``core.doc_index``）。``core`` 不得反向依赖 ``public_kb``，故分块
链条留在这里，见 ``core/doc_index.py`` 的模块文档。

**两条路径**：

  检索模式（长文档）::

      提问 → 复用上传时建好的索引 → top-K 块（字符数恒定）→ 渲染块标记

  全文模式（回退）::

      小文档 / 未建索引 / 索引不可用 → 沿用「全文注入」（旧行为）

**渲染契约**（上下文经 SystemMessage 注入，AgentState 不加字段，D-0-2）：
每个文件渲染为 ``[文件：名]`` 开头的一段；检索模式在其后附机器可读头 + 块标记，
供 ``agent/nodes/doc_qa.py`` 解析出引用来源与相关度（见该模块的 ``_BLOCK_RE``）
::

    [文件：招标文件.pdf]
    [检索模式｜命中 5/12 块｜最高相关度 0.820]
    [块 12｜1/5｜第三章 评标办法｜0.820]
    <块正文>
    [块 40｜2/5｜第四章 合同条款｜0.610]
    ...

全文模式不写头（节点据上下文长度自行判定截断），保持与旧格式完全一致。
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence, Tuple

from core.config.settings import Settings

logger = logging.getLogger(__name__)

MODE_RETRIEVAL = "retrieval"
MODE_FULLTEXT = "fulltext"

#: 块标记：``[块 {原文块序}｜{命中序}/{命中总数}｜{章节}｜{相关度}]``
BLOCK_LINE = "[块 {idx}｜{seq}/{total}｜{chapter}｜{score:.3f}]"
#: 检索模式头：``[检索模式｜命中 {used}/{total} 块｜最高相关度 {max:.3f}]``
RETRIEVAL_HEADER = "[检索模式｜命中 {used}/{total} 块｜最高相关度 {score:.3f}]"


def chunk_record(record, settings: Settings) -> List[Any]:
    """会话文件记录 → Document 列表（跳过清洗：``FileRecord.markdown`` 已清洗）。

    ``parse_file`` 的产物是 **cleaned** markdown，故必须用 ``chunk_cleaned_markdown``
    而不是 ``chunk_markdown``——后者会再清洗一遍，属重复处理。
    """
    from public_kb.ingestion.handoff import chunk_cleaned_markdown

    return chunk_cleaned_markdown(record.markdown, record.source_name, settings)


def build_index_for(record, settings: Optional[Settings] = None) -> bool:
    """上传时为文件建索引；返回是否建成。

    小文档（块数 ≤ ``doc_index_full_context_max_chunks``）**跳过建索引**——全文
    本来就没多大，检索反而可能漏，白付一次向量化成本。失败即回退全文模式，
    不阻断上传（索引挂了只是退回旧行为）。
    """
    from core import doc_index

    settings = settings or Settings()
    if not getattr(settings, "doc_index_enabled", True):
        return False

    total = int(getattr(record, "chunk_count", 0) or 0)
    min_chunks = int(getattr(settings, "doc_index_full_context_max_chunks", 12) or 12)
    if total <= min_chunks:
        logger.debug("doc_context: %s 仅 %d 块，走全文模式，不建索引",
                     record.source_name, total)
        return False

    try:
        docs = chunk_record(record, settings)
    except Exception as exc:  # noqa: BLE001  分块失败 → 回退全文模式
        logger.warning("doc_context 分块失败（回退全文模式）: %s err=%s",
                       record.source_name, exc)
        return False

    chunks = [
        doc_index.DocChunk(
            chunk_index=i,
            chapter=str((doc.metadata or {}).get("chapter") or ""),
            content=doc.page_content,
        )
        for i, doc in enumerate(docs)
        if doc.page_content and doc.page_content.strip()
    ]
    built = doc_index.build(
        file_id=record.file_id,
        session_id=record.session_id,
        source_name=record.source_name,
        chunks=chunks,
        settings=settings,
    )
    if built is None:
        logger.info("doc_context: %s 未建索引，提问时将回退全文模式", record.source_name)
    return built is not None


def _render_retrieval(result, total_chunks: int) -> str:
    """检索结果 → 头 + 块标记 + 正文。"""
    used = len(result.hits)
    max_score = result.max_dense if result.max_dense is not None else 0.0
    parts = [RETRIEVAL_HEADER.format(used=used, total=total_chunks, score=max_score)]
    for seq, hit in enumerate(result.hits, start=1):
        parts.append(
            BLOCK_LINE.format(idx=hit.chunk_index, seq=seq, total=used,
                              chapter=hit.chapter or "未知章节", score=hit.dense)
        )
        parts.append(hit.content)
    return "\n".join(parts)


def render_for_record(
    record,
    question: str,
    settings: Optional[Settings] = None,
) -> Tuple[str, str]:
    """渲染单个文件的上下文 → ``(mode, content)``。

    走检索需同时满足：索引开关开、文档块数超过「小文档阈值」、索引已建。
    任何一步不满足即回退全文模式——**回退是预期路径，不是异常**。
    """
    from core import doc_index

    settings = settings or Settings()
    if not getattr(settings, "doc_index_enabled", True):
        return MODE_FULLTEXT, record.markdown

    total = int(getattr(record, "chunk_count", 0) or 0)
    min_chunks = int(getattr(settings, "doc_index_full_context_max_chunks", 12) or 12)
    # 小文档全文本来就装得下，检索反而可能漏掉上下文，直接走全文
    if total <= min_chunks:
        return MODE_FULLTEXT, record.markdown

    try:
        result = doc_index.search_file(
            record.file_id, record.session_id, question, settings=settings
        )
    except Exception as exc:  # noqa: BLE001  检索异常 → 回退全文模式
        logger.warning("doc_context 检索失败（回退全文模式）: %s err=%s",
                       record.source_name, exc)
        return MODE_FULLTEXT, record.markdown

    if result is None or result.empty:
        return MODE_FULLTEXT, record.markdown

    logger.info(
        "doc_context 检索: %s 命中 %d/%d 块 max_dense=%.3f low_conf=%s %dms",
        record.source_name, len(result.hits), total,
        result.max_dense or 0.0, result.low_confidence, result.elapsed_ms,
    )
    return MODE_RETRIEVAL, _render_retrieval(result, total)


def context_docs(
    records: Sequence[Any],
    question: str,
    *,
    kind: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> List[Tuple[str, str, str]]:
    """一组文件记录 → ``(文件名, 上下文, kind)`` 列表（graph 注入用）。

    单个文件渲染失败不影响其余文件（逐个兜底到全文）。
    """
    settings = settings or Settings()
    out: List[Tuple[str, str, str]] = []
    for record in records:
        try:
            _mode, content = render_for_record(record, question, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("doc_context 渲染异常，按全文注入: %s err=%s",
                           getattr(record, "source_name", "?"), exc)
            content = record.markdown
        entry: Tuple[str, str, str] = (record.source_name, content, kind or "explicit")
        out.append(entry)
    return out


__all__ = [
    "MODE_RETRIEVAL",
    "MODE_FULLTEXT",
    "BLOCK_LINE",
    "RETRIEVAL_HEADER",
    "chunk_record",
    "build_index_for",
    "render_for_record",
    "context_docs",
]
