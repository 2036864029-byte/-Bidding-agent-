# 功能：管理 Milvus 集合 schema、索引、连接、加载和写入。
"""Milvus 向量存储管理器。

约束：
  - 仅使用 pymilvus 3.x 的 MilvusClient 管理 collection 生命周期
  - 保留 langchain_milvus 包装器，供上层兼容现有 similarity_search 接口
  - 服务端 BM25 仅负责自动生成 sparse 内容；Schema、Function 和索引由本模块声明
"""

from __future__ import annotations

import logging
from concurrent.futures import as_completed
from typing import Any, List, Optional, Sequence, Tuple

from langchain_core.documents import Document
from langchain_milvus import Milvus as MilvusVectorStore
from langchain_openai import OpenAIEmbeddings
from pymilvus import DataType, Function, FunctionType, MilvusClient

from core.concurrency.pools import get_embedding_pool
from core.config.settings import Settings
from core.contracts import (
    ConfigurationContractError,
    IngestionContractError,
    MilvusCollectionContract,
    validate_embedding_batch,
    validate_ingestion_documents,
)
from core.contracts.chunk_ids import compute_chunk_uid
from core.vectorstore.index_params import build_dense_index_params

logger = logging.getLogger(__name__)

# 显式声明标量的 Schema 上限：超出会被服务端整批拒绝，故入库前按此截断/校验
_CHAPTER_MAX_LEN = 512
_PARENT_CHUNK_ID_MAX_LEN = 64


def _truncate_field(value: Any, max_length: int, field_name: str) -> str:
    """按 Schema 上限截断显式声明字段，避免整条 insert 被服务端拒绝。"""
    text = "" if value is None else str(value)
    if len(text) > max_length:
        logger.warning(
            "字段 %s 超长 (%d > %d)，已截断: %s…",
            field_name, len(text), max_length, text[:32],
        )
        return text[:max_length]
    return text


