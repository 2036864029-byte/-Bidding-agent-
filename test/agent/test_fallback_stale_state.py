"""fallback 节点跨轮状态隔离测试（Wave 0 / W0-5）。

审计 B5：`business_result` 通道无 reducer 且随 checkpointer 跨轮持久。旧实现
在 `data.failed_branch` 非空时直接复用 `business_result.answer`，导致同一 thread
的第二轮 fallback 返回上一轮的错误文案。
"""

from __future__ import annotations

from agent.nodes.fallback import node_fallback

_STALE_STATE = {
    "messages": [],
    "business_result": {
        "branch": "fallback",
        "answer": "抱歉，「智能询价」功能暂时不可用，请稍后重试。",
        "data": {"error": "engine down", "failed_branch": "node_price_inquiry"},
    },
}


def test_fallback_ignores_stale_business_result():
    """上一轮的异常降级文案不得泄漏到本轮。"""
    out = node_fallback(_STALE_STATE)
    answer = out["business_result"]["answer"]
    assert "暂时不可用" not in answer
    assert "不太确定您想做什么" in answer


def test_fallback_output_is_pure_guidance():
    out = node_fallback(_STALE_STATE)
    assert out["business_result"]["branch"] == "fallback"
    assert out["business_result"]["data"] is None
    assert out["messages"][0].content == out["business_result"]["answer"]


def test_fallback_without_state_business_result():
    """空 state 也必须给出引导语（不因缺字段报错）。"""
    out = node_fallback({"messages": []})
    assert "不太确定您想做什么" in out["business_result"]["answer"]
