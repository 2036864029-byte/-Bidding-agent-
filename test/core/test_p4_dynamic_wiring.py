"""P4 动态化接线 + echo_demo 零改接入演示（离线，全 mock LLM）。

验证三点：
1. 默认 build_graph 按 registry 动态建节点/边：5 业务节点 + router；echo_demo
   （默认 disabled）不接线。
2. 「登记即接入」：fresh registry seed_builtins + 登记一个 enabled echo_demo spec
   + 注册 entry —— build_graph / build_router_node **零修改**自动纳入新能力
   （graph 出现 echo_demo 节点，router 候选含 echo_demo）。
3. 端到端：fake LLM 路由 "ECHO|..." → echo_demo → node_echo_demo 回显。
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=DeprecationWarning)

import pytest

from agent.graph import build_graph
from agent.nodes.base import register as register_node
from agent.nodes.base import reset_node_registry
from agent.nodes.echo_demo import node_echo_demo
from agent.registry import (
    CapabilityRegistry,
    CapabilitySpec,
    get_default_registry,
    reset_default_registry,
    seed_builtins,
)
from agent.router import build_decision_model, build_router_node


@pytest.fixture(autouse=True)
def _clean_registries():
    reset_default_registry()
    reset_node_registry()
    yield
    reset_default_registry()
    reset_node_registry()


class _FakeStructured:
    def __init__(self, parent):
        self._parent = parent

    def invoke(self, messages):  # noqa: ARG002
        # 决策意图：仅用于测试路由，不回真实 LLM。
        # 注意结构化路径发给 LLM 的是「历史+当前输入」模板渲染后的整段文本。
        texts = [str(m.content) for m in messages if getattr(m, "type", "") == "human"]
        user = "".join(texts)
        return SimpleNamespace(
            intent="echo_demo" if "ECHO|" in user else "fallback",
            reason="测试",
        )


class _FakeLLM:
    """鸭子类型 LLM：with_structured_output 路径（tool_fallback=False）。"""

    def with_structured_output(self, model):  # noqa: ARG002
        return _FakeStructured(self)

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self


def _echo_enabled_registry() -> CapabilityRegistry:
    """seed 5 内置 + 登记 enabled 的 echo_demo（即演示新增能力）。"""
    reg = CapabilityRegistry()
    seed_builtins(reg)
    reg.register(CapabilitySpec(
        id="echo_demo", node="echo_demo",
        description="回显演示（登记即接入）",
        route_desc="回显演示：请用户说一句话，原样复述",
        route_tool_doc="回显演示：用户要求复述/回显一句话时使用",
        examples=["请复述这句话"],
        model_slots=[], tools=[],
        enabled=True,
    ))
    return reg


def _wired_node_names(compiled) -> set[str]:
    return set(compiled.get_graph().nodes.keys())


def test_default_graph_wires_five_business_nodes_only():
    compiled = build_graph(llm=_FakeLLM())
    names = _wired_node_names(compiled)
    assert {"router", "knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback"} <= names
    assert "echo_demo" not in names  # 默认 disabled → 不接线


def test_echo_enabled_registry_autowires_without_graph_edits():
    """graph/router 源码零修改，仅 registry 登记 enabled echo + 注册 entry。"""
    register_node("echo_demo", node_echo_demo)  # 装配层：登记 entry
    compiled = build_graph(llm=_FakeLLM(), registry=_echo_enabled_registry())
    assert "echo_demo" in _wired_node_names(compiled)


def test_router_candidates_include_new_capability_without_router_edits():
    reg = _echo_enabled_registry()
    # router 候选枚举由 registry 生成 → 含 echo_demo
    model = build_decision_model([c.id for c in reg.enabled_specs()])
    enum = model.model_json_schema()["properties"]["intent"]["enum"]
    assert "echo_demo" in enum
    assert len(enum) == 6


def test_echo_demo_end_to_end_offline():
    """fake LLM 路由 ECHO 查询 → echo_demo 节点 → 回显。零 router/graph 源码改动。"""
    register_node("echo_demo", node_echo_demo)
    compiled = build_graph(llm=_FakeLLM(), registry=_echo_enabled_registry())

    from langchain_core.messages import HumanMessage

    result = compiled.invoke(
        {"messages": [HumanMessage(content="ECHO|你好")]},
        config={"configurable": {"thread_id": "t1"}},
    )
    br = result.get("business_result") or {}
    assert br.get("branch") == "echo_demo"
    assert result.get("router_intent") == "echo_demo"
    msgs = result.get("messages") or []
    assert msgs[-1].content == "回显：ECHO|你好"


def test_missing_node_entry_raises_clear_error():
    """enabled spec 引用了未登记 entry 的节点 → 构建期报错（防误接线）。"""
    reg = _echo_enabled_registry()
    # 故意不 register_node("echo_demo") → 应清晰报错
    with pytest.raises(ValueError, match="echo_demo"):
        build_graph(llm=_FakeLLM(), registry=reg)


def test_build_router_node_registry_param_matches_default():
    """build_router_node 默认 registry == 显式默认注册表（行为一致）。"""
    default = get_default_registry()
    node_default = build_router_node(_FakeLLM())
    node_explicit = build_router_node(_FakeLLM(), registry=default)
    # 同一注册表下闭包内部一致：喂空状态 → fallback
    assert node_default({}) == node_explicit({}) == {"router_intent": "fallback"}