class MilvusStoreManager:
    """管理公共知识库集合的 Schema、索引、批量入库与清空。"""

    def __init__(
        self,
        settings: Settings,
        embeddings: OpenAIEmbeddings,
        *,
        client: Optional[Any] = None,
    ) -> None:
        self._settings = settings
        self._embeddings = embeddings
        self._store: Optional[MilvusVectorStore] = None
        self._uri = settings.resolved_milvus_uri
        self._connection_args = self._build_connection_args()
        self._client = client or MilvusClient(**self._connection_args)
        self._contract = MilvusCollectionContract()
        self._contract.validate()

    def initialize_collection(
        self,
        documents: Sequence[Document],
        *,
        recreate: bool = False,
    ) -> int:
        """创建集合并批量导入文档。

        默认不覆盖同名集合。只有显式传入 ``recreate=True`` 且集合名符合
        实验前缀时才允许删除，防止误删当前 ``public_kb``。
        返回实际写入行数（M2：启用去重后可能小于 len(validated)）。
        """
        validated = validate_ingestion_documents(documents)
        collection_name = self._settings.collection_name
        logger.info("初始化集合 %s，待入库 %d 个文档块", collection_name, len(validated))

        if self._has_collection():
            if not recreate:
                raise ConfigurationContractError(
                    f"集合 {collection_name} 已存在；默认禁止覆盖，请使用新的实验集合名"
                )
            self._assert_recreate_is_safe()
            self._client.drop_collection(collection_name)
            logger.info("已删除实验集合: %s", collection_name)

        schema = self._build_schema()
        index_params = self._build_index_params()
        self._client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )
        self._validate_collection_contract()
        self._client.load_collection(collection_name)

        inserted = self._batch_insert(validated)
        self._client.flush(collection_name)
        self._store = self._create_vector_store_wrapper()
        logger.info(
            "集合 %s 入库完成，共 %d 条记录（去重跳过 %d 条）",
            collection_name, inserted, len(validated) - inserted,
        )
        return inserted

    def _build_schema(self) -> Any:
        """构建 dense-only 或 dense+BM25 的集合 Schema。

        两层字段结构：
          - 核心字段（id/text/vector[+sparse]）固定；
          - 通用标量字段（doc_id/doc_type/doc_name/chapter/hierarchy_path/
            chunk_uid/chunk_index）显式声明——可建标量索引、可过滤；
          - 类型化扩展字段（article_number/question 及未来字段）走动态字段，
            新增 doc_type 零迁移。
        """
        schema = self._client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field(
            field_name=self._contract.primary_field,
            datatype=DataType.INT64,
            is_primary=True,
            auto_id=True,
        )
        text_options = {
            "field_name": self._contract.text_field,
            "datatype": DataType.VARCHAR,
            "max_length": 65535,
        }
        if self._settings.enable_bm25:
            text_options["enable_analyzer"] = True
            text_options["analyzer_params"] = {
                "type": self._settings.bm25_analyzer_type,
            }
        schema.add_field(**text_options)
        schema.add_field(
            field_name=self._contract.dense_field,
            datatype=DataType.FLOAT_VECTOR,
            dim=self._settings.embedding_dim,
        )

        # 通用标量字段（显式声明以便建索引/过滤）
        schema.add_field(field_name="chunk_uid", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="doc_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="doc_type", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="doc_name", datatype=DataType.VARCHAR, max_length=255)
        schema.add_field(field_name="chapter", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="hierarchy_path", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
        # 父子块字段：显式声明——parent_chunk_id 是分组检索的分组键，落动态字段会被静默忽略
        schema.add_field(
            field_name="parent_chunk_id",
            datatype=DataType.VARCHAR,
            max_length=_PARENT_CHUNK_ID_MAX_LEN,
        )
        schema.add_field(field_name="chunk_type", datatype=DataType.VARCHAR, max_length=16)
        schema.add_field(field_name="sequence_in_parent", datatype=DataType.INT64)

        if self._settings.enable_bm25:
            schema.add_field(
                field_name=self._contract.sparse_field,
                datatype=DataType.SPARSE_FLOAT_VECTOR,
            )
            schema.add_function(Function(
                name=self._contract.bm25_function_name,
                input_field_names=[self._contract.text_field],
                output_field_names=[self._contract.sparse_field],
                function_type=FunctionType.BM25,
            ))
        return schema

    def _build_index_params(self) -> Any:
        """构建 dense 索引，并在启用时追加 BM25 sparse 索引 + 标量过滤索引。"""
        index_params = self._client.prepare_index_params()
        dense_params = build_dense_index_params(self._settings)
        index_params.add_index(
            field_name=self._contract.dense_field,
            index_type=self._settings.milvus_dense_index_type,
            metric_type=self._contract.dense_metric,
            params=dense_params,
        )
        if self._settings.enable_bm25:
            index_params.add_index(
                field_name=self._contract.sparse_field,
                index_type=self._settings.milvus_sparse_index_type,
                metric_type=self._contract.sparse_metric,
                params={
                    "inverted_index_algo": "DAAT_MAXSCORE",
                    "bm25_k1": self._settings.bm25_k1,
                    "bm25_b": self._settings.bm25_b,
                },
            )
        # 标量索引：chunk_uid（幂等判重查询）+ doc_type（按类型过滤检索）
        index_params.add_index(
            field_name="chunk_uid",
            index_type="INVERTED",
        )
        index_params.add_index(
            field_name="doc_type",
            index_type="INVERTED",
        )
        # 分组检索键 + 父子块过滤键
        index_params.add_index(
            field_name="parent_chunk_id",
            index_type="INVERTED",
        )
        index_params.add_index(
            field_name="chunk_type",
            index_type="INVERTED",
        )
        return index_params

    def _validate_collection_contract(self) -> None:
        """创建后校验关键字段与 BM25 Function，失败时禁止继续入库。"""
        info = self._client.describe_collection(self._settings.collection_name)
        field_names = {field.get("name", "") for field in info.get("fields", [])}
        required = {
            self._contract.primary_field,
            self._contract.text_field,
            self._contract.dense_field,
            # 通用标量 + 父子块字段：缺失会退化为动态字段，过滤/分组静默失效
            "chunk_uid", "doc_id", "doc_type", "doc_name", "chapter",
            "hierarchy_path", "chunk_index",
            "parent_chunk_id", "chunk_type", "sequence_in_parent",
        }
        if self._settings.enable_bm25:
            required.add(self._contract.sparse_field)
        missing = sorted(required - field_names)
        if missing:
            raise ConfigurationContractError(
                f"集合 Schema 缺少字段: {', '.join(missing)}"
            )

        if self._settings.enable_bm25:
            functions = info.get("functions", []) or []
            function_names = {item.get("name", "") for item in functions}
            if self._contract.bm25_function_name not in function_names:
                raise ConfigurationContractError(
                    f"集合缺少 BM25 Function: {self._contract.bm25_function_name}"
                )

        indexes = set(self._client.list_indexes(self._settings.collection_name))
        required_indexes = {
            self._contract.dense_field,
            "chunk_uid", "doc_type", "parent_chunk_id", "chunk_type",
        }
        if self._settings.enable_bm25:
            required_indexes.add(self._contract.sparse_field)
        missing_indexes = required_indexes - indexes
        if missing_indexes:
            raise ConfigurationContractError(
                f"集合缺少索引: {', '.join(sorted(missing_indexes))}"
            )

    def _batch_insert(self, documents: Sequence[Document]) -> int:
        """批量向量化并插入集合；不在客户端生成 BM25 sparse 内容。

        启用去重（settings.enable_dedup）时，逐批按 chunk_uid 判重：
          1. 批内先按 chunk_uid 去重（同一批出现相同 uid 只写一次）；
          2. 再查询集合中已存在的 uid（增量/幂等场景），命中跳过；
          3. 返回实际写入行数（`_batch_insert` 语义从"无返回"改为"返回写入数"）。
        关闭去重时行为与旧版完全一致（全量写）。

        稠密向量生成使用 ThreadPoolExecutor 并发，Milvus insert 仍单线程串行，
        避免客户端并发问题（settings.embedding_max_workers 控制并发度）。
        """
        batch_size = self._settings.embedding_batch_size
        total = len(documents)
        inserted = 0
        skipped = 0

        # 批内按 chunk_uid 去重
        to_write = list(documents)
        if self._settings.enable_dedup:
            unique: list[Document] = []
            seen: set[str] = set()
            for doc in to_write:
                uid = str(
                    doc.metadata.get("chunk_uid")
                    or compute_chunk_uid(doc.page_content, doc.metadata)
                )
                if uid in seen:
                    skipped += 1
                    continue
                seen.add(uid)
                unique.append(doc)
            to_write = unique

        if not to_write:
            logger.info("无待写入文档")
            return 0

        batches: List[List[Document]] = [
            to_write[i:i + batch_size] for i in range(0, len(to_write), batch_size)
        ]
        batch_vectors: dict[int, List[List[float]]] = {}

        def _embed_one(index_batch: Tuple[int, List[Document]]) -> Tuple[int, List[List[float]]]:
            idx, batch = index_batch
            texts = [doc.page_content for doc in batch]
            vectors = self._embeddings.embed_documents(texts)
            return idx, vectors

        # W2-5：向量化线程池集中到 core.concurrency.pools（进程级复用）
        executor = get_embedding_pool()
        futures = [
            executor.submit(_embed_one, (idx, batch))
            for idx, batch in enumerate(batches)
        ]
        for future in as_completed(futures):
            idx, vectors = future.result()
            batch_vectors[idx] = vectors

        for idx, batch in enumerate(batches):
            vectors = batch_vectors[idx]
            validate_embedding_batch(batch, vectors, self._settings.embedding_dim)
            data = self._build_records(batch, vectors)

            if self._settings.enable_dedup:
                # 查询集合中已存在的 uid，命中跳过（幂等/增量场景）
                existing = self._query_existing_uids(
                    [row["chunk_uid"] for row in data]
                )
                deduped_data = [
                    row for row in data if row["chunk_uid"] not in existing
                ]
                skipped += len(data) - len(deduped_data)
                data = deduped_data
                if not data:
                    continue

            result = self._client.insert(self._settings.collection_name, data)
            insert_count = self._extract_insert_count(result, len(data))
            if insert_count != len(data):
                raise IngestionContractError(
                    f"批次期望写入 {len(data)} 条，服务端确认 {insert_count} 条"
                )
            inserted += insert_count
            logger.debug("入库进度: %d/%d", inserted, len(to_write))

        if skipped:
            logger.info("去重跳过 %d 条重复文本块", skipped)
        logger.info("入库完成: %d 条记录已写入", inserted)
        return inserted

    def _query_existing_uids(self, uids: Sequence[str]) -> set[str]:
        """按 chunk_uid 批量查询集合中已存在的 uid（用于幂等/增量去重）。

        每次调用前先对 uid 去重并限制单次查询规模，避免表达式过长。
        查询失败（如集合尚未就绪）时按空集处理——去重是尽力而为的
        优化，不应阻断入库主流程。
        """
        if not uids:
            return set()
        existing: set[str] = set()
        unique_uids = sorted(set(uids))
        # Milvus expr 长度/性能考虑：分批查询，每批 200 个 uid
        chunk_size = 200
        for start in range(0, len(unique_uids), chunk_size):
            part = unique_uids[start:start + chunk_size]
            expr = f"chunk_uid in {list(part)}"
            try:
                rows = self._client.query(
                    collection_name=self._settings.collection_name,
                    filter=expr,
                    output_fields=["chunk_uid"],
                )
            except Exception as exc:
                logger.warning("chunk_uid 判重查询失败（%s），按未命中处理", exc)
                continue
            for row in rows or []:
                uid = row.get("chunk_uid")
                if uid is not None:
                    existing.add(str(uid))
        return existing

    def _build_records(
        self,
        documents: Sequence[Document],
        vectors: Sequence[Sequence[float]],
    ) -> List[dict]:
        """将 Document 与 dense vectors 转换为 Milvus row records。"""
        validate_embedding_batch(documents, vectors, self._settings.embedding_dim)
        data: List[dict] = []
        for doc, vector in zip(documents, vectors):
            parent_chunk_id = str(doc.metadata.get("parent_chunk_id") or "")
            if len(parent_chunk_id) > _PARENT_CHUNK_ID_MAX_LEN:
                raise IngestionContractError(
                    f"parent_chunk_id 超长 ({len(parent_chunk_id)} > "
                    f"{_PARENT_CHUNK_ID_MAX_LEN}): {parent_chunk_id[:32]}…"
                )
            record = {
                self._contract.text_field: doc.page_content,
                self._contract.dense_field: list(vector),
                "doc_name": doc.metadata.get("doc_name", ""),
                "doc_id": doc.metadata.get("doc_id", ""),
                "doc_type": doc.metadata.get("doc_type", ""),
                "chapter": _truncate_field(
                    doc.metadata.get("chapter"), _CHAPTER_MAX_LEN, "chapter"
                ),
                "hierarchy_path": _truncate_field(
                    doc.metadata.get("hierarchy_path"),
                    _CHAPTER_MAX_LEN,
                    "hierarchy_path",
                ),
                "chunk_index": doc.metadata.get("chunk_index", 0),
                "chunk_uid": doc.metadata.get("chunk_uid")
                or compute_chunk_uid(doc.page_content, doc.metadata),
                "schema_version": self._settings.collection_schema_version,
                "embedding_model": self._settings.embedding_model,
                # 父子块字段（向后兼容：旧数据无该字段时默认 atom）
                "chunk_type": doc.metadata.get("chunk_type") or "atom",
                "parent_chunk_id": parent_chunk_id,
                "sequence_in_parent": doc.metadata.get("sequence_in_parent", 0),
                # 法条时效性（任务 M3）：施行日期 / 效力状态（可空，向后兼容）
                "effective_date": doc.metadata.get("effective_date") or "",
                "status": doc.metadata.get("status") or "",
            }
            for key, value in doc.metadata.items():
                if key in (
                    "doc_name", "doc_id", "doc_type", "chapter",
                    "hierarchy_path", "chunk_index", "chunk_uid",
                    "chunk_type", "parent_chunk_id", "sequence_in_parent",
                    "effective_date", "status",
                ):
                    continue
                if key.startswith("_") or value is None:
                    continue
                if isinstance(value, str) and len(value) > 65535:
                    value = value[:65535]
                record[key] = value
            data.append(record)
        return data

    def add_documents(self, documents: Sequence[Document], *, flush: bool = True) -> int:
        """增量导入文档。

        启用去重时幂等：重复导入同一批文本块会因 chunk_uid 已存在而全部
        跳过（返回 0）；返回实际写入行数。

        flush=False 用于大批量导入：跳过逐批落盘（Milvus flush 每次可达数秒），
        由调用方在整批完成后统一 flush 一次。崩溃恢复依赖进度文件 + chunk_uid
        判重兜底，不丢失正确性。
        """
        validated = validate_ingestion_documents(documents)
        if not self._has_collection():
            logger.info("集合不存在，转为全量初始化")
            return self.initialize_collection(validated)
        self._validate_collection_contract()
        if self._store is None:
            self.load_existing()

        logger.info("增量导入 %d 个文档块", len(validated))
        inserted = self._batch_insert(validated)
        if flush:
            self._client.flush(self._settings.collection_name)
        logger.info(
            "增量导入完成 %d 条（跳过 %d 条），当前集合总数约 %d",
            inserted,
            len(validated) - inserted,
            int(self._client.get_collection_stats(self._settings.collection_name).get("row_count", 0) or 0),
        )
        return inserted

    @property
    def collection(self) -> Any:
        """获取底层 MilvusClient 实例。"""
        if not self._has_collection():
            raise RuntimeError(
                f"集合 {self._settings.collection_name} 未初始化，请先初始化或加载"
            )
        return self._client

    def load_existing(self) -> bool:
        """加载并校验已存在的集合。"""
        if not self._has_collection():
            return False
        self._validate_collection_contract()
        self._client.load_collection(self._settings.collection_name)
        self._store = self._create_vector_store_wrapper()
        logger.info("已加载现有集合: %s", self._settings.collection_name)
        return True

    def clear_collection(self) -> None:
        """清空实验集合；生产集合不允许经此接口删除。"""
        if not self._has_collection():
            self._store = None
            return
        self._assert_recreate_is_safe()
        self._client.drop_collection(self._settings.collection_name)
        self._store = None
        logger.info("实验集合 %s 已清空", self._settings.collection_name)

    @property
    def store(self) -> MilvusVectorStore:
        """获取底层 MilvusVectorStore 实例。"""
        if self._store is None:
            raise RuntimeError(
                f"集合 {self._settings.collection_name} 未初始化，请先初始化或加载"
            )
        return self._store

    def _create_vector_store_wrapper(self) -> MilvusVectorStore:
        return MilvusVectorStore(
            embedding_function=self._embeddings,
            collection_name=self._settings.collection_name,
            connection_args=self._connection_args.copy(),
            auto_id=True,
            vector_field=self._contract.dense_field,
            text_field=self._contract.text_field,
            primary_field=self._contract.primary_field,
        )

    def _build_connection_args(self) -> dict:
        args: dict = {
            "uri": self._uri,
            "timeout": self._settings.milvus_timeout,
        }
        if self._settings.milvus_token:
            args["token"] = self._settings.milvus_token
        return args

    def _assert_recreate_is_safe(self) -> None:
        name = self._settings.collection_name
        if name == self._settings.milvus_protected_collection:
            raise ConfigurationContractError(
                f"拒绝删除生产集合 {name!r}"
            )
        if not name.startswith(self._settings.milvus_experiment_prefix):
            raise ConfigurationContractError(
                f"拒绝删除非实验集合 {name!r}；允许前缀为 "
                f"{self._settings.milvus_experiment_prefix!r}"
            )

    def _has_collection(self) -> bool:
        return bool(self._client.has_collection(self._settings.collection_name))

    @staticmethod
    def _extract_insert_count(result: Any, fallback: int) -> int:
        if result is None:
            return fallback
        if isinstance(result, dict):
            for key in ("insert_count", "insertCount"):
                if key in result:
                    return int(result[key])
        for key in ("insert_count", "insertCount"):
            value = getattr(result, key, None)
            if value is not None:
                return int(value)
        return fallback
