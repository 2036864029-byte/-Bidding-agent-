# 功能：在线混合检索编排器，串联稠密/稀疏、RRF、阈值过滤和重排。
"""Hybrid retrieval orchestration."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from langchain_core.documents import Document
from langchain_milvus import Milvus as MilvusVectorStore
from pymilvus import AnnSearchRequest, RRFRanker

from core.config.settings import Settings
from core.contracts import (
    MilvusCollectionContract,
    RerankerStatus,
    RetrievalDiagnostics,
    RetrievalMode,
    build_effective_expr,
    validate_question,
)
from core.vectorstore.index_params import build_dense_search_params

from .entities import entity_to_doc, normalize_hit_entity
from .fallback import dense_only_retrieve
from .milvus_search import hybrid_search_with_full_fields
from .parent_assembly import assemble_parent_contexts, is_duplicate_parent
from .reranker import Reranker
from .strategies import adaptive_threshold

logger = logging.getLogger(__name__)


class HybridRetrievalError(RuntimeError):
    """严格验证模式下，混合检索未按契约执行。"""


@dataclass(frozen=True)
class RetrievalResult:
    """一次检索的最终文档与诊断结果。"""

    docs: List[Tuple[Document, float]]
    diagnostics: RetrievalDiagnostics


class HybridRetriever:
    """编排 dense、服务端 BM25、RRF、Reranker 与降级路径。"""

    def __init__(
        self,
        *,
        vector_store: MilvusVectorStore,
        collection: Optional[Any],
        embeddings: Optional[Any],
        settings: Settings,
        reranker: Reranker,
    ) -> None:
        self._vector_store = vector_store
        self._collection = collection
        self._embeddings = embeddings
        self._settings = settings
        self._reranker = reranker
        self._has_sparse_cache: Optional[bool] = None

    def retrieve(self, raw_question: str) -> RetrievalResult:
        """检索入口：结果后统一补父块上下文（hybrid 与 dense 降级路径都覆盖）。"""
        started = time.perf_counter()
        result = self._retrieve_impl(raw_question)
        return self._attach_parent_context(result, started)

    def _retrieve_impl(self, raw_question: str) -> RetrievalResult:
        """稠密 COSINE + 服务端 BM25 → RRF → 可选 Reranker。"""
        question = validate_question(raw_question)
        expr = self._effective_expr()

        if self._collection is None or self._embeddings is None:
            logger.info("未提供原生 collection/embeddings，使用纯稠密检索")
            return self._dense_fallback(
                question,
                "native_collection_or_embeddings_missing",
                expr=expr,
            )

        dense_vec: Optional[List[float]] = None
        try:
            dense_vec = self._embeddings.embed_query(question)

            if self._has_sparse_cache is None:
                collection_info = self._collection.describe_collection(
                    self._settings.collection_name,
                )
                field_names = {
                    field.get("name", "")
                    for field in collection_info.get("fields", [])
                }
                self._has_sparse_cache = "sparse_vector" in field_names

            if not self._has_sparse_cache:
                reason = "sparse_vector_field_missing"
                if self._settings.strict_hybrid_validation:
                    raise HybridRetrievalError(reason)
                logger.info("当前 Schema 无稀疏向量字段，使用纯稠密检索")
                return self._dense_fallback(question, reason, dense_vec, expr)

            return self._hybrid_retrieve(question, dense_vec, expr)
        except HybridRetrievalError:
            raise
        except Exception as error:
            if self._settings.strict_hybrid_validation:
                raise HybridRetrievalError(str(error)) from error
            logger.warning("混合检索异常 (%s)，回退到纯稠密检索", error)
            return self._dense_fallback(
                question,
                f"hybrid_error:{type(error).__name__}",
                dense_vec,
                expr,
            )

    def _effective_expr(self) -> Optional[str]:
        """按开关生成法条时效过滤表达式；关闭时返回 None（不过滤）。"""
        if not self._settings.enable_effective_filter:
            return None
        from datetime import date
        return build_effective_expr(date.today())

    def _rerank_candidate_k(self) -> int:
        """rerank 取回条数：同父去重会丢弃部分候选，故分组开启时多取再截断。"""
        top_k = self._settings.retrieval_top_k
        if not self._settings.retrieval_group_by_parent:
            return top_k
        return top_k * max(1, self._settings.retrieval_group_size)

    def _is_duplicate_parent(self, doc: Document, seen: set) -> bool:
        """同父兄弟子块兜底去重（服务端分组静默失效时的护栏）。"""
        if not self._settings.retrieval_group_by_parent:
            return False
        return is_duplicate_parent(doc, seen)

    def _attach_parent_context(
        self, result: RetrievalResult, started: Optional[float] = None
    ) -> RetrievalResult:
        """命中子块拼回完整父块（写入 metadata["context_text"]，不改 page_content）。"""
        if (
            not self._settings.retrieval_parent_assembly
            or self._collection is None
            or not result.docs
        ):
            return result
        try:
            docs = [doc for doc, _ in result.docs]
            assemble_parent_contexts(
                docs,
                collection=self._collection,
                collection_name=self._settings.collection_name,
                max_chars=self._settings.retrieval_parent_max_chars,
            )
            self._log_context_metrics(result, docs, started)
        except Exception as error:  # noqa: BLE001 - 拼接失败不应阻断检索
            logger.warning("父块拼接失败 (%s)，回退子块原文", error)
        return result

    def _log_context_metrics(
        self, result: RetrievalResult, docs: List[Document], started: Optional[float]
    ) -> None:
        """G7 埋点：候选数 / 返回数 / 拼接父块数 / 上下文总长 / 端到端耗时。"""
        assembled = sum(1 for doc in docs if doc.metadata.get("context_text"))
        total_chars = sum(
            len(doc.metadata.get("context_text") or doc.page_content) for doc in docs
        )
        elapsed_ms = (
            round((time.perf_counter() - started) * 1000) if started is not None else -1
        )
        logger.info(
            "检索上下文埋点: 候选=%s 条, 返回=%d 条, 拼接父块=%d 条, 总字符=%d, 耗时=%dms",
            result.diagnostics.fusion_count, len(docs), assembled, total_chars, elapsed_ms,
        )
        if total_chars > self._settings.retrieval_context_warn_chars:
            logger.warning(
                "单轮检索上下文总长 %d 字符，超过告警阈值 %d（未截断，请关注 LLM 上下文预算）",
                total_chars, self._settings.retrieval_context_warn_chars,
            )

    def _hybrid_retrieve(
        self,
        question: str,
        dense_vec: List[float],
        expr: Optional[str] = None,
    ) -> RetrievalResult:
        contract = MilvusCollectionContract()
        dense_req = AnnSearchRequest(
            data=[dense_vec],
            anns_field=contract.dense_field,
            param=build_dense_search_params(self._settings),
            limit=self._settings.hybrid_dense_limit,
        )
        sparse_req = AnnSearchRequest(
            data=[question],
            anns_field=contract.sparse_field,
            param={"metric_type": contract.sparse_metric, "params": {}},
            limit=self._settings.hybrid_sparse_limit,
        )

        raw_hits = hybrid_search_with_full_fields(
            self._collection,
            self._settings,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=self._settings.rrf_k),
            limit=self._settings.hybrid_fusion_limit,
            expr=expr,
            group_by_field=(
                "parent_chunk_id" if self._settings.retrieval_group_by_parent else None
            ),
            group_size=self._settings.retrieval_group_size,
        )
        candidates: List[Tuple[str, float, dict]] = []
        for hit in raw_hits:
            entity = normalize_hit_entity(hit.entity)
            candidates.append((str(entity.get("text", "")), hit.score, entity))

        if not candidates:
            return RetrievalResult(
                docs=[],
                diagnostics=RetrievalDiagnostics(
                    retrieval_mode=RetrievalMode.REFUSAL,
                    dense_count=self._settings.hybrid_dense_limit,
                    sparse_count=self._settings.hybrid_sparse_limit,
                ),
            )

        reranked, reranker_status = self._reranker.rerank(
            query=question,
            documents=[candidate[0] for candidate in candidates],
            top_k=self._rerank_candidate_k(),
        )

        if reranker_status is RerankerStatus.SUCCESS and reranked:
            return self._rerank_result(reranked, candidates)

        # Reranker 失败或成功但无结果时保留 RRF 排序，不构造虚假相关度分数。
        results: List[Tuple[Document, float]] = []
        seen_parents: set = set()
        for _, rrf_score, entity in candidates:
            doc = entity_to_doc(entity, rrf_score)
            if self._is_duplicate_parent(doc, seen_parents):
                continue
            doc.metadata["rrf_score"] = round(rrf_score, 4)
            doc.metadata["score_type"] = "rrf"
            results.append((doc, rrf_score))
            if len(results) >= self._settings.retrieval_top_k:
                break
        return RetrievalResult(
            docs=results,
            diagnostics=RetrievalDiagnostics(
                retrieval_mode=RetrievalMode.HYBRID_RRF,
                dense_count=self._settings.hybrid_dense_limit,
                sparse_count=self._settings.hybrid_sparse_limit,
                fusion_count=len(candidates),
                reranker_status=reranker_status,
                # B7：区分"reranker 挂了"与"reranker 正常但没返回结果"
                fallback_reason=(
                    "reranker_empty"
                    if reranker_status is RerankerStatus.SUCCESS
                    else "reranker_failed"
                ),
            ),
        )

    def _rerank_result(
        self,
        reranked: List[dict],
        candidates: List[Tuple[str, float, dict]],
    ) -> RetrievalResult:
        threshold = adaptive_threshold(
            reranked[0]["relevance_score"],
            settings=self._settings,
        )
        results: List[Tuple[Document, float]] = []
        seen_parents: set = set()
        for item in reranked:
            score = float(item["relevance_score"])
            index = int(item["index"])
            if score < threshold or not (0 <= index < len(candidates)):
                continue
            _, rrf_score, entity = candidates[index]
            doc = entity_to_doc(entity, score)
            doc.metadata["rrf_score"] = round(rrf_score, 4)
            doc.metadata["score_type"] = "reranker"
            if self._is_duplicate_parent(doc, seen_parents):
                continue
            results.append((doc, score))
            if len(results) >= self._settings.retrieval_top_k:
                break
        return RetrievalResult(
            docs=results,
            diagnostics=RetrievalDiagnostics(
                retrieval_mode=RetrievalMode.HYBRID_RERANK,
                dense_count=self._settings.hybrid_dense_limit,
                sparse_count=self._settings.hybrid_sparse_limit,
                fusion_count=len(candidates),
                reranker_status=RerankerStatus.SUCCESS,
                threshold=threshold,
            ),
        )

    def _dense_fallback(
        self,
        question: str,
        reason: str,
        dense_vec: Optional[List[float]] = None,
        expr: Optional[str] = None,
    ) -> RetrievalResult:
        docs = dense_only_retrieve(
            question,
            self._vector_store,
            self._settings,
            self._collection,
            self._embeddings,
            dense_vec=dense_vec,
            expr=expr,
        )
        return RetrievalResult(
            docs=docs,
            diagnostics=RetrievalDiagnostics(
                retrieval_mode=(
                    RetrievalMode.DENSE_NATIVE
                    if self._collection is not None and self._embeddings is not None
                    else RetrievalMode.DENSE_LANGCHAIN
                ),
                dense_count=len(docs),
                fallback_reason=reason,
            ),
        )
