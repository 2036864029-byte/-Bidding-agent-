"""guardrail_hooks.with_guardrails 离线测试（方案 Phase 1 / B-7）。"""

from __future__ import annotations

import pytest

from agent.guardrail_hooks import GuardrailError, with_guardrails


def _node(**over):
    base = {"business_result": {"branch": "knowledge_qa", "answer": "答", "data": {"sources": []}}}
    base.update(over)
    return base


def _wrap(validators, **kw):
    node = with_guardrails(lambda state: _node(), validators, **kw)
    return node({})


def test_all_pass_no_attachment():
    out = _wrap([lambda br: (True, "ok1"), lambda br: (True, "ok2")])
    assert out == _node()  # 结构完全不变


def test_failure_attaches_report_without_blocking():
    def bad(br):
        return False, "引用不足"

    out = _wrap([lambda br: (True, "ok"), bad])
    assert out["business_result"]["answer"] == "答"  # 不阻断
    guards = out["business_result"]["data"]["guardrails"]
    assert guards == [
        {"index": 1, "ok": True, "detail": "ok"},
        {"index": 2, "ok": False, "detail": "引用不足"},
    ]


def test_raise_on_fail_raises():
    with pytest.raises(GuardrailError):
        _wrap([lambda br: (False, "x")], raise_on_fail=True)


def test_validator_exception_treated_as_failure():
    def boom(br):
        raise ValueError("validator crashed")

    out = _wrap([boom])
    assert out["business_result"]["data"]["guardrails"][0]["ok"] is False
    assert "validator_error" in out["business_result"]["data"]["guardrails"][0]["detail"]


def test_non_business_result_passthrough():
    node = with_guardrails(lambda state: {"router_intent": "fallback"}, [lambda br: (False, "x")])
    assert node({}) == {"router_intent": "fallback"}
