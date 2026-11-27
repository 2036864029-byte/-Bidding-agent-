"""agent.policy 测试 —— 能力 → 工具白名单的双层落地。

覆盖：
  1. registry 声明驱动白名单（声明名与实现名不同也能授权）；
  2. 未登记能力 fail-closed（空白名单，拒绝全部）；
  3. 装配期过滤：未声明工具不会 bind 给模型；
  4. 执行期校验：模型越权给工具名 → 不调用、回写拒绝原因、轨迹标 denied。
"""

from __future__ import annotations

from typing import Any, List

from langchain_core.messages import AIMessage

from agent.policy import (
    ToolPolicy,
    bind_capability_tools,
    policy_for,
    split_denied,
    tool_name,
)
from agent.registry import CapabilityRegistry, CapabilitySpec
from agent.tool_agent import run_tool_agent

# ── 假工具 ────────────────────────────────────────────────────


class _FakeTool:
    """鸭子类型工具（只要 name/description 就够 ToolPolicy 与 bind_tools 用）。"""

    def __init__(self, name: str, calls: List[str] | None = None) -> None:
        self.name = name
        self.description = f"{name} 工具"
        self.calls = calls if calls is not None else []

    def invoke(self, args: Any = None) -> dict:
        self.calls.append(self.name)
        return {"ok": self.name, "args": args}


class _FakeLLM:
    """按预设 tool_calls 序列作答的假 LLM（末轮给文本）。"""

    def __init__(self, script: List[AIMessage]) -> None:
        self.script = list(script)
        self.bound_names: List[str] = []

    def bind_tools(self, tools):  # noqa: ANN001
        self.bound_names = [tool_name(t) for t in tools]
        return self

    def invoke(self, messages):  # noqa: ANN001
        return self.script.pop(0) if self.script else AIMessage(content="完成")


def _registry_with(capability_id: str, tools: List[str]) -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.register(CapabilitySpec(id=capability_id, tools=tools, enabled=True))
    return reg


# ── 白名单来源 ────────────────────────────────────────────────


def test_policy_reads_registry_declaration():
    reg = _registry_with("deep_agent", ["kb_search", "read_file"])
    policy = policy_for("deep_agent", registry=reg)
    assert policy.is_allowed("kb_search")
    assert not policy.is_allowed("price_query")
    assert policy.source == "registry"


def test_policy_fail_closed_for_unregistered_capability():
    reg = _registry_with("deep_agent", ["kb_search"])
    policy = policy_for("ghost_agent", registry=reg)
    assert policy.allowed_names == frozenset()
    assert not policy.is_allowed("kb_search")  # 未登记 ≠ 放行
    assert policy.source == "unregistered"


def test_bind_accepts_declared_name_and_actual_name():
    """声明 kb_search / 实现 kb_search_tool：授权认声明名，执行也认实际名。"""
    reg = _registry_with("deep_agent", ["kb_search", "read_file"])
    catalog = {
        "kb_search": lambda: _FakeTool("kb_search_tool"),
        "read_file": lambda: _FakeTool("read_file"),
        "price_query": lambda: _FakeTool("price_query"),
    }
    bound = bind_capability_tools("deep_agent", catalog, registry=reg)
    names = sorted(tool_name(t) for t in bound.tools)
    assert names == ["kb_search_tool", "read_file"]  # 未声明的 price_query 不装配
    assert bound.policy.is_allowed("kb_search")      # 声明名
    assert bound.policy.is_allowed("kb_search_tool")  # 实际名
    assert bound.policy.describe()["resolved"] == ["kb_search_tool", "read_file"]


def test_bind_skips_factory_failure_without_crashing():
    reg = _registry_with("deep_agent", ["kb_search", "read_file"])

    def _boom():
        raise RuntimeError("工具构造失败")

    bound = bind_capability_tools(
        "deep_agent", {"kb_search": _boom, "read_file": lambda: _FakeTool("read_file")},
        registry=reg,
    )
    assert [tool_name(t) for t in bound.tools] == ["read_file"]


def test_split_denied():
    policy = ToolPolicy(capability_id="x", declared=frozenset({"a"}))
    allowed, denied = split_denied(policy, ["a", "b"])
    assert allowed == ["a"] and denied == ["b"]
    assert split_denied(None, ["a", "b"]) == (["a", "b"], [])  # 不启用即全放行


# ── 装配期过滤（模型看不到就没有调用机会）───────────────────────


def test_tool_agent_binds_only_declared_tools():
    allowed = _FakeTool("kb_search")
    forbidden = _FakeTool("read_file")
    policy = ToolPolicy(capability_id="demo", declared=frozenset({"kb_search"}))
    llm = _FakeLLM([AIMessage(content="好")])

    run_tool_agent(
        "问题", llm=llm, tools=[allowed, forbidden],
        system_prompt="sys", policy=policy,
    )
    assert llm.bound_names == ["kb_search"]  # 越权工具根本没交给模型


# ── 执行期校验（模型硬给越权工具名 → 拒绝 + 留痕）──────────────


def test_tool_agent_denies_undeclared_tool_call():
    forbidden = _FakeTool("read_file")
    policy = ToolPolicy(capability_id="demo", declared=frozenset({"kb_search"}))
    llm = _FakeLLM([
        AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": {"question": "x"}, "id": "c1"},
        ]),
        AIMessage(content="改用已授权手段作答"),
    ])

    out = run_tool_agent(
        "读一下文件", llm=llm, tools=[forbidden],
        system_prompt="sys", policy=policy,
    )
    assert forbidden.calls == []  # 越权工具确实没被执行
    assert out["tool_trace"][0]["denied"] is True
    assert "工具未授权" in out["tool_trace"][0]["result_head"]
    assert out["answer"] == "改用已授权手段作答"


def test_tool_agent_without_policy_keeps_legacy_behaviour():
    tool = _FakeTool("read_file")
    llm = _FakeLLM([
        AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": {"question": "x"}, "id": "c1"},
        ]),
        AIMessage(content="好"),
    ])
    out = run_tool_agent("读", llm=llm, tools=[tool], system_prompt="sys")
    assert tool.calls == ["read_file"]
    assert "denied" not in out["tool_trace"][0]
