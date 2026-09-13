"""闸门测试（第 3 项）：带文件时是否升级 deep_agent。

判据本身是确定性的（关键词 + 可选"指向本文档"），规则在
`orchestration/routing_rules.yaml` 的 `agent_gate` 段。**不触碰网络**：
deep_agent 是否启用用 `CAPABILITY_ENABLED` 注入（conftest 会在用例间重置缓存）。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from agent.nodes.doc_qa import DOC_CONTEXT_MARKER, SESSION_DOC_MARKER
from orchestration.routing_rules import AgentGateRule, load_rules_cached, reset_rules_cache
from orchestration.supervisor import route_question, should_upgrade_to_agent


def _msgs(question: str, kind: str = "explicit"):
    marker = DOC_CONTEXT_MARKER if kind == "explicit" else SESSION_DOC_MARKER
    return [
        SystemMessage(content=f"{marker}\n[文件：a.pdf]\n第一章 内容"),
        HumanMessage(content=question),
    ]


@pytest.fixture
def agent_enabled(monkeypatch):
    """启用 deep_agent（能力开关 + 清缓存）——闸门才有落点。"""
    from agent.registry import reset_capability_overrides

    monkeypatch.setenv("CAPABILITY_ENABLED", "deep_agent")
    reset_capability_overrides()
    yield
    reset_capability_overrides()


@pytest.fixture(autouse=True)
def _fresh_rules():
    reset_rules_cache()
    yield
    reset_rules_cache()


# ── 能力未启用时闸门必须不生效（否则会把图路由到未接线的节点）────────


def test_gate_inert_when_agent_disabled():
    """默认 deep_agent 未接线 → 闸门即使命中关键词也不放行。"""
    assert should_upgrade_to_agent("这份标书的保证金比例合规吗？") is False
    assert route_question(_msgs("这份标书的保证金比例合规吗？")) == "doc_qa"


# ── 命中 / 不命中 ───────────────────────────────────────────────────


@pytest.mark.parametrize("question", [
    "这份标书要求的履约保证金比例，符合规定吗？",
    "这份标书的采购人有没有行政处罚记录？",
    "这份标书把 ITSS 认证设为资格条件，有依据吗？",
    "这份标书的招标代理机构有没有不良记录？",
])
def test_gate_routes_cross_source_questions_to_agent(question, agent_enabled):
    assert route_question(_msgs(question)) == "deep_agent"


@pytest.mark.parametrize("question", [
    "这份标书的计划工期是多少天？",
    "这份标书的评标委员会由几名成员组成？",
    "这份标书里质量保证金按多少比例预留？",
])
def test_gate_keeps_single_source_questions_on_doc_qa(question, agent_enabled):
    """纯文件问题不带跨源信号 → 留在单节点（那是 15.8s vs 0.5s 的差别）。"""
    assert route_question(_msgs(question)) == "doc_qa"


# ── 收紧档：require_doc_ref ─────────────────────────────────────────


def test_require_doc_ref_needs_document_pointer(agent_enabled):
    """开启 require_doc_ref 后，「跨源词但未指向本文档」不再放行。

    实测取舍（49 条集）：误入 0% 但漏答升到 17~30%（vs 宽松档 3.8% / 0~13%）。
    """
    rules_dir = load_rules_cached().agent_gate
    import re

    strict = AgentGateRule(
        enabled=True, keywords=rules_dir.keywords, require_doc_ref=True,
        doc_ref_pattern=re.compile("这份|本标书|本文件|文件里"),
    )

    # 有跨源词但不指向本文档（行业性问题）→ 不放行
    assert should_upgrade_to_agent("近期中标金额超过一千万的项目有哪些？",
                                  rules=_FakeRules(strict)) is False
    # 指向本文档 + 跨源词 → 放行
    assert should_upgrade_to_agent("这份标书的保证金比例合规吗？",
                                  rules=_FakeRules(strict)) is True


class _FakeRules:
    """只带 agent_gate 的最小规则替身（should_upgrade_to_agent 只读这一段）。"""

    def __init__(self, gate: AgentGateRule) -> None:
        self.agent_gate = gate


def test_gate_disabled_by_rule_flag():
    """YAML 里 enabled: false → 整条闸门关闭（可一键回退到"带文件全走 doc_qa"）。"""
    off = AgentGateRule(enabled=False, keywords=("合规",))
    assert should_upgrade_to_agent("这份标书合规吗？", rules=_FakeRules(off)) is False


# ── L0 优先级与回归保护 ─────────────────────────────────────────────


def test_session_file_still_yields_to_l1_rules(agent_enabled):
    """L0 弱档（自动附着）仍让 L1 强规则先判 —— 闸门不得改变这条性质。"""
    assert route_question(_msgs("你好", kind="session")) == "general_chat"


def test_session_file_can_also_upgrade(agent_enabled):
    """过 L1 之后的会话文件档同样可升级（两档共用同一闸门）。"""
    assert route_question(_msgs("这份标书的保证金比例合规吗？", kind="session")) == "deep_agent"


def test_yaml_gate_config_is_loaded():
    """规则来自 YAML（改阈值/词表不改代码）：至少要有 keys 且 enabled 是布尔。"""
    gate = load_rules_cached().agent_gate

    assert isinstance(gate.enabled, bool)
    assert len(gate.keywords) >= 10
    assert isinstance(gate.require_doc_ref, bool)
