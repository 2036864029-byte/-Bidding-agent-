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
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from pydantic import BaseModel, Field

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
    from .services.milvus_store import MilvusStoreManager

    from core.config.settings import Settings
    from core.embeddings.bge_m3 import create_embeddings
    from .retrieval.retriever import HybridRetriever
    from .retrieval.reranker import SiliconFlowReranker

    settings = Settings()
    embeddings = create_embeddings(settings)
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


def kb_search(question: str, *, top_k: int = 8) -> RetrievalResult:
    """检索公共知识库，返回三元组（不调 LLM）。

    Args:
        question: 用户问题。
        top_k: 返回的精排块上限（在检索结果上截断）。

    Returns:
        RetrievalResult（空结果时三元组为空，由调用方决定拒答/降级）。
    """
    from .guardrails.citations_build import build_citations

    outcome = _get_retriever().retrieve(question)
    docs_with_scores = outcome.docs[:top_k]
    trace = getattr(outcome.diagnostics, "to_dict", lambda: {})() or {}

    if not docs_with_scores:
        logger.info("kb_search: 检索为空，返回空三元组（question=%s）", question[:60])
        return RetrievalResult(recall_trace=trace)

    citations = build_citations(docs_with_scores)
    return RetrievalResult(
        chunks=[_to_chunk(doc, score) for doc, score in docs_with_scores],
        citations_meta=citations,
        rendered_context=format_docs(docs_with_scores),
        recall_trace=trace,
    )


def kb_search_as_tool():
    """构建 LangChain tool 形态的 kb_search（**只建不接线**，P6 多 Agent 用）。

    调用方拿到 tool 后自行绑定；本函数不注册到任何 agent。
    """
    from langchain_core.tools import tool

    @tool
    def kb_search_tool(question: str, top_k: int = 8) -> dict:
        """检索招投标公共知识库（法规/招标知识），返回三元组（含引用元数据与渲染上下文）。

        Args:
            question: 用户问题。
            top_k: 返回的精排块上限（默认 8）。

        Returns:
            dict：{chunks, citations_meta, rendered_context, recall_trace}。
        """
        result = kb_search(question, top_k=top_k)
        return result.model_dump(mode="json")

    return kb_search_tool


__all__ = [
    "Chunk",
    "RetrievalResult",
    "kb_search",
    "kb_search_as_tool",
    "reset_retriever",
]
