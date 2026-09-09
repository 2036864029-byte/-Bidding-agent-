"""agent.guardrails.output — 输出侧护栏离线测试（W3-4）。

覆盖：各分支合法输出通过、必填/answer 非空/data 契约失败、降级标记与指标、
包装器透传/降级/同步 messages、无 business_result 节点透传、与 _with_fallback 组合。
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from agent.guardrails.output import (
    apply_output_guardrails,
    validate_business_result,
    with_output_guardrails,
)


def _br(branch, answer="回答", data=None):
    return {"branch": branch, "answer": answer, "data": data}


def test_valid_branches_pass():
    """各分支合法输出（含引导/拒答/空记录等正常出口）通过校验。"""
    cases = [
        _br("knowledge_qa", data={"sources": []}),
        _br("price_inquiry", data={"records": []}),
        _br("general_chat", data=None),
        _br("doc_qa", data={"status": "no_doc_attached"}),
        _br("doc_agent", data={"tool_trace": []}),
        _br("deep_agent", data={"trace": {"plan": []}}),
        _br("safety", data={"safety_reason": "x"}),
        _br("fallback", data=None),
    ]
    for br in cases:
        assert validate_business_result(br) == [], br["branch"]


def test_missing_top_level_field_fails():
    failed = validate_business_result({"answer": "x"})
    assert any(f["rule_id"] == "output_required" for f in failed)


def test_empty_answer_fails():
    failed = validate_business_result(_br("general_chat", answer="  "))
    assert any(f["rule_id"] == "output_answer_empty" for f in failed)


def test_missing_data_key_fails():
    failed = validate_business_result(_br("knowledge_qa", data={}))
    assert any(f["rule_id"] == "output_data_missing" for f in failed)


def test_data_non_dict_fails():
    failed = validate_business_result(_br("price_inquiry", data=None))
    assert any(f["rule_id"] == "output_data_shape" for f in failed)


def test_non_dict_business_result_fails():
    failed = validate_business_result(None)
    assert failed and failed[0]["rule_id"] == "output_shape"


def test_apply_degrades_marks_and_metric(monkeypatch):
    import agent.guardrails.output as output_mod

    seen = []
    monkeypatch.setattr(output_mod, "record_guardrail_fail", lambda b: seen.append(b))

    degraded = apply_output_guardrails(_br("general_chat", answer=""))

    assert degraded["answer"] == "抱歉，本次回答未通过输出校验，请换个问法重试。"
    assert degraded["data"]["output_guardrail"]["degraded"] is True
    assert degraded["data"]["output_guardrail"]["failed"][0]["rule_id"] == "output_answer_empty"
    assert seen == ["output_general_chat"]


def test_apply_passthrough_when_valid():
    br = _br("general_chat", answer="ok")
    assert apply_output_guardrails(br) is br


def test_wrapper_valid_node_passthrough():
    def node(state, config=None):
        return {"business_result": _br("general_chat"), "messages": []}

    wrapped = with_output_guardrails(node)
    out = wrapped({"messages": []})
    assert out["business_result"]["answer"] == "回答"


def test_wrapper_degrades_and_syncs_message():
    from langchain_core.messages import AIMessage

    def node(state, config=None):
        return {
            "business_result": _br("general_chat", answer=""),
            "messages": [AIMessage(content="")],
        }

    wrapped = with_output_guardrails(node)
    out = wrapped({"messages": []})
    assert "未通过输出校验" in out["business_result"]["answer"]
    assert out["messages"][0].content == out["business_result"]["answer"]


def test_wrapper_passthrough_without_business_result():
    def node(state, config=None):
        return {"router_intent": "x"}

    wrapped = with_output_guardrails(node)
    assert wrapped({"messages": []}) == {"router_intent": "x"}


def test_composed_with_fallback_degrades_empty_answer():
    """与 _with_fallback 组合：外层输出护栏在内层异常兜底之后再校验。"""
    from core.errors.fallback import with_fallback

    def bad_node(state, config=None):
        return {"business_result": {"branch": "general_chat", "answer": "", "data": None}}

    composed = with_output_guardrails(with_fallback(bad_node))
    out = composed({"messages": []})
    assert "未通过输出校验" in out["business_result"]["answer"]
