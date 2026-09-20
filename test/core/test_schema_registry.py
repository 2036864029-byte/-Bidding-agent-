"""public_kb.schema_registry — 集合登记与 embedding 配置校验（方案 Phase 0 / A-11）。"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from core.config.settings import Settings
from core.vectorstore.namespace import KB_PUBLIC_COLLECTION, PRICE_SEMANTIC_COLLECTION
from public_kb.schema_registry import (
    get_collection_meta,
    get_registered_collections,
    validate_embedding_config,
)


def test_both_collections_registered_with_default_names():
    assert get_registered_collections() == [
        KB_PUBLIC_COLLECTION,
        PRICE_SEMANTIC_COLLECTION,
    ]
    for name in (KB_PUBLIC_COLLECTION, PRICE_SEMANTIC_COLLECTION):
        meta = get_collection_meta(name)
        assert meta["embedding_dim"] == 1024
        assert meta["metric"] == "COSINE"
    # 2026-09-08 重构：public_kb（RAG chunk）dense 索引迁 HNSW；price 语义集合保持 IVF_FLAT
    assert get_collection_meta(KB_PUBLIC_COLLECTION)["dense_index_type"] == "HNSW"
    assert get_collection_meta(PRICE_SEMANTIC_COLLECTION)["dense_index_type"] == "IVF_FLAT"


def test_unknown_collection_returns_none():
    assert get_collection_meta("no_such_collection") is None


def test_default_settings_pass_validation():
    """P0：默认配置下校验应通过，不改变行为。"""
    assert validate_embedding_config(Settings()) == []


def test_dim_mismatch_detected():
    from core.config.settings import Settings as S

    s = S(embedding_dim=768)
    assert validate_embedding_config(s) != []
