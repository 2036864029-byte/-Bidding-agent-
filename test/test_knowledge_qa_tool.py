"""knowledge_qa 拆包后 节点分发 + 工具链路 离线测试（方案 Phase 1 / B-4、B-9）。

不触碰任何网络：工具链路的 kb_search / llm 均打桩；开关经 monkeypatch。
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

warnings.filterwarnings("ignore", category=DeprecationWarning)

from public_kb.guardrails.citations_models import Citation
from public_kb.tools import Chunk, RetrievalResult

import agent.nodes.knowledge_qa.node as node_mod
import agent.nodes.knowledge_qa.chain as chain_mod


# ── 夹具 ──────────────────────────────────────────────────────

def _triple(n: int = 1) -> RetrievalResult:
    chunks = [
        Chunk(content="招标方式分为公开招标和邀请招标。", chunk_id=1, chunk_uid="u1",
              doc_name="招标投标法", chapter="第一章", chunk_index=0, score=0.9)
    ][:n]
    citations = [
        Citation(context_index=1, chunk_id=1, chunk_uid="u1",
                 doc_name="招标投标法", chapter="第一章", chunk_index=0,
                 text="招标方式分为公开招标和邀请招标。", score=0.9)
    ][:n]
    return RetrievalResult(
        chunks=chunks,
        citations_meta=citations,
        rendered_context="[来源1] 文档: 招标投标法 | 章节: 第一章\n招标方式分为公开招标和邀请招标。",
        recall_trace={"mode": "fake"},
    )


def _llm_like(content: str):
    """返回一个能参与 `prompt | llm | parser` 的假 Runnable。"""
    return RunnableLambda(lambda messages: AIMessage(content=content))


# ── 节点分发 ─────────────────────────────────────────────────

def test_default_switch_is_off_tool_path():
    """USE_TOOL_KB 默认 false（.env 未设置时）—— 保持旧行为。"""
    assert node_mod._TOOL_PATH_ENABLED is False


def test_node_dispatches_to_legacy_when_switch_off(monkeypatch):
    monkeypatch.setattr(node_mod, "_TOOL_PATH_ENABLED", False)
    called = {"legacy": False, "tool": False}

    def fake_legacy(state):
        called["legacy"] = True
        return {"business_result": {"branch": "knowledge_qa", "answer": "legacy", "data": {}}}

    def boom(state):
        called["tool"] = True
        raise AssertionError("不应走工具路径")

    monkeypatch.setattr(node_mod, "run_legacy_node", fake_legacy)
    monkeypatch.setattr(node_mod, "_tool_chain", SimpleNamespace(answer_knowledge=boom))

    out = node_mod.node_knowledge_qa({"messages": [HumanMessage(content="招标方式有哪些")]})
    assert called["legacy"] and not called["tool"]
    assert out["business_result"]["answer"] == "legacy"


def test_node_dispatches_to_tool_path_when_switch_on(monkeypatch):
    monkeypatch.setattr(node_mod, "_TOOL_PATH_ENABLED", True)

    def fake_answer(question):
        return {"answer": "按【来源1】…", "sources": [], "citations": [{"context_index": 1}],
                "citation_validation": {"all_passed": True}, "retrieval_diagnostics": {}}

    monkeypatch.setattr(node_mod, "_tool_chain", SimpleNamespace(answer_knowledge=fake_answer))

    out = node_mod.node_knowledge_qa({"messages": [HumanMessage(content="招标方式有哪些")]})
    assert out["business_result"]["branch"] == "knowledge_qa"
    assert out["business_result"]["answer"] == "按【来源1】…"
    assert out["business_result"]["data"]["citation_validation"] == {"all_passed": True}
    assert out["messages"][0].content == "按【来源1】…"


def test_node_empty_messages_friendly(monkeypatch):
    monkeypatch.setattr(node_mod, "_TOOL_PATH_ENABLED", True)
    out = node_mod.node_knowledge_qa({"messages": []})
    assert "没有收到您的问题" in out["business_result"]["answer"]


# ── 工具链路 chain.answer_knowledge ──────────────────────────

def test_chain_empty_retrieval_refuses(monkeypatch):
    monkeypatch.setattr(chain_mod, "kb_search", lambda q: RetrievalResult())
    out = chain_mod.answer_knowledge("查无此条")
    assert "暂无相关内容" in out["answer"]
    assert out["sources"] == []
    assert out["citations"] == []
    assert out["citation_validation"]["is_refusal"] is True
    assert out["retrieval_diagnostics"] == {}


def test_chain_full_triple_generates_and_validates(monkeypatch):
    monkeypatch.setattr(chain_mod, "kb_search", lambda q: _triple(1))
    monkeypatch.setattr(chain_mod, "_get_llm", lambda: _llm_like("招标方式包括公开招标和邀请招标。【来源1】"))

    out = chain_mod.answer_knowledge("招标方式有哪些？")

    assert "招标方式" in out["answer"]
    assert len(out["citations"]) == 1
    assert out["citations"][0]["chunk_uid"] == "u1"
    # 引用标记解析到有效来源 → 校验通过
    assert out["citation_validation"]["all_passed"] is True
    assert out["retrieval_diagnostics"] == {"mode": "fake"}
    # legacy sources 视图由 chunk 构建
    assert out["sources"][0]["doc"] == "招标投标法"
