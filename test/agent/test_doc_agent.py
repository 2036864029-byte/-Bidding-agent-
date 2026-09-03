"""doc_agent — 工具驱动 agent 离线测试（方案 Phase 6 / Step 3 H-2/H-3）。

验证能力包双形态复用：kb_search_as_tool + price_query_as_tool 可挂到 agent 上
自主调用；registry 预留登记 + base entry 登记即接入（echo_demo 同款先例）。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent.tool_agent import run_tool_agent


# ── 夹具（与 test_tool_agent 同构）────────────────────────────

def _tc(name, args, cid):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


class _FakeTool:
    def __init__(self, name, result):
        self.name = name
        self._result = result

    def invoke(self, args):
        return self._result


class _FakeToolLLM:
    def __init__(self, steps):
        self._steps = list(steps)
        self._i = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        step = self._steps[min(self._i, len(self._steps) - 1)]
        self._i += 1
        return step(messages) if callable(step) else step


# ── 工具绑定（能力包双形态）──────────────────────────────────

def test_doc_agent_tool_list_contains_both_capabilities():
    from agent.nodes.doc_agent import _doc_agent_tools

    names = sorted(t.name for t in _doc_agent_tools())
    assert "kb_search_tool" in names      # public_kb 法规知识（tool 形态）
    assert "price_query" in names         # price_engine 结构化查询（tool 形态）


def test_doc_agent_loop_calls_kb_tool_and_answers():
    """doc_agent 用 kb_search_tool 检索后回答——证明 tool 形态可用。"""
    llm = _FakeToolLLM([
        AIMessage(content="", tool_calls=[_tc("kb_search_tool", {"question": "什么是公开招标？"}, "c1")]),
        AIMessage(content="公开招标是指招标人以招标公告方式邀请不特定投标人投标。"),
    ])
    tool = _FakeTool("kb_search_tool", {"chunks": [{"content": "公开招标定义"}], "citations_meta": []})
    out = run_tool_agent("什么是公开招标？", llm=llm, tools=[tool],
                         system_prompt="你是招投标助手。")
    assert "公开招标" in out["answer"]
    assert out["tool_trace"][0]["name"] == "kb_search_tool"


def test_doc_agent_node_runs_with_injected_deps():
    from agent.nodes.doc_agent import build_doc_agent_node

    llm = _FakeToolLLM([
        AIMessage(content="", tool_calls=[_tc("price_query", {"question": "X中标情况"}, "c1")]),
        AIMessage(content="查到 X 中标 1 个项目。"),
    ])
    node = build_doc_agent_node(
        llm=llm,
        tools=[_FakeTool("price_query", {"records": [{"project_name": "A"}]})],
    )
    out = node({"messages": [HumanMessage(content="X公司中标了哪些项目？")]})
    br = out["business_result"]
    assert br["branch"] == "doc_agent"
    assert "中标" in br["answer"]
    assert br["data"]["tool_trace"][0]["name"] == "price_query"
    assert out["messages"][0].content == br["answer"]


# ── 登记与接线（registry enabled=False 预留 + entry 已登记）───

def test_doc_agent_registry_reserved_disabled():
    from agent.registry import get_default_registry

    spec = get_default_registry().get("doc_agent")
    assert spec is not None
    assert spec.enabled is False          # 预留防误路由（同 echo_demo）
    assert spec.model_slots == ["doc_agent"]
    assert spec.tools == ["kb_search", "price_query"]


def test_doc_agent_entry_registered_in_base():
    from agent.nodes.base import get_node_spec

    assert get_node_spec("doc_agent") is not None


def test_doc_agent_enabled_registry_wires_without_graph_edits():
    """启用 doc_agent 后 graph/router 零改动自动接线（P4 registry 数据驱动）。"""
    from agent.graph import build_graph
    from agent.nodes import node_doc_agent
    from agent.nodes.base import register
    from agent.registry import CapabilityRegistry, CapabilitySpec, seed_builtins

    reg = CapabilityRegistry()
    seed_builtins(reg)
    reg.register(CapabilitySpec(
        id="doc_agent", node="doc_agent", enabled=True,
        route_desc="文档/法规综合问答", route_tool_doc="doc_agent 工具化回答",
        examples=["查一下这个法规"], model_slots=["doc_agent"],
        tools=["kb_search", "price_query"],
    ))
    register("doc_agent", node_doc_agent)  # 装配层登记 entry（base 已 seed）

    compiled = build_graph(llm=object(), registry=reg)
    assert "doc_agent" in compiled.get_graph().nodes.keys()
