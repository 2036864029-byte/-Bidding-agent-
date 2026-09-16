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
    assert len(price.examples) == 5  # 2026-09-16 起含采购需求示例（我要采购120台摄像头等）
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


# ── 能力开关可配置（enabled 从硬编码改为 Settings 覆盖）──────────────


def test_capability_enabled_override_forces_on(monkeypatch):
    """CAPABILITY_ENABLED 可把声明为 disabled 的能力拉起来（不改代码）。"""
    from agent.registry import (
        get_default_registry, reset_capability_overrides, reset_default_registry,
    )

    monkeypatch.setenv("CAPABILITY_ENABLED", "deep_agent")
    reset_capability_overrides()
    reset_default_registry()
    try:
        ids = [s.id for s in get_default_registry().enabled_specs()]
        assert "deep_agent" in ids
        # spec 的声明默认值不变（覆盖只影响"实际启用"，不篡改声明）
        assert get_default_registry().get("deep_agent").enabled is False
    finally:
        reset_capability_overrides()
        reset_default_registry()


def test_capability_disabled_override_forces_off(monkeypatch):
    """CAPABILITY_DISABLED 可停用内置能力（应急开关）。"""
    from agent.registry import (
        get_default_registry, reset_capability_overrides, reset_default_registry,
    )

    monkeypatch.setenv("CAPABILITY_DISABLED", "price_inquiry")
    reset_capability_overrides()
    reset_default_registry()
    try:
        assert "price_inquiry" not in [s.id for s in get_default_registry().enabled_specs()]
    finally:
        reset_capability_overrides()
        reset_default_registry()


def test_capability_override_full_width_comma(monkeypatch):
    """全角逗号也要能分隔（中文配置里很常见）。"""
    from agent.registry import (
        get_default_registry, reset_capability_overrides, reset_default_registry,
    )

    monkeypatch.setenv("CAPABILITY_ENABLED", "deep_agent，doc_agent")
    reset_capability_overrides()
    reset_default_registry()
    try:
        ids = [s.id for s in get_default_registry().enabled_specs()]
        assert {"deep_agent", "doc_agent"} <= set(ids)
    finally:
        reset_capability_overrides()
        reset_default_registry()


def test_capability_override_takes_precedence_over_disabled_list(monkeypatch):
    """同时在两个名单里时，启用优先（避免配置矛盾导致意外停用）。"""
    from agent.registry import is_capability_enabled, CapabilitySpec, reset_capability_overrides

    monkeypatch.setenv("CAPABILITY_ENABLED", "x")
    monkeypatch.setenv("CAPABILITY_DISABLED", "x")
    reset_capability_overrides()
    try:
        assert is_capability_enabled(CapabilitySpec(id="x", enabled=False)) is True
    finally:
        reset_capability_overrides()
