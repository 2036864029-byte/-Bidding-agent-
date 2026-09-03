"""graph._with_fallback config 转发测试（方案 Phase 6 / F-2）。

子图化后节点入口可选接收 config（thread_id）并下传子图 invoke；_with_fallback
须按节点签名透传 config，且不影响单参节点的兜底语义。
"""

from __future__ import annotations

from agent.graph import _accepts_config, _with_fallback


def _result(branch: str, answer: str) -> dict:
    return {"business_result": {"branch": branch, "answer": answer, "data": {}}}


def test_accepts_config_two_arg_and_single_arg():
    assert _accepts_config(lambda state, config=None: {}) is True
    assert _accepts_config(lambda state, config: {}) is True
    assert _accepts_config(lambda state: {}) is False


def test_with_fallback_forwards_config_to_two_arg_node():
    seen = {}

    def node(state, config=None):
        seen["config"] = config
        return _result("knowledge_qa", "ok")

    wrapped = _with_fallback(node)
    out = wrapped({"messages": []}, config={"configurable": {"thread_id": "t1"}})
    assert seen["config"] == {"configurable": {"thread_id": "t1"}}
    assert out["business_result"]["answer"] == "ok"


def test_with_fallback_single_arg_node_ignores_config():
    def node(state):
        return _result("general_chat", "hello")

    wrapped = _with_fallback(node)
    out = wrapped({"messages": []}, config={"configurable": {"thread_id": "t1"}})
    assert out["business_result"]["answer"] == "hello"


def test_with_fallback_error_still_degrades_with_config_present():
    def boom(state):
        raise RuntimeError("engine down")

    wrapped = _with_fallback(boom)
    out = wrapped({"messages": []}, config={"configurable": {"thread_id": "t1"}})
    assert out["business_result"]["branch"] == "fallback"
    assert "暂时不可用" in out["business_result"]["answer"]
    assert out["business_result"]["data"]["failed_branch"] == "boom"
