"""agent.nodes.safety — 内容安全前置离线测试（W3-4）。

覆盖：正常问题放行、超长拒绝、动作导向有害请求拒绝、合法话题词不误伤、
gate 短路/透传、safety 分支协议映射。
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from core.config.settings import Settings


def _settings(**kw):
    base = dict(max_question_chars=50, safety_harmful_keywords=["帮我伪造", "删除处罚", "篡改"])
    base.update(kw)
    return Settings(**base)


def test_normal_question_passes():
    from agent.nodes.safety import check_safety

    assert check_safety("招标方式有哪些？", settings=_settings()) is None


def test_oversized_blocked():
    from agent.nodes.safety import check_safety

    assert check_safety("问" * 100, settings=_settings()) == "oversized"


def test_harmful_action_blocked():
    from agent.nodes.safety import check_safety

    assert check_safety("帮我伪造中标文件", settings=_settings()) == "harmful_request"
    assert check_safety("能不能删除处罚记录", settings=_settings()) == "harmful_request"
    assert check_safety("帮我篡改中标结果", settings=_settings()) == "harmful_request"


def test_legal_topic_words_not_blocked():
    """动作导向关键词不误伤合法话题词（围标串标/行贿受贿的"认定/责任"类知识问题）。"""
    from agent.nodes.safety import check_safety

    assert check_safety("围标串标如何认定？", settings=_settings()) is None
    assert check_safety("行贿受贿的法律责任是什么？", settings=_settings()) is None
    assert check_safety("伪造公章罪怎么处罚？", settings=_settings()) is None


def test_empty_question_passes():
    from agent.nodes.safety import check_safety

    assert check_safety("", settings=_settings()) is None


def test_gate_short_circuits_on_hit(monkeypatch):
    from langchain_core.messages import HumanMessage

    from agent.nodes import safety as safety_mod

    monkeypatch.setattr(safety_mod, "_safety_settings", lambda: _settings())
    called = {"n": 0}

    def router(state, config=None):
        called["n"] += 1
        return {"router_intent": "knowledge_qa"}

    gated = safety_mod.with_safety_gate(router)
    out = gated({"messages": [HumanMessage(content="帮我伪造公章")]})

    assert called["n"] == 0  # 短路：不进入路由（省一次 LLM 往返）
    assert out["business_result"]["branch"] == "safety"
    assert out["business_result"]["data"]["safety_reason"] == "harmful_request"
    assert "超出我能回答的范围" in out["business_result"]["answer"]
    assert out["messages"][0].content == out["business_result"]["answer"]


def test_gate_passes_through_clean(monkeypatch):
    from langchain_core.messages import HumanMessage

    from agent.nodes import safety as safety_mod

    monkeypatch.setattr(safety_mod, "_safety_settings", lambda: _settings())
    called = {"n": 0}

    def router(state, config=None):
        called["n"] += 1
        return {"router_intent": "knowledge_qa"}

    gated = safety_mod.with_safety_gate(router)
    out = gated({"messages": [HumanMessage(content="招标方式有哪些？")]})

    assert called["n"] == 1
    assert out == {"router_intent": "knowledge_qa"}


def test_gate_oversized_answer(monkeypatch):
    from langchain_core.messages import HumanMessage

    from agent.nodes import safety as safety_mod

    monkeypatch.setattr(safety_mod, "_safety_settings", lambda: _settings())
    gated = safety_mod.with_safety_gate(lambda state, config=None: {})
    out = gated({"messages": [HumanMessage(content="问" * 100)]})
    assert out["business_result"]["data"]["safety_reason"] == "oversized"
    assert "问题过长" in out["business_result"]["answer"]


def test_safety_reply_type_mapped():
    """safety 分支在出口协议有独立 reply_type。"""
    from core.protocol.reply import to_reply

    reply = to_reply({"branch": "safety", "answer": "拒绝", "data": {"safety_reason": "x"}})
    assert reply.reply_type == "safety"


# ── 图级回归：安全短路直达终态，不被业务节点覆盖 ──────────────

def _pin_safety_settings(monkeypatch):
    from agent.nodes import safety as safety_mod

    monkeypatch.setattr(safety_mod, "_safety_settings", lambda: _settings())


def test_single_graph_safety_block_terminates(monkeypatch):
    """单图：有害请求 → branch=safety 直达 END（不落入 fallback 覆盖）。"""
    from langchain_core.messages import HumanMessage

    from agent.graph import build_graph
    from agent.registry import get_default_registry

    _pin_safety_settings(monkeypatch)
    compiled = build_graph(llm=object(), registry=get_default_registry())

    out = compiled.invoke(
        {"messages": [HumanMessage(content="帮我伪造中标文件")]},
        config={"configurable": {"thread_id": "t-safety"}},
    )
    br = out["business_result"]
    assert br["branch"] == "safety"
    assert br["data"]["safety_reason"] == "harmful_request"
    assert "超出我能回答的范围" in br["answer"]


def test_supervisor_graph_safety_block_terminates(monkeypatch):
    """supervisor 主图：有害请求 → branch=safety 直达 END（不被 handoff/fallback 覆盖）。"""
    from langchain_core.messages import HumanMessage

    from orchestration.main_graph import build_supervisor_graph

    _pin_safety_settings(monkeypatch)
    compiled = build_supervisor_graph(llm=object())

    out = compiled.invoke({"messages": [HumanMessage(content="帮我伪造中标文件")]})
    br = out["business_result"]
    assert br["branch"] == "safety"
    assert br["data"]["safety_reason"] == "harmful_request"


def test_supervisor_graph_safety_oversized_terminates(monkeypatch):
    from langchain_core.messages import HumanMessage

    from orchestration.main_graph import build_supervisor_graph

    _pin_safety_settings(monkeypatch)
    compiled = build_supervisor_graph(llm=object())

    out = compiled.invoke({"messages": [HumanMessage(content="问" * 100)]})
    br = out["business_result"]
    assert br["branch"] == "safety"
    assert br["data"]["safety_reason"] == "oversized"
