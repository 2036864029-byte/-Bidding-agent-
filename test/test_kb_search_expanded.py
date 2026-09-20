"""kb_search_expanded（Layer 1 统一轻量补检）离线测试。

不触碰网络：_get_retriever 打桩（按 query 返回不同 docs）。
覆盖：基线原序 + 子句补检并集 / 去重 / max_chunks 上限 / 无子句回退 /
子句切分过滤 / subgraph 开关路由。
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

warnings.filterwarnings("ignore", category=DeprecationWarning)

import agent.nodes.knowledge_qa.subgraph as subgraph_mod  # noqa: E402
from agent.nodes.knowledge_qa.subgraph import _retrieve_node  # noqa: E402
from public_kb.tools import _split_clauses, kb_search_expanded  # noqa: E402


def _doc(content: str, **meta) -> Document:
    return Document(
        page_content=content,
        metadata={"doc_name": "测试法", "chapter": "第一章", **meta},
    )


def _retriever_by_query(mapping):
    def _retrieve(q):
        return SimpleNamespace(
            docs=mapping(q),
            diagnostics=SimpleNamespace(to_dict=lambda: {"mode": "fake"}),
        )

    return SimpleNamespace(retrieve=_retrieve)


Q = "请问第一条规则是什么内容？同时第二条规则又怎么规定？"
CLAUSE1 = "请问第一条规则是什么内容"
CLAUSE2 = "同时第二条规则又怎么规定"
_BASE = [(_doc("基线块A", chunk_id=1, chunk_uid="a"), 0.9),
         (_doc("基线块B", chunk_id=2, chunk_uid="b"), 0.8)]


def _two_clause_mapping(q):
    if q == CLAUSE1:
        return [(_doc("基线块A", chunk_id=1, chunk_uid="a"), 0.9),
                (_doc("新块C", chunk_id=3, chunk_uid="c"), 0.7)]
    if q == CLAUSE2:
        return [(_doc("基线块B", chunk_id=2, chunk_uid="b"), 0.8),
                (_doc("新块D", chunk_id=4, chunk_uid="d"), 0.6)]
    return _BASE


def test_expanded_merges_base_then_clause_new_blocks(monkeypatch):
    """基线原序在前，子句补检新块按子句序追加；渲染/引用按合并后重建。"""
    monkeypatch.setattr("public_kb.tools._get_retriever",
                        lambda: _retriever_by_query(_two_clause_mapping))

    result = kb_search_expanded(Q)

    contents = [c.content for c in result.chunks]
    assert contents == ["基线块A", "基线块B", "新块C", "新块D"]  # 基线原序 + 新块
    assert len(result.citations_meta) == 4
    assert result.citations_meta[2].chunk_uid == "c"  # 编号与 [来源3] 对应
    for i in range(1, 5):
        assert f"[来源{i}]" in result.rendered_context
    assert result.recall_trace == {"mode": "fake"}


def test_expanded_dedupes_overlapping(monkeypatch):
    """子句补检返回与基线重叠的块 → 不重复添加。"""
    monkeypatch.setattr(
        "public_kb.tools._get_retriever",
        lambda: _retriever_by_query(lambda q: [(_doc("同内容", chunk_id=1, chunk_uid="a"), 0.9)]),
    )
    result = kb_search_expanded(Q)
    assert len(result.chunks) == 1
    assert result.chunks[0].content == "同内容"


def test_expanded_caps_max_chunks(monkeypatch):
    """max_chunks 封顶总块数（基线已占满则不再补检）。"""
    monkeypatch.setattr("public_kb.tools._get_retriever",
                        lambda: _retriever_by_query(_two_clause_mapping))
    result = kb_search_expanded(Q, max_chunks=3)
    assert [c.content for c in result.chunks] == ["基线块A", "基线块B", "新块C"]


def test_expanded_no_clauses_falls_back_base(monkeypatch):
    """问题切不出有效子句 → 只返回基线（等价 kb_search）。"""
    monkeypatch.setattr("public_kb.tools._get_retriever",
                        lambda: _retriever_by_query(_two_clause_mapping))
    result = kb_search_expanded("招标方式有哪些？")  # 7 字 < min_clause_len
    assert [c.content for c in result.chunks] == ["基线块A", "基线块B"]


def test_split_clauses_filters_short():
    assert _split_clauses("请问第一条规定了什么内容？", 8) == ["请问第一条规定了什么内容"]
    assert _split_clauses("短？更短", 8) == []


def test_expanded_skips_clauses_when_base_empty(monkeypatch):
    """base 检索为空 → 不再为子句补检付检索成本（拒答路径省钱）。

    旧逻辑 `clauses and len(docs) < max_chunks` 在 docs=[] 时恒真，空结果仍
    白付多次子句检索；2026-09-16 起 base 空直接短路。
    """
    calls = {"retrieve": 0}

    def _retrieve(q):
        calls["retrieve"] += 1
        return SimpleNamespace(
            docs=[],
            diagnostics=SimpleNamespace(to_dict=lambda: {}),
        )

    monkeypatch.setattr(
        "public_kb.tools._get_retriever", lambda: SimpleNamespace(retrieve=_retrieve)
    )

    # Q 能切出 2 个子句（≥8 字）——若没短路会再多 2 次检索
    result = kb_search_expanded(Q)

    assert result.chunks == []
    assert calls["retrieve"] == 1, "base 为空时只应付一次基线检索"


def test_subgraph_routes_on_switch(monkeypatch):
    """开关 False → kb_search；True → kb_search_expanded。"""
    called: dict = {}

    monkeypatch.setattr(subgraph_mod, "_get_settings",
                        lambda: SimpleNamespace(kb_light_decompose=False))
    monkeypatch.setattr(subgraph_mod, "kb_search",
                        lambda q: called.setdefault("path", "kb_search"))
    monkeypatch.setattr(subgraph_mod, "kb_search_expanded",
                        lambda q: called.setdefault("path", "expanded"))
    _retrieve_node({"messages": [HumanMessage(content="测试问题")]})
    assert called["path"] == "kb_search"

    called.clear()
    monkeypatch.setattr(subgraph_mod, "_get_settings",
                        lambda: SimpleNamespace(kb_light_decompose=True))
    _retrieve_node({"messages": [HumanMessage(content="测试问题")]})
    assert called["path"] == "expanded"
