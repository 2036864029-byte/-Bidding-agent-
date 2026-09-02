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
)


def test_default_registry_has_five_builtin_plus_echo():
    reset_default_registry()
    reg = get_default_registry()
    ids = reg.ids()
    for want in ("knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback", "echo_demo"):
        assert want in ids, f"缺能力 {want}"
    assert len(reg.all()) == 6


def test_capability_spec_fields():
    reg = get_default_registry()
    kq = reg.get("knowledge_qa")
    assert kq.node == "knowledge_qa"
    assert "kb_generation" in kq.model_slots
    assert "kb_search" in kq.tools
    echo = reg.get("echo_demo")
    assert echo.description.startswith("回显演示")


def test_register_overwrite_and_lookup():
    reg = CapabilityRegistry()
    reg.register(CapabilitySpec(id="x", node="x", description="x"))
    assert reg.get("x").node == "x"
    reg.register(CapabilitySpec(id="x", node="y", description="x"))
    assert reg.get("x").node == "y"  # 覆盖


def test_builtin_nodespecs_have_callable_entries():
    specs = builtin_specs()
    assert set(specs) == {"knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback"}
    for spec in specs.values():
        assert isinstance(spec, NodeSpec)
        assert callable(spec.entry)
        assert spec.fallback == "fallback"
