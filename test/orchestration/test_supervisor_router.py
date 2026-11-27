"""Supervisor 三级路由离线测试（方案 Phase 6 / Step 2 G-5）。

不触碰网络：L1 规则确定性；L2 注入 fake embedder（哈希桶 → 同文同向量）；L3 注入
fake llm_router。覆盖：规则命中 / 语义命中 / LLM 兜底 / 全 miss→fallback /
非法 L3 意图防误路由。
"""

from __future__ import annotations

import pytest

from _support.fakes import HashEmbeddings
from orchestration.handoff import HandoffMeta
from orchestration.supervisor import (
    classify_rules,
    classify_semantic,
    decide_handoff,
    route_question,
    route_question_full,
)


@pytest.fixture(autouse=True)
def _semantic_off_by_default(monkeypatch):
    """本文件默认关 L2：这些用例断言的是 L1/L3 与级联顺序，L2 打开会引入真实
    embedder 调用（违反离线约束）且可能抢先命中、改变预期落点。
    需要验证语义层的用例自行把开关打开。
    """
    import orchestration.supervisor as sup

    monkeypatch.setattr(sup, "SEMANTIC_ROUTE_ENABLED", False)
    sup.reset_semantic_cache()

# ── L1 规则快路径 ─────────────────────────────────────────────

def test_rule_greeting_to_general_chat():
    assert classify_rules("你好") == "general_chat"
    assert classify_rules("您好，请问你能做什么？") == "general_chat"


def test_rule_project_number_to_price_inquiry():
    assert classify_rules("[350001]FJGGZY[GK]2024013 中标情况") == "price_inquiry"
    assert classify_rules("查一下 AH2024-001 项目") == "price_inquiry"


def test_rule_doc_qa_keywords():
    assert classify_rules("帮我分析这个文档") == "doc_qa"
    assert classify_rules("解读一下这份合同") == "doc_qa"


def test_rule_ambiguous_returns_none():
    # 普通业务问题不该被 L1 误路由（留给 L2/L3）
    assert classify_rules("XX公司中标了哪些项目？") is None
    assert classify_rules("什么是公开招标？") is None


# ── L2 语义相似度（注入 fake embedder）────────────────────────
# 假件取自 test/_support/fakes.py（P1-3 集中）：HashEmbeddings 的语义正合此处所需
# —— 同文余弦 1.0、异文正交 0.0，便于精确断言"命中/不命中"。


def test_semantic_hit_exact_example():
    # question 与 knowledge_qa 示例逐字相同 → 余弦 1.0 > 阈值 → 语义路由命中
    hit = classify_semantic(
        "评标委员会怎么组成？",
        embedder=HashEmbeddings(),
    )
    assert hit == "knowledge_qa"


def test_semantic_below_threshold_returns_none():
    # 无关文本（不同哈希桶，余弦≈0）→ 低于阈值 → None
    # ⚠️ 文本与任一能力示例桶相撞（假 embedder 的哈希巧合）会误命中；
    # 2026-09-16 price_inquiry 新增采购需求示例后"今天天气怎么样"与其同桶，改用"今天天气不错"。
    hit = classify_semantic(
        "今天天气不错",
        embedder=HashEmbeddings(),
    )
    assert hit is None


def test_semantic_switch_off_skips_without_embedder(monkeypatch):
    """开关关 + 无注入 embedder → 直接 None（不建默认 API embedder）。"""
    import orchestration.supervisor as sup

    monkeypatch.setattr(sup, "SEMANTIC_ROUTE_ENABLED", False)
    assert classify_semantic("评标委员会怎么组成？") is None


def test_semantic_example_vectors_cached(monkeypatch):
    """示例向量预计算：第二次调用只多 1 次嵌入（问题本身），这是语义层能默认开启的前提。"""
    import orchestration.supervisor as sup

    monkeypatch.setattr(sup, "SEMANTIC_ROUTE_ENABLED", True)
    sup.reset_semantic_cache()
    emb = HashEmbeddings()

    first = classify_semantic("评标委员会怎么组成？", embedder=emb)
    calls_after_first = emb.query_calls
    second = classify_semantic("评标委员会怎么组成？", embedder=emb)

    assert first == second == "knowledge_qa"
    assert calls_after_first > 1  # 首次：问题 + 全部能力示例
    assert emb.query_calls - calls_after_first == 1  # 第二次：只嵌入问题


