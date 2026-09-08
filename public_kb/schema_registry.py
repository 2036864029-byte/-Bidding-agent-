# 功能：Milvus 集合 schema / 索引 / embedding 版本注册表。
"""集合 schema 注册表（Phase 0 / A-11）。

集中登记两个 Milvus 集合的 schema 与 embedding 版本约定，供入库/检索前
校验"模型/维度/索引变更需重建集合"，防换模型翻车。

登记内容：
  - public_kb             （法规文本块，embedding 由 Settings.embedding_model 决定）
  - mysql_price_semantic  （MySQL 结构化数据语义集合；字段随 MySQL 表结构动态生成，
                           本登记只声明 embedding 空间约定——字段级 schema 在 P3
                           price_engine 下沉、集合统一到 core/vectorstore 后补齐）

接线范围（忠于现状，P0 收敛）：
  - 本模块先提供 *校验函数* 与 *单一登记表*（解集合名/维度/索引参数的重复维护）；
  - ingestion / retriever 启动期的强校验接线推迟到 P3——届时 collection 生命周期
    统一到 core/vectorstore 客户端，避免在 P0 扩大 ingestion/retrieval 行为面。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from core.config.settings import Settings
from core.embeddings.bge_m3 import EMBEDDING_MODEL_ID, EMBEDDING_VERSION
from core.vectorstore.namespace import (
    KB_PUBLIC_COLLECTION,
    PRICE_SEMANTIC_COLLECTION,
)

logger = logging.getLogger(__name__)

# 向量空间约定（两集合共用 BGE-m3 1024 维）
EXPECTED_EMBEDDING_MODEL_ID = EMBEDDING_MODEL_ID  # "BAAI/bge-m3"
EXPECTED_EMBEDDING_VERSION = EMBEDDING_VERSION      # "bge-m3-v1"
EXPECTED_EMBEDDING_DIM = 1024

# 索引参数登记（2026-09-08 HNSW+BM25 重构后）：
#   - public_kb（RAG chunk）：dense=HNSW（COSINE），sparse=SPARSE_INVERTED_INDEX（BM25）
#   - mysql_price_semantic：仍 IVF_FLAT（price 链路独立建索引，未迁移）
INDEX_IVF_FLAT_NLIST = 128
INDEX_HNSW_M = 16
INDEX_HNSW_EF_CONSTRUCTION = 128
INDEX_METRIC = "COSINE"
INDEX_SPARSE_TYPE = "SPARSE_INVERTED_INDEX"

# 登记表：集合 → 元数据
COLLECTION_REGISTRY = {
    KB_PUBLIC_COLLECTION: {
        "description": "法规/招标知识文本块（PDF 切片后入库）",
        "embedding_model": EXPECTED_EMBEDDING_MODEL_ID,
        "embedding_version": EXPECTED_EMBEDDING_VERSION,
        "embedding_dim": EXPECTED_EMBEDDING_DIM,
        "dense_index_type": "HNSW",
        "sparse_index_type": INDEX_SPARSE_TYPE,
        "metric": INDEX_METRIC,
        "hnsw_m": INDEX_HNSW_M,
        "hnsw_ef_construction": INDEX_HNSW_EF_CONSTRUCTION,
        "nlist": INDEX_IVF_FLAT_NLIST,
        "schema_version": "public_kb_v2",
        "note": "字段级 schema 由 MilvusStoreManager / contracts 声明（A-8 随 chunk 契约上提 core 后统一）",
    },
    PRICE_SEMANTIC_COLLECTION: {
        "description": "MySQL 结构化数据（企业/处罚/标讯）语义召回向量",
        "embedding_model": EXPECTED_EMBEDDING_MODEL_ID,
        "embedding_version": EXPECTED_EMBEDDING_VERSION,
        "embedding_dim": EXPECTED_EMBEDDING_DIM,
        "dense_index_type": "IVF_FLAT",
        "metric": INDEX_METRIC,
        "nlist": INDEX_IVF_FLAT_NLIST,
        "schema_version": "mysql_price_semantic_v1",
        "note": "字段随 MySQL 表结构动态生成；P3 price_engine 下沉后补字段级 schema",
    },
}


def get_registered_collections() -> List[str]:
    """返回已登记集合名列表。"""
    return list(COLLECTION_REGISTRY.keys())


def validate_embedding_config(settings: Settings) -> List[str]:
    """校验当前 Settings 的 embedding 配置与登记空间约定是否一致。

    返回不一致项列表（空 = 一致）。不抛错——调用方（入库/检索）决定如何处理；
    默认配置下应为空列表（P0 不改变任何行为）。
    """
    mismatches: List[str] = []
    if settings.embedding_dim != EXPECTED_EMBEDDING_DIM:
        mismatches.append(
            f"embedding_dim={settings.embedding_dim} != 约定 {EXPECTED_EMBEDDING_DIM}"
        )
    # 模型允许 env 覆盖；若覆盖非 bge-m3 且非空，提示需重建集合
    if settings.embedding_model and settings.embedding_model != EXPECTED_EMBEDDING_MODEL_ID:
        mismatches.append(
            f"embedding_model={settings.embedding_model!r} != 约定 "
            f"{EXPECTED_EMBEDDING_MODEL_ID!r}（换模型需重建集合，勿沿用旧向量）"
        )
    return mismatches


def validate_embedding_config_strict(settings: Settings) -> None:
    """严格校验：不一致即抛 ConfigurationContractError。

    供未来 P3 接线（ingestion / retriever 启动时调用）。
    """
    from core.contracts import ConfigurationContractError  # noqa: PLC0415

    mismatches = validate_embedding_config(settings)
    if mismatches:
        raise ConfigurationContractError(
            "embedding 配置与登记空间约定不一致: " + "; ".join(mismatches)
        )


def get_collection_meta(collection_name: str) -> Optional[dict]:
    """按集合名取登记元数据（未登记返回 None）。"""
    return COLLECTION_REGISTRY.get(collection_name)
