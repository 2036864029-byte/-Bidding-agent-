# 功能：PublicKnowledgeRAG 对外门面，串联离线入库与在线问答。
"""Public knowledge RAG facade — renamed from rag_engine.py (Phase 1 / B-5).

构造注入化：settings / llm / embeddings / store_manager 均可传入（不传则按
Settings 自建）。query() 内部仍走既有 LCEL 链（行为不变）；rag_engine 兼容壳已随 P6 结构收敛删除。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from langchain_core.language_models import BaseChatModel
from langchain_openai import OpenAIEmbeddings

from core.config.settings import Settings
from core.contracts import validate_qa_result
from core.embeddings import get_embeddings
from core.llm.registry import create_llm_for_slot
from core.vectorstore.milvus_client import MilvusStoreManager

from .generation.chain import build_chain
from .ingestion.pipeline import FileIngestionPipeline
from .ingestion.sinks.milvus_sink import MilvusSink
from .retrieval.reranker import SiliconFlowReranker
from .schema_registry import validate_embedding_config_strict

logger = logging.getLogger(__name__)


class PublicKnowledgeRAG:
    """Public entry point for knowledge-base initialization and querying."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        llm: Optional[BaseChatModel] = None,
        embeddings: Optional[OpenAIEmbeddings] = None,
        store_manager: Optional[MilvusStoreManager] = None,
    ) -> None:
        self._settings = settings or Settings()
        # W1-4：embedding 配置与集合约定校验接线（换模型/维度必须重建集合，
        # 否则旧向量空间被新查询向量污染）。不一致直接拒绝构造。
        validate_embedding_config_strict(self._settings)
        self._embeddings: OpenAIEmbeddings = (
            embeddings if embeddings is not None else get_embeddings(self._settings)
        )
        self._llm: BaseChatModel = llm if llm is not None else create_llm_for_slot(
            "kb_generation", self._settings
        )
        self._store_manager = store_manager if store_manager is not None else MilvusStoreManager(
            self._settings, self._embeddings
        )
        self._qa_chain: Optional[Any] = None
        logger.info("PublicKnowledgeRAG 初始化完成")

    def init_knowledge_base(self, pdf_dir: str) -> None:
        """Parse all PDFs, validate chunks, and initialize the collection.

        经三段式 FileIngestionPipeline 入库（FormatRouter → PdfNormalizer →
        clean/chunk → MilvusSink），与 CLI 入库同一条流水线；doc_name 取 pdf
        stem（与 handoff 口径一致，跨机器稳定）。
        """
        pdf_path = Path(pdf_dir).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 目录不存在: {pdf_dir}")
        if not pdf_path.is_dir():
            raise NotADirectoryError(f"路径不是目录: {pdf_dir}")

        pdf_files = sorted(pdf_path.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(f"目录中未找到 PDF 文件: {pdf_dir}")

        logger.info("开始公共知识库初始化，共发现 %d 个 PDF 文件", len(pdf_files))
        results = FileIngestionPipeline(
            self._settings,
            sink=MilvusSink(self._store_manager, mode="initialize"),
        ).run(pdf_files)

        completed = [r for r in results if r.status == "completed"]
        failed = [r for r in results if r.status == "failed"]
        if not completed:
            raise RuntimeError(
                f"所有 PDF 处理均失败，无法初始化知识库。失败清单: "
                f"{[r.path for r in failed]}"
            )

        logger.info(
            "PDF 初始化入库完成: chunks=%d, inserted=%d",
            sum(r.chunk_count for r in completed),
            sum(r.inserted_count for r in completed),
        )

        self._build_qa_chain()
        logger.info(
            "公共知识库初始化完成：成功 %d 本，失败 %d 本",
            len(completed), len(failed),
        )

    def query(self, question: str) -> Dict[str, Any]:
        """Answer a question and return the existing QA result contract."""
        if self._qa_chain is None:
            raise RuntimeError(
                "知识库尚未初始化，请先调用 init_knowledge_base() 入库。"
            )

        logger.info("用户提问: %s", question[:100])
        result: Dict[str, Any] = self._qa_chain.invoke(question)
        # W1-4：契约校验接线（此前 validate_qa_result 无任何生产调用方）
        validate_qa_result(result)
        logger.info("回答完成，来源数: %d", len(result.get("sources", [])))
        return result

    def clear_kb(self) -> None:
        """Clear the configured experimental collection."""
        logger.warning("正在清空 public_kb 集合...")
        self._store_manager.clear_collection()
        self._qa_chain = None
        # B11：工具路径的 retriever 单例持有旧的 MilvusClient/集合；不失效的话
        # 同进程清空重建后 kb_search 仍指向已删集合，直到重启。
        from .tools import reset_retriever

        reset_retriever()
        logger.warning("public_kb 集合已清空。")

    def ensure_loaded(self) -> None:
        """Load an existing collection and build its QA chain."""
        self._store_manager.load_existing()
        self._build_qa_chain()

    def load_existing(self) -> bool:
        """公开入口：加载并校验已存在的集合，成功后构建问答链。

        返回是否加载成功（集合不存在时为 False）。此前 CLI 直接访问私有
        ``_store_manager.load_existing()``（M6 治理），现收敛到公开方法。
        """
        loaded = self._store_manager.load_existing()
        if loaded:
            self._build_qa_chain()
        return loaded

    def add_pdf(self, pdf_path: str) -> int:
        """Parse one PDF and append its chunks to the existing collection."""
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"文件不是 PDF: {pdf_path}")

        logger.info("增量导入 PDF: %s", path.name)
        results = FileIngestionPipeline(
            self._settings,
            sink=MilvusSink(self._store_manager, mode="append"),
        ).run([path])
        res = results[0]
        if res.status == "failed":
            raise RuntimeError(f"PDF 导入失败: {res.reason}")
        if self._qa_chain is None:
            self._build_qa_chain()

        logger.info("增量导入完成: %s → %d 块", path.name, res.chunk_count)
        return res.chunk_count

    def _build_qa_chain(self) -> None:
        """Build the LCEL chain with native Milvus hybrid retrieval when available."""
        try:
            collection = self._store_manager.collection
            store = self._store_manager.store
        except RuntimeError as exc:
            # B10：旧实现只把 collection 包进 try，store 在 try 外抛错——日志承诺的
            # "MilvusClient 不可用则走纯稠密"从未发生。集合未加载时显式报可读错误。
            raise RuntimeError(f"知识库集合未加载，无法构建问答链: {exc}") from exc

        self._qa_chain = build_chain(
            vector_store=store,
            llm=self._llm,
            settings=self._settings,
            collection=collection,
            embeddings=self._embeddings,
            reranker_class=SiliconFlowReranker,
        )