def test_semantic_degrades_when_embedder_fails(monkeypatch):
    """嵌入不可用 → 返回 None（让过 L2 交 L3），不抛错阻断路由。"""
    import orchestration.supervisor as sup

    monkeypatch.setattr(sup, "SEMANTIC_ROUTE_ENABLED", True)
    sup.reset_semantic_cache()

    class BrokenEmbeddings(HashEmbeddings):
        def embed_query(self, text: str):
            raise RuntimeError("embedding api down")

    assert classify_semantic("评标委员会怎么组成？", embedder=BrokenEmbeddings()) is None


# ── 三级级联 route_question ──────────────────────────────────

def test_route_cascade_l1_wins_first():
    # L1 命中优先于 L3（即便 fake router 返回别的）
    fake_router = lambda state: {"router_intent": "fallback"}
    from langchain_core.messages import HumanMessage

    r = route_question([HumanMessage(content="你好")], llm_router=fake_router)
    assert r == "general_chat"


def test_route_cascade_l3_llm_fallback(monkeypatch):
    from langchain_core.messages import HumanMessage

    fake_router = lambda state: {"router_intent": "price_inquiry"}
    r = route_question(
        [HumanMessage(content="科大讯飞股份有限公司中标了哪些项目？")],
        llm_router=fake_router,
    )
    assert r == "price_inquiry"


def test_route_cascade_l3_invalid_intent_falls_back():
    from langchain_core.messages import HumanMessage

    fake_router = lambda state: {"router_intent": "bogus_intent"}
    r = route_question([HumanMessage(content="随便问问")], llm_router=fake_router)
    assert r == "fallback"


def test_route_cascade_all_miss_no_llm_falls_back():
    from langchain_core.messages import HumanMessage

    # llm 与 llm_router 均缺 → 无法 L3 → fallback（不建真 LLM）
    r = route_question([HumanMessage(content="随便问问")], llm=None)
    assert r == "fallback"


# ── L0 文档上下文（B2：本轮带了上传文件 → 直接 doc_qa）─────────


def _doc_messages(question: str):
    """构造「本轮注入了文档 + 用户问题」的消息序列（D-0-2 注入形状）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.nodes.doc_qa import DOC_CONTEXT_MARKER

    return [
        SystemMessage(content=f"{DOC_CONTEXT_MARKER}\n[文件：a.pdf]\n第一章 内容"),
        HumanMessage(content=question),
    ]


def test_doc_context_wins_over_l1_rule():
    """带文件时即便问题命中 L1 问候规则，也必须走 doc_qa。"""
    assert route_question(_doc_messages("你好")) == "doc_qa"


def test_doc_context_wins_over_l3_llm():
    """B2 原始症状：带 file_ids 问法规问题，曾被 LLM 判成 knowledge_qa、文件被忽略。"""
    fake_router = lambda state: {"router_intent": "knowledge_qa"}

    r = route_question(
        _doc_messages("政府采购工程建设项目应适用什么法律？"),
        llm_router=fake_router,
    )

    assert r == "doc_qa"


def test_no_doc_context_routes_as_before():
    """无文档上下文时不受影响：仍按三级级联交给 L3。"""
    from langchain_core.messages import HumanMessage

    fake_router = lambda state: {"router_intent": "knowledge_qa"}

    r = route_question(
        [HumanMessage(content="政府采购工程建设项目应适用什么法律？")],
        llm_router=fake_router,
    )

    assert r == "knowledge_qa"


def test_stale_doc_context_does_not_trigger_l0():
    """上一轮注入的文档上下文不得触发本轮 L0（与 doc_qa 节点同口径）。"""
    from langchain_core.messages import AIMessage, HumanMessage

    fake_router = lambda state: {"router_intent": "knowledge_qa"}
    messages = _doc_messages("第一个问题") + [
        AIMessage(content="回答"),
        HumanMessage(content="第二个问题"),   # 本轮未注入文档
    ]

    assert route_question(messages, llm_router=fake_router) == "knowledge_qa"


def test_doc_context_ignored_when_doc_qa_disabled():
    """doc_qa 未启用时 L0 自动失效，退回三级级联（不许路由到不存在的 agent）。"""
    from agent.registry import CapabilityRegistry, CapabilitySpec

    reg = CapabilityRegistry()
    reg.register(CapabilitySpec(id="knowledge_qa", node="knowledge_qa"))
    fake_router = lambda state: {"router_intent": "knowledge_qa"}

    r = route_question(
        _doc_messages("这份文件说明了什么"),
        registry=reg,
        llm_router=fake_router,
    )

    assert r == "knowledge_qa"


# ── A3：会话自动附着是弱信号，不得压过 L1 强规则 ──────────────


def _session_doc_messages(question: str):
    """构造「服务端自动附着本会话文件 + 用户问题」的消息序列。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.nodes.doc_qa import SESSION_DOC_MARKER

    return [
        SystemMessage(content=f"{SESSION_DOC_MARKER}\n[文件：a.pdf]\n第一章 内容"),
        HumanMessage(content=question),
    ]


