"""P4 能力注册表 + NodeSpec 离线测试（方案 Phase 4 / P4-1、P4-3）。"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from agent.nodes.base import NodeSpec, builtin_specs
from agent.registry import (
    CapabilityRegistry,
    CapabilitySpec,
    get_default_registry,
    reset_default_registry,
    seed_builtins,
)


def test_default_registry_has_five_builtin_plus_echo():
    reset_default_registry()
    reg = get_default_registry()
    ids = reg.ids()
    for want in ("knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback",
                 "echo_demo", "doc_agent", "deep_agent"):
        assert want in ids, f"缺能力 {want}"
    assert len(reg.all()) == 8


def test_default_enabled_candidates_equal_five_builtins():
    """L2 门禁：router 候选集 = 现有 5 分支（echo_demo / doc_agent / deep_agent 预留默认 disabled）。"""
    reset_default_registry()
    reg = get_default_registry()
    enabled = [s.id for s in reg.enabled_specs()]
    assert enabled == ["knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback"]
    assert not reg.get("echo_demo").enabled
    assert not reg.get("doc_agent").enabled
    assert not reg.get("deep_agent").enabled


def test_capability_spec_fields():
    reg = get_default_registry()
    kq = reg.get("knowledge_qa")
    assert kq.node == "knowledge_qa"
    assert "kb_generation" in kq.model_slots
    assert "kb_search" in kq.tools
    echo = reg.get("echo_demo")
    assert echo.description.startswith("回显演示")
    assert echo.graph_node == "echo_demo"


def test_route_copy_present_for_builtins():
    """每个 enabled 能力都带路由原文（system prompt 条目 + tool 描述 + 示例）。"""
    reg = get_default_registry()
    for spec in reg.enabled_specs():
        assert spec.route_desc, f"{spec.id} 缺 route_desc"
        assert spec.route_tool_doc, f"{spec.id} 缺 route_tool_doc"
    price = reg.get("price_inquiry")
    assert len(price.examples) == 3
    assert price.route_desc.startswith("结构化业务数据查询")
    assert price.route_tool_doc.startswith("结构化业务数据查询（三大核心能力）")


def test_register_overwrite_and_lookup():
    reg = CapabilityRegistry()
    reg.register(CapabilitySpec(id="x", node="x", description="x"))
    assert reg.get("x").node == "x"
    reg.register(CapabilitySpec(id="x", node="y", description="x"))
    assert reg.get("x").node == "y"  # 覆盖


def test_graph_node_defaults_to_id():
    reg = CapabilityRegistry()
    reg.register(CapabilitySpec(id="x", description="x"))
    assert reg.get("x").graph_node == "x"


def test_seed_builtins_reusable_on_fresh_registry():
    """seed_builtins 可在任意注册表复刻默认候选集（demo/测试用）。"""
    reg = CapabilityRegistry()
    seed_builtins(reg)
    assert [s.id for s in reg.enabled_specs()] == [
        "knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback",
    ]


def test_builtin_nodespecs_have_callable_entries():
    specs = builtin_specs()
    assert set(specs) == {"knowledge_qa", "price_inquiry", "general_chat", "doc_qa",
                          "doc_agent", "deep_agent", "fallback"}
    for spec in specs.values():
        assert isinstance(spec, NodeSpec)
        assert callable(spec.entry)
        assert spec.fallback == "fallback"
