"""knowledge_qa 节点单一实现测试（W1-1 收口后）。

W1-1 删除了 `USE_TOOL_KB` 开关与 legacy `rag.query()` / `chain.answer_knowledge`，
节点恒走 knowledge_qa 子图。本文件验证：分发消失、config 透传、空消息友好降级，
以及旧模块不得回潮。
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

import agent.nodes.knowledge_qa.node as node_mod


def _fake_invoke(state, config=None):
    return {
        "business_result": {
            "branch": "knowledge_qa",
            "answer": "按【来源1】…",
            "data": {"citation_validation": {"all_passed": True}},
        },
        "messages": [AIMessage(content="按【来源1】…")],
    }


def test_node_always_invokes_subgraph(monkeypatch):
    """无开关可切：节点唯一路径就是子图。"""
    called = {}

    def spy(state, config=None):
        called["state"] = state
        return _fake_invoke(state, config)

    monkeypatch.setattr(node_mod, "get_knowledge_qa_subgraph",
                        lambda: SimpleNamespace(invoke=spy))

    out = node_mod.node_knowledge_qa({"messages": [HumanMessage(content="招标方式有哪些")]})

    assert called["state"]["messages"][-1].content == "招标方式有哪些"
    assert out["business_result"]["branch"] == "knowledge_qa"
    assert out["business_result"]["data"]["citation_validation"] == {"all_passed": True}
    assert out["messages"][0].content == "按【来源1】…"


def test_node_forwards_config_to_subgraph(monkeypatch):
    """子图化后：config（thread_id）透传子图 invoke（多轮记忆依赖）。"""
    seen = {}

    def spy(state, config=None):
        seen["config"] = config
        seen["state"] = state
        return _fake_invoke(state, config)

    monkeypatch.setattr(node_mod, "get_knowledge_qa_subgraph",
                        lambda: SimpleNamespace(invoke=spy))

    node_mod.node_knowledge_qa(
        {"messages": [HumanMessage(content="q")]},
        config={"configurable": {"thread_id": "t1"}},
    )

    assert seen["config"] == {"configurable": {"thread_id": "t1"}}
    assert seen["state"]["messages"][-1].content == "q"


def test_node_empty_messages_friendly():
    """空消息由子图直接给友好提示（不建 LLM）。"""
    out = node_mod.node_knowledge_qa({"messages": []})
    assert "没有收到您的问题" in out["business_result"]["answer"]


def test_legacy_switch_and_modules_removed():
    """W1-1/W1-2：开关字段与旧实现模块不得回潮。"""
    from pathlib import Path

    from core.config.settings import Settings

    assert not hasattr(Settings(), "enable_kb_tool_path")
    assert not hasattr(node_mod, "_TOOL_PATH_ENABLED")

    kb_root = Path(node_mod.__file__).resolve().parent
    assert not (kb_root / "legacy.py").exists()
    assert not (kb_root / "chain.py").exists()
