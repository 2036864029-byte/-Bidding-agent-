"""Phase 2 会话理解节点测试（改写+路由合并为一次结构化调用，2026-09-16）。

覆盖：
  1. structured 路径：resolved_question 同 id 原位替换 messages[-1]（追问断链修复）；
  2. 自包含问题：resolved_question=None → 不产生替换（零多余行为）；
  3. tool_fallback 路径：工具参数无法回传 resolved_question → 先独立改写再路由。
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from agent.router import build_router_node


class _FakeStructured:
    """with_structured_output：当前输入是追问时返回 resolved_question。"""

    def __init__(self, parent, resolved: str = "招标方式有哪些？请逐一介绍"):
        self._parent = parent
        self._resolved = resolved

    def invoke(self, messages):
        texts = [str(m.content) for m in messages if getattr(m, "type", "") == "human"]
        user = "".join(texts)
        is_followup = "具体介绍" in user
        return SimpleNamespace(
            intent="knowledge_qa",
            reason="测试",
            resolved_question=self._resolved if is_followup else None,
            needs_clarification=False,
            candidates=[],
        )


class _FakeLLM:
    """structured 路径 fake（tool_fallback=False）。"""

    def with_structured_output(self, model):  # noqa: ARG002
        return _FakeStructured(self)

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self


class _FakeToolFallbackLLM(RunnableLambda):
    """tool_fallback 路径 fake：结构化不支持 + Runnable（改写链）+ bind_tools（路由）。"""

    def __init__(self):
        super().__init__(lambda pv: AIMessage(content="招标方式有哪些？请逐一介绍"))
        self._route_intent = "knowledge_qa"

    def with_structured_output(self, model):  # noqa: ARG002
        raise ValueError("response_format not supported")

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        bound = self

        class _Bound:
            def invoke(self, messages):  # noqa: ARG002
                return SimpleNamespace(
                    tool_calls=[{"name": f"route_{bound._route_intent}"}]
                )

        return _Bound()


class _FakeClarifyLLM:
    """澄清路径 fake：needs_clarification=True + 候选（Phase 3）。"""

    def with_structured_output(self, model):  # noqa: ARG002
        return _FakeStructuredClarify()

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self


class _FakeStructuredClarify:
    def invoke(self, messages):  # noqa: ARG002
        return SimpleNamespace(
            intent="fallback",
            reason="测试",
            resolved_question=None,
            needs_clarification=True,
            candidates=["招标方式有哪些？请逐一介绍", "公开招标和邀请招标的区别"],
        )


def _history(*contents: str):
    return [HumanMessage(content=c) for c in contents]


# ── structured 路径：合并会话理解 ─────────────────────────────


def test_merged_router_replaces_followup_in_place():
    node = build_router_node(_FakeLLM())
    messages = _history("招标方式有哪些？", "具体介绍一下？")
    last_id = messages[-1].id

    out = node({"messages": messages})

    assert out["router_intent"] == "knowledge_qa"
    replaced = out["messages"][0]
    assert isinstance(replaced, HumanMessage)
    assert replaced.content == "招标方式有哪些？请逐一介绍"
    assert replaced.id == last_id  # 同 id 原位替换（add_messages 合并）


def test_merged_router_no_replace_for_self_contained():
    node = build_router_node(_FakeLLM())
    messages = _history("A公司中标了哪些项目？", "什么是公开招标？")

    out = node({"messages": messages})

    assert out["router_intent"] == "knowledge_qa"
    assert "messages" not in out  # 自包含 → 不产生替换


def test_merged_router_empty_messages_falls_back():
    node = build_router_node(_FakeLLM())
    assert node({"messages": []}) == {"router_intent": "fallback"}


# ── tool_fallback 路径：独立改写 + 路由 ───────────────────────


def test_merged_router_tool_fallback_pre_rewrites():
    node = build_router_node(_FakeToolFallbackLLM())
    messages = _history("招标方式有哪些？", "具体介绍一下？")
    last_id = messages[-1].id

    out = node({"messages": messages})

    assert out["router_intent"] == "knowledge_qa"
    assert "messages" in out
    replaced = out["messages"][0]
    assert replaced.content == "招标方式有哪些？请逐一介绍"
    assert replaced.id == last_id


# ── 澄清式反问（Phase 3）：无法消解 → clarify ─────────────────


def test_merged_router_routes_to_clarify_when_ambiguous():
    node = build_router_node(_FakeClarifyLLM())
    messages = _history("招标方式有哪些？", "具体介绍一下？")

    out = node({"messages": messages})

    assert out["router_intent"] == "clarify"
    br = out["business_result"]
    assert br["branch"] == "clarify"
    assert br["data"]["candidates"] == [
        "招标方式有哪些？请逐一介绍",
        "公开招标和邀请招标的区别",
    ]
    assert "messages" not in out  # 澄清不替换问题，等用户选候选