def test_session_doc_does_not_override_l1_rules():
    """回归：上传过文件的会话里问项目编号，必须仍走 price_inquiry（B2 症状的反向）。"""
    for q, expected in [
        ("查一下 AH2024-001 项目", "price_inquiry"),
        ("[350001]FJGGZY[GK]2024013 中标情况", "price_inquiry"),
        ("你好", "general_chat"),
    ]:
        assert route_question(_session_doc_messages(q)) == expected, q


def test_session_doc_routes_to_doc_qa_when_no_strong_signal():
    """没有 L1 强信号时，自动附着的文件才生效（否则 A3 等于没做）。"""
    fake_router = lambda state: {"router_intent": "knowledge_qa"}

    r = route_question(
        _session_doc_messages("投标保证金是多少？"),
        llm_router=fake_router,
    )

    assert r == "doc_qa"


def test_session_doc_still_hits_l1_doc_rule():
    """L1 的 doc_qa 关键词（"分析文档"）命中在先，结果同样是 doc_qa。"""
    assert route_question(_session_doc_messages("帮我分析这个文档")) == "doc_qa"


def test_explicit_doc_context_still_outranks_l1():
    """显式 file_ids 是强信号：即便问题命中 L1 问候，也走 doc_qa（A2 行为不变）。"""
    assert route_question(_doc_messages("你好")) == "doc_qa"


# ── W0-4：router 只构建一次 ───────────────────────────────────

def test_supervisor_node_builds_llm_router_once(monkeypatch):
    """每请求不得重建 L3 router——构建期会触发一次 LLM 预检测（审计 C4）。"""
    from langchain_core.messages import HumanMessage

    import orchestration.supervisor as sup_mod

    calls = {"build": 0}

    def fake_build(llm, registry=None):
        calls["build"] += 1
        return lambda state: {"router_intent": "fallback"}

    monkeypatch.setattr(sup_mod, "_build_llm_router", fake_build)

    node = sup_mod.build_supervisor_node(llm=object())
    assert calls["build"] == 1, "节点构建时应只创建一次 router"

    state = {"messages": [HumanMessage(content="随便问问")]}
    node(state)
    node(state)
    assert calls["build"] == 1, "多次调用节点不得重复构建 router"


def test_supervisor_node_respects_injected_llm_router(monkeypatch):
    """注入 llm_router 时不得再构建（离线测试零 LLM 调用）。"""
    import orchestration.supervisor as sup_mod

    def boom(llm, registry=None):
        raise AssertionError("注入 llm_router 时不应构建默认 router")

    monkeypatch.setattr(sup_mod, "_build_llm_router", boom)
    node = sup_mod.build_supervisor_node(
        llm=None, llm_router=lambda state: {"router_intent": "general_chat"}
    )
    assert callable(node)


# ── Phase 2：route_question_full / 追问消解（2026-09-16）──────


def test_route_question_full_recovers_resolved_from_l3_messages():
    """L3 返回的 messages 原位替换 → RouteResult.resolved_question 还原（Phase 2）。"""
    from langchain_core.messages import HumanMessage

    last = HumanMessage(content="具体介绍一下？")
    last.id = "msg-1"
    fake_router = lambda state: {  # noqa: E731
        "router_intent": "knowledge_qa",
        "messages": [HumanMessage(content="招标方式有哪些？请逐一介绍", id="msg-1")],
    }

    r = route_question_full(
        [HumanMessage(content="招标方式有哪些？"), last],
        llm_router=fake_router,
    )

    assert r.intent == "knowledge_qa"
    assert r.resolved_question == "招标方式有哪些？请逐一介绍"


