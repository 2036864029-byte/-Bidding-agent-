"""core.contracts — 共享契约层（方案 Phase 6 / A-8 上提，解 R1 反向依赖）。

chunk 身份（chunk_uid）与知识库契约自 public_kb 上提，供 core/vectorstore 与 RAG
两侧共用；`public_kb/` 侧旧路径兼容壳已随 P6 结构收敛删除。
"""

from .chunk_ids import compute_chunk_uid, compute_text_hash, normalize_chunk_text
from .contracts import (
    ConfigurationContractError,
    IngestionContractError,
    KnowledgeBaseContractError,
    MilvusCollectionContract,
    RetrievalContractError,
    RetrievalDiagnostics,
    RetrievalMode,
    RerankerStatus,
    build_effective_expr,
    validate_embedding_batch,
    validate_ingestion_documents,
    validate_qa_result,
    validate_question,
)

__all__ = [
    # chunk_ids
    "normalize_chunk_text",
    "compute_text_hash",
    "compute_chunk_uid",
    # contracts
    "KnowledgeBaseContractError",
    "ConfigurationContractError",
    "IngestionContractError",
    "RetrievalContractError",
    "RetrievalMode",
    "RerankerStatus",
    "MilvusCollectionContract",
    "RetrievalDiagnostics",
    "validate_question",
    "validate_ingestion_documents",
    "validate_embedding_batch",
    "validate_qa_result",
    "build_effective_expr",
]
