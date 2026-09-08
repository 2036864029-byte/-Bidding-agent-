"""
fallback — 兜底引导节点。

当意图不明时，列出可用功能清单并引导用户。

注意（W0-5）：本节点**不读取 state 里的 business_result**。该通道无 reducer
且随 checkpointer 跨轮持久——若复用其中的历史降级文案，同一 thread 的后续轮次
会返回上一轮的错误信息（审计 B5）。业务节点异常时的友好降级文案由
`_with_fallback` 直接产出（它就是该轮终态），无需本节点转述。
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage

from ..state import AgentState

logger = logging.getLogger(__name__)


def node_fallback(state: AgentState) -> dict:
    """兜底引导节点（意图不明时列出可用功能）。

    Args:
        state: AgentState（仅用于日志，不消费 business_result）。

    Returns:
        {"business_result": {...}, "messages": [AIMessage]}
    """
    messages = state.get("messages") or []
    logger.info("fallback: 意图不明确，引导用户（messages=%d）", len(messages))

    answer = (
        "🤔 我不太确定您想做什么，以下是我可以帮您的：\n\n"
        "  📚 专业知识问答 — 例如：「招标方式有哪些？」「评标委员会怎么组成？」\n"
        "  💰 智能询价 — 例如：「查一下XX产品的历史中标价格」\n"
        "   通用对话 — 例如：「你能做什么？」\n\n"
        "请告诉我您想使用哪个功能，或者直接提出问题。"
    )

    return {
        "business_result": {
            "branch": "fallback",
            "answer": answer,
            "data": None,
        },
        "messages": [AIMessage(content=answer)],
    }