def test_route_question_full_l1_hit_no_resolved():
    """L1 快路径 → resolved_question 恒 None（只处理自包含确定性场景）。"""
    from langchain_core.messages import HumanMessage

    r = route_question_full(
        [HumanMessage(content="你好")],
        llm_router=lambda state: {"router_intent": "x"},  # noqa: E731
    )
    assert r.intent == "general_chat"
    assert r.resolved_question is None


def test_route_question_full_l3_no_replacement_none():
    """L3 未返回 messages 替换 → resolved_question=None（自包含/旧式 fake）。"""
    from langchain_core.messages import HumanMessage

    r = route_question_full(
        [HumanMessage(content="什么是公开招标？")],
        llm_router=lambda state: {"router_intent": "knowledge_qa"},  # noqa: E731
    )
    assert r.intent == "knowledge_qa"
    assert r.resolved_question is None


def test_supervisor_node_applies_resolved_replacement():
    """supervisor_node 应用 L3 追问消解 → messages 同 id 原位替换。"""
    from langchain_core.messages import HumanMessage

    from orchestration.supervisor import build_supervisor_node

    last = HumanMessage(content="具体介绍一下？")
    last.id = "msg-2"
    fake_router = lambda state: {  # noqa: E731
        "router_intent": "knowledge_qa",
        "messages": [HumanMessage(content="招标方式有哪些？请逐一介绍", id="msg-2")],
    }
    node = build_supervisor_node(llm=None, llm_router=fake_router)

    out = node({"messages": [HumanMessage(content="招标方式有哪些？"), last]})

    assert out["supervisor_route"] == "knowledge_qa"
    assert out["router_intent"] == "knowledge_qa"
    assert out["messages"][0].content == "招标方式有哪些？请逐一介绍"
    assert out["messages"][0].id == "msg-2"


# ── Phase 3：澄清式反问 ──────────────────────────────────────


def test_route_question_full_clarify_from_l3():
    """L3 返回 router_intent=clarify + 候选 → RouteResult 澄清决策。"""
    from langchain_core.messages import HumanMessage

    fake_router = lambda state: {  # noqa: E731
        "router_intent": "clarify",
        "business_result": {
            "branch": "clarify",
            "data": {"candidates": ["招标方式有哪些？请逐一介绍", "公开招标和邀请招标的区别"]},
        },
    }
    r = route_question_full(
        [HumanMessage(content="具体介绍一下？")],
        llm_router=fake_router,
    )
    assert r.intent == "clarify"
    assert r.needs_clarification is True
    assert r.candidates == ["招标方式有哪些？请逐一介绍", "公开招标和邀请招标的区别"]


def test_supervisor_node_routes_clarify_with_candidates():
    """supervisor_node 把澄清决策落到 business_result（图条件边路由到 clarify 节点）。"""
    from langchain_core.messages import HumanMessage

    from orchestration.supervisor import build_supervisor_node

    fake_router = lambda state: {  # noqa: E731
        "router_intent": "clarify",
        "business_result": {
            "branch": "clarify",
            "data": {"candidates": ["招标方式有哪些？请逐一介绍"]},
        },
    }
    node = build_supervisor_node(llm=None, llm_router=fake_router)

    out = node({"messages": [HumanMessage(content="具体介绍一下？")]})

    assert out["supervisor_route"] == "clarify"
    assert out["router_intent"] == "clarify"
    assert out["business_result"]["branch"] == "clarify"
    assert out["business_result"]["data"]["candidates"] == ["招标方式有哪些？请逐一介绍"]


# ── handoff 决策矩阵 ─────────────────────────────────────────


def test_handoff_price_explanation_keyword_to_knowledge():
    meta = decide_handoff("price_inquiry", "XX公司中标了哪些项目？中标通知书有什么法律效力？", {})
    assert isinstance(meta, HandoffMeta)
    assert meta.from_agent == "price_inquiry"
    assert meta.to == "knowledge_qa"
    assert meta.payload["sub_question"]


def test_handoff_price_without_keyword_none():
    assert decide_handoff("price_inquiry", "XX公司中标了哪些项目？", {}) is None


def test_handoff_non_price_intent_none():
    assert decide_handoff("knowledge_qa", "中标通知书有什么法律效力？", {}) is None
    assert decide_handoff("general_chat", "你好", {}) is None
