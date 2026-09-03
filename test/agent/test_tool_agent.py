"""agent.tool_agent — 工具驱动 agent 循环离线测试（方案 Phase 6 / Step 3 H-1）。

不触碰网络：fake LLM 模拟 bind_tools + tool_calls 序列；fake tool 返回固定结果。
覆盖：工具调用→最终回答、无工具直答、轮次上限兜底、未知工具/工具异常不中断循环、
空消息守卫。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from agent.tool_agent import build_tool_agent_node, run_tool_agent


# ── 夹具 ──────────────────────────────────────────────────────

def _tc(name, args, cid):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


class _FakeTool:
    """鸭子类型 tool：name + invoke（BaseTool 的最小可调用面）。"""

    def __init__(self, name, result):
        self.name = name
        self._result = result

    def invoke(self, args):
        return self._result


class _FakeToolLLM:
    """fake LLM：bind_tools 返回自身；invoke 按步骤序列返回 AIMessage。"""

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls = 0
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, messages):
        self.calls += 1
        step = self._steps[min(self.calls - 1, len(self._steps) - 1)]
        return step(messages) if callable(step) else step


# ── 循环行为 ──────────────────────────────────────────────────

def test_agent_runs_tool_then_answers():
    llm = _FakeToolLLM([
        AIMessage(content="", tool_calls=[_tc("kb_tool", {"question": "q"}, "c1")]),
        AIMessage(content="根据检索结果回答。"),
    ])
    out = run_tool_agent("q", llm=llm, tools=[_FakeTool("kb_tool", {"answer": "检索结果"})],
                         system_prompt="sp")
    assert out["answer"] == "根据检索结果回答。"
    assert out["rounds"] == 2
    assert llm.calls == 2
    assert out["tool_trace"][0]["name"] == "kb_tool"
    assert "检索结果" in out["tool_trace"][0]["result_head"]


def test_agent_direct_answer_without_tool():
    llm = _FakeToolLLM([AIMessage(content="直接回答。")])
    out = run_tool_agent("q", llm=llm, tools=[_FakeTool("t", {})], system_prompt="sp")
    assert out["rounds"] == 1
    assert out["answer"] == "直接回答。"
    assert out["tool_trace"] == []


def test_agent_bounded_iterations_stops():
    """LLM 永远要求调工具 → max_iterations 后兜底回答，不无限循环。"""
    llm = _FakeToolLLM([AIMessage(content="", tool_calls=[_tc("t", {}, "c")])])
    out = run_tool_agent("q", llm=llm, tools=[_FakeTool("t", {})],
                         system_prompt="sp", max_iterations=3)
    assert out["rounds"] == 3
    assert len(out["tool_trace"]) == 3
    assert "未能完成" in out["answer"]


def test_agent_unknown_tool_continues():
    llm = _FakeToolLLM([
        AIMessage(content="", tool_calls=[_tc("nope", {}, "c1")]),
        AIMessage(content="完成。"),
    ])
    out = run_tool_agent("q", llm=llm, tools=[_FakeTool("kb_tool", {})], system_prompt="sp")
    assert "未知工具" in out["tool_trace"][0]["result_head"]
    assert out["answer"] == "完成。"


def test_agent_tool_error_does_not_break_loop():
    class _BoomTool:
        name = "boom"

        def invoke(self, args):
            raise RuntimeError("db down")

    llm = _FakeToolLLM([
        AIMessage(content="", tool_calls=[_tc("boom", {}, "c1")]),
        AIMessage(content="降级回答。"),
    ])
    out = run_tool_agent("q", llm=llm, tools=[_BoomTool()], system_prompt="sp")
    assert "工具错误" in out["tool_trace"][0]["result_head"]
    assert out["answer"] == "降级回答。"


# ── 节点形态 ──────────────────────────────────────────────────

def test_build_tool_agent_node_empty_messages():
    node = build_tool_agent_node(
        tools=[_FakeTool("t", {})], system_prompt="sp",
        llm=_FakeToolLLM([AIMessage(content="x")]), agent_name="demo",
    )
    out = node({"messages": []})
    assert out["business_result"]["branch"] == "demo"
    assert "没有收到您的问题" in out["business_result"]["answer"]


def test_build_tool_agent_node_wraps_result():
    llm = _FakeToolLLM([
        AIMessage(content="", tool_calls=[_tc("kb_tool", {"question": "q"}, "c1")]),
        AIMessage(content="基于工具结果的回答。"),
    ])
    node = build_tool_agent_node(
        tools=[_FakeTool("kb_tool", {"records": [1]})], system_prompt="sp", llm=llm,
        agent_name="demo",
    )
    from langchain_core.messages import HumanMessage

    out = node({"messages": [HumanMessage(content="查一下")]})
    assert out["business_result"]["branch"] == "demo"
    assert out["business_result"]["answer"] == "基于工具结果的回答。"
    assert len(out["business_result"]["data"]["tool_trace"]) == 1
    assert out["messages"][0].content == "基于工具结果的回答。"


def test_build_tool_agent_node_requires_llm_source():
    import pytest

    with pytest.raises(ValueError):
        build_tool_agent_node(tools=[_FakeTool("t", {})], system_prompt="sp")
