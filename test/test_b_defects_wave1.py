"""Wave 1 / W1-5 B 类功能缺陷回归（B1/B2/B9/B10/B11/B12）。

每条对应审计中的一条缺陷；不触网络/DB。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from core.contracts import KnowledgeBaseContractError, validate_question
from price_engine.intent import _post_process_intent
from price_engine.models import HardFilters, SearchIntent

# ── B2：top_n 非法值不得让意图解析崩溃 ────────────────────────

def test_post_process_top_n_non_numeric_is_ignored():
    """LLM 输出 "top3"/"abc" 时旧实现 int() 抛 ValueError 并冒泡出解析函数。"""
    intent = SearchIntent(hard_filters=HardFilters(), top_n="top3")
    out = _post_process_intent(intent)
    assert out.top_n is None


def test_post_process_top_n_clamped():
    assert _post_process_intent(SearchIntent(hard_filters=HardFilters(), top_n=999)).top_n == 20
    assert _post_process_intent(SearchIntent(hard_filters=HardFilters(), top_n=0)).top_n == 1


# ── B1：聚合 SQL 不得引用不存在的 _score_ 列 ──────────────────

def test_aggregation_sql_does_not_order_by_missing_score_column(monkeypatch):
    import price_engine.queries as q

    captured: dict = {}

    class _FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            captured["sql"] = sql

        def fetchall(self):
            return []

    class _FakeConn:
        def cursor(self, *args, **kwargs):
            return _FakeCursor()

    monkeypatch.setattr(q, "_get_connection", lambda db: _FakeConn())
    monkeypatch.setattr(q, "_release_connection", lambda conn: None)

    intent = SearchIntent(
        hard_filters=HardFilters(successful_bidder="某某有限公司"),
        aggregation="max_amount",   # 无 sort_by → 旧实现拼出 ORDER BY `_score_` → MySQL 1054
    )
    q._query_bidding_aggregation(intent)

    assert "sql" in captured, "聚合 SQL 未构建"
    assert "`_score_`" not in captured["sql"]


# ── B9：空白问题给友好提示，而非冒泡成"功能暂不可用" ──────────

def test_validate_question_rejects_blank_with_contract_error():
    with pytest.raises(KnowledgeBaseContractError):
        validate_question("   ")


def test_subgraph_blank_question_friendly(monkeypatch):
    import agent.nodes.knowledge_qa.subgraph as sg

    def _boom(question):
        raise KnowledgeBaseContractError("question 不能为空")

    monkeypatch.setattr(sg, "kb_search", _boom)
    sub = sg.build_knowledge_qa_subgraph()
    out = sub.invoke({"messages": [HumanMessage(content="   ")]})

    assert "没有收到您的问题" in out["business_result"]["answer"]
    assert out["business_result"]["branch"] == "knowledge_qa"


# ── B12：kb_search 先钳制 top_k，再判空 ───────────────────────

def test_kb_search_clamps_top_k_zero(monkeypatch):
    import public_kb.tools as tools_mod

    doc = Document(
        page_content="第一条 内容。",
        metadata={"doc_name": "法A", "chapter": "第一章", "chunk_index": 0, "chunk_id": 1},
    )
    outcome = SimpleNamespace(
        docs=[(doc, 0.9)],
        diagnostics=SimpleNamespace(to_dict=lambda: {"mode": "fake"}),
    )
    monkeypatch.setattr(
        tools_mod, "_get_retriever",
        lambda: SimpleNamespace(retrieve=lambda q: outcome),
    )

    result = tools_mod.kb_search("招标方式有哪些？", top_k=0)

    assert len(result.chunks) == 1, "top_k=0 不应把有结果的检索截成空召回"


# ── B10：集合未加载时给出可读错误（旧实现的"纯稠密降级"不可达） ──

def test_build_qa_chain_raises_readable_error_when_collection_missing():
    from public_kb.engine import PublicKnowledgeRAG

    class _StoreManager:
        @property
        def collection(self):
            raise RuntimeError("集合未初始化")

    rag = PublicKnowledgeRAG.__new__(PublicKnowledgeRAG)
    rag._store_manager = _StoreManager()

    with pytest.raises(RuntimeError, match="知识库集合未加载"):
        rag._build_qa_chain()


# ── B11：clear_kb 必须让工具路径的 retriever 单例失效 ─────────

def test_clear_kb_resets_tool_retriever(monkeypatch):
    import public_kb.engine as engine_mod
    import public_kb.tools as tools_mod

    calls: list[int] = []
    monkeypatch.setattr(tools_mod, "reset_retriever", lambda: calls.append(1))

    class _StoreManager:
        def clear_collection(self):
            return None

    rag = engine_mod.PublicKnowledgeRAG.__new__(engine_mod.PublicKnowledgeRAG)
    rag._store_manager = _StoreManager()
    rag._qa_chain = object()

    rag.clear_kb()

    assert calls == [1], "清空集合后必须重置 kb_search 的 retriever 单例"
    assert rag._qa_chain is None


# ── W1-6：死配置不得回潮 ──────────────────────────────────────

def test_dead_config_fields_removed():
    """这三个字段全仓零读取（W1-6 已删）：chunk_max_parent_chars 由
    retrieval_parent_max_chars 取代，另两个 PDF 表格阈值从未接线。"""
    from core.config.settings import Settings

    settings = Settings()
    for name in (
        "chunk_max_parent_chars",
        "pdf_tiered_table_min_rows",
        "pdf_tiered_table_max_empty_ratio",
    ):
        assert not hasattr(settings, name), f"{name} 是死配置，不得回潮"
