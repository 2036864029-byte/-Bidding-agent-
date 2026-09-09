"""W2-1 接线数据驱动化测试：规则文件 + registry 驱动 agent 集。

验收（计划 §4 Wave 1/2）：新增第 6 个能力只需 registry 登记 + 节点 entry 登记，
不改 `main_graph.py` / `handoff.py`。
"""

from __future__ import annotations

from agent.nodes.base import register, reset_node_registry
from agent.registry import CapabilityRegistry, CapabilitySpec, _seed_builtin_specs
from orchestration.main_graph import build_supervisor_graph
from orchestration.registry_wiring import agent_ids, resolve_agent_nodes
from orchestration.routing_rules import RULES_PATH, load_rules
from orchestration.supervisor import classify_rules, decide_handoff

# ── 规则文件 ─────────────────────────────────────────────────

def test_rules_file_exists_and_parses():
    assert RULES_PATH.exists(), "routing_rules.yaml 缺失（L1 与 handoff 规则将不生效）"
    rules = load_rules()
    assert [r.id for r in rules.l1] == ["greeting", "project_number", "doc_qa"]
    assert rules.handoff and rules.handoff[0].from_agent == "price_inquiry"
    assert rules.handoff[0].to == "knowledge_qa"
    assert "法律效力" in rules.handoff[0].keywords


def test_classify_rules_uses_loaded_rules():
    """L1 命中与 max_len 语义由数据决定。"""
    assert classify_rules("你好") == "general_chat"
    assert classify_rules("你好" + "啊" * 60) is None      # 超 max_len 不命中
    assert classify_rules("[350001]FJGGZY[GK]2024013 中标情况") == "price_inquiry"
    assert classify_rules("帮我分析这个文档") == "doc_qa"
    assert classify_rules("什么是公开招标？") is None


def test_decide_handoff_uses_loaded_rules():
    meta = decide_handoff(
        "price_inquiry", "XX公司中标了哪些项目？中标通知书有什么法律效力？"
    )
    assert meta is not None and meta.to == "knowledge_qa"
    assert decide_handoff("price_inquiry", "XX公司中标了哪些项目？") is None
    assert decide_handoff("knowledge_qa", "中标通知书有什么法律效力？") is None


# ── registry 驱动 agent 集 ───────────────────────────────────

def test_resolve_agent_nodes_covers_enabled_capabilities():
    nodes = resolve_agent_nodes()
    assert set(nodes) == set(agent_ids())
    assert {"knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback"} <= set(nodes)


def test_new_capability_wires_without_editing_main_graph_or_handoff():
    """新增能力 = registry 登记 + base 登记 entry，supervisor 自动接线。"""
    reg = CapabilityRegistry()
    for spec in _seed_builtin_specs():
        reg.register(spec)
    reg.register(CapabilitySpec(
        id="echo_demo", node="echo_demo",
        description="回显演示（W2-1 接线验证）",
        route_desc="回显演示",
        route_tool_doc="回显演示：用户要求复述一句话时使用",
    ))

    def node_echo(state, config=None):
        return {
            "business_result": {"branch": "echo_demo", "answer": "回显", "data": {}},
            "messages": [],
        }

    try:
        register("echo_demo", node_echo)
        assert "echo_demo" in agent_ids(reg)

        graph = build_supervisor_graph(
            registry=reg, llm_router=lambda state: {"router_intent": "fallback"},
        )
        assert "echo_demo" in set(graph.get_graph().nodes)
    finally:
        reset_node_registry()


def test_supervisor_graph_nodes_match_registry():
    """supervisor 节点集 == registry enabled 能力（两条主图同一来源）。"""
    graph = build_supervisor_graph(
        llm_router=lambda state: {"router_intent": "fallback"},
    )
    nodes = set(graph.get_graph().nodes)
    assert set(agent_ids()) <= nodes
    assert "supervisor" in nodes and "handoff_decider" in nodes
