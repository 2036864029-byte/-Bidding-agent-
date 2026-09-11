# 功能：RAG 能力包工具面 — kb_search 供 Agent / 多 Agent 当工具调用。
"""public_kb.tools — RAG 工具化门面（方案 Phase 1 / B-1，解 R2/R4）。

目标：把 RAG 变成"可被 agent 当工具调用、返回 chunks + 引用元数据 + 渲染上下文"
的能力包。kb_search **不调 LLM**——只检索 + 构建三元组，生成编排留在节点侧。

本 Phase 只建契约与脚手架，**不接线到节点**（B-4 节点重写、开关 USE_TOOL_KB 在
后续步骤接）。retriever 用带锁惰性工厂持有（解 R4：public_kb 全局单例竞态）。

三元组（返回结构，作为 Agent 消费的稳定契约）：
  chunks          精排后文本块（正文 + 来源/评分元数据）
  citations_meta  标准化引用（与 guardrails 同构，可直接 format_citations / R1~R7 校验）
  rendered_context 已渲染的 LLM 参考上下文（format_docs 输出，供 prompt 直接拼装）
  recall_trace     召回诊断（阶段/降级原因，供可观测）
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

from langchain_core.documents import Document
from pydantic import BaseModel, Field

from core.observability.tracing import set_span_attrs
from core.observability.tracing import span as _span

from .guardrails.citations_models import Citation
from .rendering.context import format_docs

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════
# 工具契约模型
# ═════════════════════════════════════════════════════════

class Chunk(BaseModel):
    """精排后的单个文本块（工具返回给调用方的最小可消费单元）。"""

    content: str = Field(description="块正文")
    score: float = Field(0.0, description="精排分数（0~1）")
    chunk_id: Optional[int] = Field(None, description="Milvus 行级 chunk_id（int 主键）")
    chunk_uid: Optional[str] = Field(None, description="内容派生 chunk_uid")
    doc_name: str = Field("", description="数据源文档名")
    chapter: str = Field("", description="章节路径")
    chunk_index: int = Field(-1, description="文档内块序号")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="其余透传元数据")


class RetrievalResult(BaseModel):
    """kb_search 的三元组返回契约（区别于 retriever.RetrievalResult）。"""

    chunks: List[Chunk] = Field(default_factory=list)
    citations_meta: List[Citation] = Field(
        default_factory=list,
        description="标准化引用（与 guardrails 同构，可直接校验/渲染）",
    )
    rendered_context: str = Field("", description="已渲染的 LLM 参考上下文")
    recall_trace: Dict[str, Any] = Field(
        default_factory=dict, description="召回诊断（阶段/降级原因）"
    )


# ═════════════════════════════════════════════════════════
# 带锁惰性 retriever 单例（解 R4 竞态）
# ═════════════════════════════════════════════════════════

_retriever_lock = threading.Lock()
_retriever: Optional[Any] = None


def _build_retriever():
    """按 build_chain 现有装配逻辑（generation/chain.py 检索段）惰性构建 HybridRetriever。

    与 rag_engine._build_qa_chain 的接线一致（MilvusStoreManager.load_existing →
    collection/store + BGE embeddings + SiliconFlow reranker）。重依赖在此延迟导入，
    保证 import public_kb.tools 轻量。
    """
    from core.config.settings import Settings
    from core.contracts import ConfigurationContractError
    from core.embeddings import get_embeddings
    from core.vectorstore.milvus_client import MilvusStoreManager

    from .retrieval.reranker import SiliconFlowReranker
    from .retrieval.retriever import HybridRetriever
    from .schema_registry import validate_embedding_config_strict

    settings = Settings()
    # W1-4：embedding 配置与集合约定校验接线（换模型/维度须重建集合）。
    # 契约错误转 RuntimeError，由上层按"知识库不可用"引导，而不是静默用错向量空间。
    try:
        validate_embedding_config_strict(settings)
    except ConfigurationContractError as exc:
        raise RuntimeError(f"embedding 配置与集合约定不一致，拒绝检索: {exc}") from exc
    embeddings = get_embeddings(settings)
    manager = MilvusStoreManager(settings, embeddings)
    manager.load_existing()  # 集合不存在时 collection 置空 → 走稠密降级
    reranker = SiliconFlowReranker(
        model=settings.reranker_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        timeout=settings.reranker_timeout,
        max_retries=settings.reranker_max_retries,
        retry_backoff_seconds=settings.reranker_retry_backoff_seconds,
    )
    return HybridRetriever(
        vector_store=manager.store,
        collection=manager.collection,
        embeddings=embeddings,
        settings=settings,
        reranker=reranker,
    )


def _get_retriever():
    """带锁获取 retriever 单例（测试可 monkeypatch 本函数注入假 retriever）。"""
    global _retriever
    if _retriever is None:
        with _retriever_lock:
            if _retriever is None:
                _retriever = _build_retriever()
                logger.info("kb_search: HybridRetriever 惰性初始化完成")
    return _retriever


def reset_retriever() -> None:
    """清空 retriever 单例（测试/重载用）。"""
    global _retriever
    _retriever = None


# ═════════════════════════════════════════════════════════
# kb_search 主入口
# ═════════════════════════════════════════════════════════

# W3-3：summary 契约的摘要长度（块正文截断阈值）。
_SUMMARY_MAX_CHARS = 240


def _to_chunk(doc: Document, score: float) -> Chunk:
    meta = doc.metadata or {}
    chunk_id = meta.get("chunk_id")
    return Chunk(
        content=doc.page_content,
        score=round(float(score), 4),
        chunk_id=int(chunk_id) if isinstance(chunk_id, (int, str)) and str(chunk_id).isdigit() else None,
        chunk_uid=meta.get("chunk_uid"),
        doc_name=meta.get("doc_name", ""),
        chapter=meta.get("chapter", ""),
        chunk_index=int(meta.get("chunk_index", -1)),
        metadata={k: v for k, v in meta.items() if not k.startswith("_")},
    )


def _project_chunk(
    chunk: Chunk,
    *,
    summary: bool,
    include_fields: Optional[Iterable[str]],
    summary_max_chars: int,
) -> Chunk:
    """按工具契约投影单个 chunk（W3-3 Deep Agent 契约）。

    summary=True → content 截为摘要片段（超 summary_max_chars 字截断并加 …）；
    include_fields 非空 → 只保留白名单顶层字段（其余置空值，metadata 同步过滤）。
    返回新对象不改原 chunk——子图全量路径（summary=False、无字段过滤）逐字段不变。
    """
    content = chunk.content
    if summary and len(content) > summary_max_chars:
        content = content[:summary_max_chars] + "…"

    if include_fields is None:
        return Chunk(
            content=content,
            score=chunk.score,
            chunk_id=chunk.chunk_id,
            chunk_uid=chunk.chunk_uid,
            doc_name=chunk.doc_name,
            chapter=chunk.chapter,
            chunk_index=chunk.chunk_index,
            metadata=chunk.metadata,
        )

    allowed = set(include_fields)
    blank = {  # 字段白名单过滤时的空值（与 Chunk 各字段类型一致）
        "content": "",
        "score": 0.0,
        "chunk_id": None,
        "chunk_uid": None,
        "doc_name": "",
        "chapter": "",
        "chunk_index": -1,
    }
    values = {
        name: (getattr(chunk, name) if name in allowed else blank[name])
        for name in blank
    }
    return Chunk(
        content=values["content"],
        score=values["score"],
        chunk_id=values["chunk_id"],
        chunk_uid=values["chunk_uid"],
        doc_name=values["doc_name"],
        chapter=values["chapter"],
        chunk_index=values["chunk_index"],
        metadata={k: v for k, v in chunk.metadata.items() if k in allowed},
    )


def kb_search(
    question: str,
    *,
    top_k: int = 11,  # E5/多跳决策：与 settings.retrieval_top_k(11) 对齐，默认透出全窗口
    offset: int = 0,
    summary: bool = False,
    include_fields: Optional[Iterable[str]] = None,
    summary_max_chars: int = _SUMMARY_MAX_CHARS,
) -> RetrievalResult:
    """检索公共知识库，返回三元组（不调 LLM）。

    Args:
        question: 用户问题。
        top_k: 返回的精排块上限（分页窗口大小）。
        offset: 分页偏移（与 top_k 组成分页窗口；引用/渲染上下文按窗口生成）。
        summary: True → 只返回摘要块（content 截断）且省略 rendered_context，
            便于 agent 多次检索组合进上下文（compact 契约）。
        include_fields: 只保留指定顶层字段（content/score/doc_name/chapter/
            chunk_id/chunk_uid/chunk_index），其余置空；None=返回全部。
        summary_max_chars: summary 模式的正文截断阈值。

    Returns:
        RetrievalResult（空结果时三元组为空，由调用方决定拒答/降级）。
    """
    from .guardrails.citations_build import build_citations

    # B12：top_k 可能由 LLM 通过工具参数传入（可为 0/负数），先钳制再截断，
    # 否则"检索有结果但 chunks 被截空"会被上层误判为无召回而拒答。
    top_k = max(1, int(top_k))
    offset = max(0, int(offset))

    with _span("retrieval.kb_search", **{"ztb.retrieval.top_k": top_k}) as _sp:
        outcome = _get_retriever().retrieve(question)
        trace = getattr(outcome.diagnostics, "to_dict", lambda: {})() or {}

        if not outcome.docs:
            logger.info("kb_search: 检索为空，返回空三元组（question=%s）", question[:60])
            set_span_attrs(_sp, **{"ztb.retrieval.docs": 0})
            return RetrievalResult(recall_trace=trace)

        docs_with_scores = outcome.docs[offset: offset + top_k]
        if not docs_with_scores:
            set_span_attrs(_sp, **{"ztb.retrieval.docs": 0})
            return RetrievalResult(recall_trace=trace)

        citations = build_citations(docs_with_scores)
        set_span_attrs(
            _sp,
            **{
                "ztb.retrieval.docs": len(docs_with_scores),
                "ztb.retrieval.mode": str(trace.get("retrieval_mode", "")),
                "ztb.retrieval.reranker": str(trace.get("reranker_status", "")),
            },
        )
        return RetrievalResult(
            chunks=[
                _project_chunk(
                    _to_chunk(doc, score),
                    summary=summary,
                    include_fields=include_fields,
                    summary_max_chars=summary_max_chars,
                )
                for doc, score in docs_with_scores
            ],
            citations_meta=citations,
            rendered_context="" if summary else format_docs(docs_with_scores),
            recall_trace=trace,
        )


def _split_clauses(question: str, min_len: int = 8) -> List[str]:
    """按标点切子句，过滤过短片段（纯连接词/介词短语检索无意义）。"""
    import re

    parts = [p.strip() for p in re.split(r"[？?。；;，,\n]", question)]
    return [p for p in parts if len(p) >= min_len]


def _norm_key(text: str) -> str:
    """去重键：去除全部空白（子块/父块排版差异）。"""
    import re

    return re.sub(r"\s+", "", text or "")


def kb_search_expanded(
    question: str,
    *,
    top_k: int = 11,
    max_chunks: int = 20,
    sub_top_k: int = 5,
    min_clause_len: int = 8,
    summary: bool = False,
    include_fields: Optional[Iterable[str]] = None,
    summary_max_chars: int = _SUMMARY_MAX_CHARS,
) -> RetrievalResult:
    """统一轻量补检（Layer 1）：基线 top_k + 子句检索并集，基线原序保持。

    与 kb_search 同契约。基线块保持原序（→ MRR 不摊薄，区别于子句无序并集）；
    子句补检**每个子句只取高分块 top_k（默认 5）再合并**——若取子句全部 docs，
    会按子句顺序保留低相关度的首遇版本（如第八条在子句 A 排第 7、子句 B 排第 0，
    先遇 A 则以低分入池被截断）；合并后按 score 全局降序追加，总块数封顶
    max_chunks（默认 20：S3 实测 16 仅双段全中 0.4857，20 才复现 S2 的 0.6286）。
    开关 `settings.kb_light_decompose` 由调用方决定是否走本函数。
    """
    from .guardrails.citations_build import build_citations

    outcome = _get_retriever().retrieve(question)
    trace = getattr(outcome.diagnostics, "to_dict", lambda: {})() or {}
    docs: List[Tuple[Document, float]] = list(outcome.docs[:top_k])

    clauses = _split_clauses(question, min_clause_len)
    if clauses and len(docs) < max_chunks:
        base_seen = {_norm_key(doc.page_content) for doc, _ in docs}
        pool: List[Tuple[Document, float]] = []
        pool_seen: set = set()
        for clause in clauses:
            sub = _get_retriever().retrieve(clause)
            for doc, score in sub.docs[:sub_top_k]:
                key = _norm_key(doc.page_content)
                if key in base_seen or key in pool_seen:
                    continue
                pool_seen.add(key)
                pool.append((doc, score))
        pool.sort(key=lambda item: item[1], reverse=True)
        for doc, score in pool:
            if len(docs) >= max_chunks:
                break
            docs.append((doc, score))

    if not docs:
        logger.info("kb_search_expanded: 检索为空（question=%s）", question[:60])
        return RetrievalResult(recall_trace=trace)

    citations = build_citations(docs)
    return RetrievalResult(
        chunks=[
            _project_chunk(
                _to_chunk(doc, score),
                summary=summary,
                include_fields=include_fields,
                summary_max_chars=summary_max_chars,
            )
            for doc, score in docs
        ],
        citations_meta=citations,
        rendered_context="" if summary else format_docs(docs),
        recall_trace=trace,
    )


def kb_search_as_tool():
    """构建 LangChain tool 形态的 kb_search（**只建不接线**，P6 多 Agent / W3-3 用）。

    调用方拿到 tool 后自行绑定；本函数不注册到任何 agent。
    默认 summary=True（compact 契约：内容截断、省 rendered_context），供 agent
    多次检索组合上下文；需要全文时显式 summary=False。
    """
    from langchain_core.tools import tool

    @tool
    def kb_search_tool(
        question: str,
        top_k: int = 5,
        offset: int = 0,
        summary: bool = True,
        include_fields: Optional[List[str]] = None,
    ) -> dict:
        """检索招投标公共知识库（法规/招标知识），返回三元组（含引用元数据与渲染上下文）。

        Args:
            question: 用户问题。
            top_k: 返回的精排块上限（默认 5）。
            offset: 分页偏移（与 top_k 组成分页窗口）。
            summary: True → 只返回摘要块（content 截断、省略 rendered_context），
                适合 agent 多次检索组合上下文；False → 返回完整块与渲染上下文。
            include_fields: 只保留指定字段（content/score/doc_name/chapter/
                chunk_id/chunk_uid/chunk_index），不传返回全部。

        Returns:
            dict：{chunks, citations_meta, rendered_context, recall_trace}。
        """
        result = kb_search(
            question, top_k=top_k, offset=offset,
            summary=summary, include_fields=include_fields,
        )
        return result.model_dump(mode="json")

    return kb_search_tool


__all__ = [
    "Chunk",
    "RetrievalResult",
    "kb_search",
    "kb_search_as_tool",
    "reset_retriever",
]
