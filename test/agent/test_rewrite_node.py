"""W3-1 多轮查询改写节点测试。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from agent.nodes.rewrite import build_rewrite_node, looks_like_followup, rewrite_question

# ── 指代预判 ─────────────────────────────────────────────────

def _msgs(*contents: str):
    return [HumanMessage(content=c) for c in contents]


def test_looks_like_followup_needs_prior_turns_and_hint():
    assert looks_like_followup(_msgs("第一问")) is False            # 首问
    assert looks_like_followup(_msgs("A公司中标了哪些项目？", "那它呢？")) is True
    assert looks_like_followup(_msgs("A公司中标了哪些项目？", "换成B公司呢？")) is True
    assert looks_like_followup(_msgs("A公司中标了哪些项目？", "什么是公开招标？")) is False


def test_looks_like_followup_ignores_non_human_last():
    messages = [HumanMessage(content="A公司中标了哪些项目？"), AIMessage(content="回答。")]
    assert looks_like_followup(messages) is False


# ── rewrite_question ─────────────────────────────────────────

def _fake_llm(content: str):
    return RunnableLambda(lambda _pv: AIMessage(content=content))


def test_rewrite_question_returns_llm_output():
    out = rewrite_question("那它呢？", "用户: A公司中标了哪些项目？", _fake_llm("A公司有没有不良记录？"))
    assert out == "A公司有没有不良记录？"


def test_rewrite_question_falls_back_on_error():
    def _boom(_pv):
        raise RuntimeError("llm down")

    out = rewrite_question("那它呢？", "历史", RunnableLambda(_boom))
    assert out == "那它呢？"


# ── 节点 ─────────────────────────────────────────────────────

def test_node_skips_llm_for_self_contained_question():
    """自包含问题零成本直通（不调用 LLM、不改消息）。"""
    called = {"n": 0}

    def fake_factory():
        called["n"] += 1
        return _fake_llm("不应被调用")

    node = build_rewrite_node(llm_factory=fake_factory)
    messages = _msgs("A公司中标了哪些项目？", "什么是公开招标？")
    out = node({"messages": messages})

    assert out == {}
    assert called["n"] == 0


def test_node_rewrites_followup_in_place_preserving_id():
    """追问被改写并**原位替换**（同 id），router/业务节点看到归一化问题。"""
    node = build_rewrite_node(llm=_fake_llm("A公司有没有不良记录？"))
    messages = _msgs("A公司中标了哪些项目？", "那它呢？")
    last = messages[-1]

    out = node({"messages": messages})

    replaced = out["messages"][0]
    assert isinstance(replaced, HumanMessage)
    assert replaced.content == "A公司有没有不良记录？"
    assert replaced.id == last.id


def test_node_fail_soft_on_llm_error():
    """改写失败 → 原样返回，不阻断本轮。"""

    def _boom(_pv):
        raise RuntimeError("down")

    node = build_rewrite_node(llm=RunnableLambda(_boom))
    messages = _msgs("A公司中标了哪些项目？", "那它呢？")

    assert node({"messages": messages}) == {}
