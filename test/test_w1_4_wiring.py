"""Wave 1 / W1-4 未接线机制处理验证。

- embedding 配置校验：构造 PublicKnowledgeRAG / 构建工具检索器时接线。
- 降级指标：HybridRetriever 降级时记录 record_recall_fallback。
- with_guardrails：已删除（零生产调用），由 test_legacy_path_allowlist 守卫防回潮。
"""

from __future__ import annotations

import pytest

from core.config.settings import Settings
from core.contracts import ConfigurationContractError

# ── embedding 配置校验接线 ────────────────────────────────────

def test_rag_init_rejects_embedding_mismatch():
    """换 embedding 模型未重建集合 → 构造即拒绝（此前校验函数零调用）。"""
    from public_kb.engine import PublicKnowledgeRAG

    settings = Settings(embedding_model="other-model-not-bge-m3")

    with pytest.raises(ConfigurationContractError):
        PublicKnowledgeRAG(settings)


def test_build_retriever_maps_config_error_to_runtime_error(monkeypatch):
    """工具路径把契约错误转成 RuntimeError，由节点按"知识库不可用"引导。"""
    import core.config.settings as cs
    import public_kb.tools as tools_mod

    original = cs.Settings
    monkeypatch.setattr(
        cs, "Settings",
        lambda *a, **k: original(embedding_model="other-model-not-bge-m3"),
    )

    with pytest.raises(RuntimeError, match="embedding 配置与集合约定不一致"):
        tools_mod._build_retriever()


# ── 降级指标接线 ──────────────────────────────────────────────

def test_dense_fallback_records_metric(monkeypatch):
    import public_kb.retrieval.retriever as retriever_mod
    from public_kb.retrieval.retriever import HybridRetriever

    recorded: list[str] = []
    monkeypatch.setattr(
        retriever_mod, "record_recall_fallback",
        lambda layer: recorded.append(layer),
    )

    retriever = HybridRetriever(
        vector_store=None, collection=None, embeddings=None,
        settings=Settings(), reranker=None,
    )
    retriever._dense_fallback("招标方式有哪些？", "native_collection_or_embeddings_missing")

    assert recorded == ["native_collection_or_embeddings_missing"]
