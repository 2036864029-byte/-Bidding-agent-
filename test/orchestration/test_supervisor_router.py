"""Supervisor 三级路由离线测试（方案 Phase 6 / Step 2 G-5）。

不触碰网络：L1 规则确定性；L2 注入 fake embedder（哈希桶 → 同文同向量）；L3 注入
fake llm_router。覆盖：规则命中 / 语义命中 / LLM 兜底 / 全 miss→fallback /
非法 L3 意图防误路由。
"""

from __future__ import annotations

import hashlib

from orchestration.supervisor import (
    classify_rules,
    classify_semantic,
    decide_handoff,
    route_question,
)
from orchestration.handoff import HandoffMeta


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

class _HashEmbedder:
    """确定性 fake：同文同向量（余弦=1.0），异文正交（余弦=0.0）。"""

    def embed_query(self, text: str):
        h = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)
        vec = [0.0] * 32
        vec[h % 32] = 1.0
        return vec


def test_semantic_hit_exact_example():
    # question 与 knowledge_qa 示例逐字相同 → 余弦 1.0 > 阈值 → 语义路由命中
    hit = classify_semantic(
        "评标委员会怎么组成？",
        embedder=_HashEmbedder(),
    )
    assert hit == "knowledge_qa"


def test_semantic_below_threshold_returns_none():
    # 无关文本（不同哈希桶，余弦≈0）→ 低于阈值 → None
    hit = classify_semantic(
        "今天天气怎么样",
        embedder=_HashEmbedder(),
    )
    assert hit is None


def test_semantic_disabled_without_embedder_skips():
    # 开关关 + 无注入 embedder → 直接 None（不建默认 API embedder）
    assert classify_semantic("评标委员会怎么组成？") is None


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


# ── W0-4：router 只构建一次 ───────────────────────────────────

def test_supervisor_node_builds_llm_router_once(monkeypatch):
    """每请求不得重建 L3 router——构建期会触发一次 LLM 预检测（审计 C4）。"""
    import orchestration.supervisor as sup_mod
    from langchain_core.messages import HumanMessage

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
