"""agent.nodes.clarify — 澄清式反问节点（Phase 3，2026-09-16）。

三档兜底（工业界标准）的第三档：改写/路由置信度不足
（`RouterDecision.needs_clarification` 且候选非空）时，反问 + 一键候选，
**替代硬拒答**——不猜、不回"暂无相关内容"，把候选意图列给用户点。

数据流：路由节点（merged 理解）已把候选写入 `business_result.data.candidates`
（原问题在 `.original`）；本节点只做格式化——补 `answer` 文案 + `messages` 终态。
与 knowledge_qa/price 等业务节点同签名 `(AgentState) -> dict`，经
`with_output_guardrails(_with_fallback(...))` 包装入图。
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage

from ..state import AgentState

logger = logging.getLogger(__name__)

CLARIFY_ANSWER = "您是想问以下哪个？"
CLARIFY_EMPTY_ANSWER = "抱歉，我没能确定您的具体意图，请换一种说法再试一次。"
CLARIFY_MAX_CANDIDATES = 3


def node_clarify(state: AgentState) -> dict:
    """澄清节点：读路由节点写入的候选 → 反问 + 一键候选终态。

    Args:
        state: 含 `business_result.data.{candidates, original}`（路由节点写入）。

    Returns:
        {"business_result": {"branch": "clarify", "answer", "data"},
         "messages": [AIMessage(content=answer)]}
    """
    br = state.get("business_result") or {}
    data = br.get("data") if isinstance(br.get("data"), dict) else {}
    candidates = [str(c) for c in (data.get("candidates") or []) if str(c).strip()]
    candidates = candidates[:CLARIFY_MAX_CANDIDATES]

    answer = CLARIFY_ANSWER if candidates else CLARIFY_EMPTY_ANSWER
    return {
        "business_result": {
            "branch": "clarify",
            "answer": answer,
            "data": {
                "candidates": candidates,
                "original": data.get("original"),
            },
        },
        "messages": [AIMessage(content=answer)],
    }


__all__ = [
    "CLARIFY_ANSWER",
    "CLARIFY_EMPTY_ANSWER",
    "CLARIFY_MAX_CANDIDATES",
    "node_clarify",
]
