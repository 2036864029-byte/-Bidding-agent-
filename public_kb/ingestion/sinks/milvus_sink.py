# 功能：校验并写入 Milvus，完成稠密/稀疏向量化入库和索引构建。
"""Milvus sink for initialized and incremental ingestion.

E-9（P5）upsert 语义：Milvus 主键 ``id`` 为 auto_id 自增，不可按 chunk_uid 直接
upsert；本 sink 依赖 ``MilvusStoreManager.add_documents`` 的 **chunk_uid 幂等判重**
（批内去重 + ``_query_existing_uids`` 跳过已存在 uid）实现"upsert by chunk_uid"——
chunk 内容为不可变派生，跳过式写入等价于按内容 upsert 且避免 delete+reinsert churn。
同一批文本块重复导入 → 全部跳过（返回 0），向量数不变（P5 验收判据）。
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Sequence

from langchain_core.documents import Document

from core.contracts import validate_ingestion_documents


MilvusSinkMode = Literal["initialize", "append"]


class MilvusSink:
    """Persist validated documents through a MilvusStoreManager."""

    def __init__(self, manager: Any, *, mode: MilvusSinkMode = "append") -> None:
        self._manager = manager
        self._mode = mode

    def write(
        self,
        documents: Sequence[Document],
        *,
        records: Sequence[Dict[str, Any]],
    ) -> int:
        validated = validate_ingestion_documents(documents)
        if self._mode == "initialize":
            return self._manager.initialize_collection(validated)
        return self._manager.add_documents(validated)
