"""kb_search 工具契约离线测试（方案 Phase 1 / B-1、B-9）。

用假 retriever 注入，验证三元组字段齐全、top_k 截断、空结果不触 LLM、
kb_search_as_tool 只建不接线。不触碰任何网络/Milvus。
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

from langchain_core.documents import Document

warnings.filterwarnings("ignore", category=DeprecationWarning)

from public_kb.tools import (  # noqa: E402
    Chunk,
    RetrievalResult,
    kb_search,
    kb_search_as_tool,
)


def _doc(content: str, **meta) -> Document:
    return Document(page_content=content, metadata={"doc_name": "测试法", "chapter": "第一章", **meta})


def _fake_retriever(docs):
    """返回带 .docs / .diagnostics 的假检索结果对象。"""
    return SimpleNamespace(
        retrieve=lambda q: SimpleNamespace(
            docs=docs,
            diagnostics=SimpleNamespace(to_dict=lambda: {"mode": "fake"}),
        )
    )


def test_kb_search_returns_full_triple(monkeypatch):
    docs = [(_doc("第一条内容", chunk_id=1, chunk_uid="u1", score=0.9), 0.9)]
    monkeypatch.setattr("public_kb.tools._get_retriever", lambda: _fake_retriever(docs))

    result = kb_search("测试问题", top_k=8)

    assert isinstance(result, RetrievalResult)
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert isinstance(chunk, Chunk)
    assert chunk.content == "第一条内容"
    assert chunk.chunk_id == 1
    assert chunk.chunk_uid == "u1"
    assert chunk.doc_name == "测试法"
    assert chunk.score == 0.9
    # 引用元数据与渲染上下文齐全
    assert len(result.citations_meta) == 1
    assert result.citations_meta[0].chunk_uid == "u1"
    assert "第一条内容" in result.rendered_context
    assert "[来源1]" in result.rendered_context
    assert result.recall_trace == {"mode": "fake"}


def test_top_k_truncates(monkeypatch):
    docs = [(_doc(f"内容{i}", chunk_id=i), 0.5) for i in range(10)]
    monkeypatch.setattr("public_kb.tools._get_retriever", lambda: _fake_retriever(docs))

    assert len(kb_search("q", top_k=3).chunks) == 3
    assert len(kb_search("q", top_k=99).chunks) == 10


def test_empty_result_returns_empty_triple_no_llm(monkeypatch):
    monkeypatch.setattr("public_kb.tools._get_retriever", lambda: _fake_retriever([]))
    result = kb_search("查无此条")
    assert result.chunks == []
    assert result.citations_meta == []
    assert result.rendered_context == ""
    assert result.recall_trace == {"mode": "fake"}


def test_kb_search_as_tool_builds_but_returns_callable(monkeypatch):
    monkeypatch.setattr("public_kb.tools._get_retriever", lambda: _fake_retriever([]))
    tool = kb_search_as_tool()
    assert tool.name == "kb_search_tool"
    # 只建不接线：未被任何 agent 注册（无全局副作用断言即为通过）
    out = tool.invoke({"question": "测试", "top_k": 3})
    assert out["chunks"] == []
    assert out["citations_meta"] == []
