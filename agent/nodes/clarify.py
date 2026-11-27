"""agent.nodes.clarify — 澄清式反问节点（Phase 3，2026-09-16；P5 增加挂起续跑）。

三档兜底（工业界标准）的第三档：改写/路由置信度不足
（`RouterDecision.needs_clarification` 且候选非空）时，反问 + 一键候选，
**替代硬拒答**——不猜、不回"暂无相关内容"，把候选意图列给用户点。

两种交付形态（同一份候选数据，差别只在"断点由谁保存"）：

- **同步终态（默认，`HITL_CLARIFY=false`）**：直接返回反问文案 + 候选 chips，
  前端展示后由用户重新发问。无状态、无需 resume 接口。
- **挂起续跑（`HITL_CLARIFY=true`）**：调 LangGraph `interrupt()` 把**整个图挂起**，
  checkpoint 落盘（thread_id 维度），API 返回 `interrupted=true` + 候选；
  客户端 `POST /chat/resume` 带答复 → `Command(resume=答复)` 从断点续跑，
  本节点在 `interrupt()` 处拿到答复，作为**新的用户消息**交回 supervisor 重新路由。
  这就是"中断—续跑"：中间不重跑前面的节点，不丢 thread 上下文。

数据流：路由节点（merged 理解）已把候选写入 `business_result.data.candidates`
（原问题在 `.original`）；本节点只做格式化 / 挂起，并把 `data.resolved` 置位，
供图的 `clarify` 出边判断"续跑回来该回 supervisor 还是结束"。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from ..state import AgentState

logger = logging.getLogger(__name__)

CLARIFY_ANSWER = "您是想问以下哪个？"
CLARIFY_EMPTY_ANSWER = "抱歉，我没能确定您的具体意图，请换一种说法再试一次。"
CLARIFY_MAX_CANDIDATES = 3

#: 同一轮内允许的挂起次数（1 = 只问一次；续跑后仍不确定则直接终态，不连环追问）
MAX_CLARIFY_ROUNDS = 1


def _hitl_enabled() -> bool:
    """读挂起开关；配置不可用时退化为同步终态（不因配置问题改变交付形态）。"""
    try:
        from core.config.settings import Settings

        return bool(Settings().hitl_clarify)
    except Exception:  # noqa: BLE001
        return False


def _resume_text(value: Any) -> str:
    """把 resume 载荷归一为答复文本。

    接受三种常见形态：裸字符串；``{"answer": "..."}``；``{"candidate": "..."}``
    （前端点候选 chips）。空/取消 → 空串，由调用方按"用户放弃"处理。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("answer", "candidate", "text", "value"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return ""
    return str(value).strip()


def node_clarify(state: AgentState) -> dict:
    """澄清节点：读路由节点写入的候选 → 反问（同步终态 / 挂起等答复）。

    Args:
        state: 含 `business_result.data.{candidates, original, round}`（路由节点写入）。

    Returns:
        - 首轮且开启挂起：调 `interrupt()`（挂起期间本节点不返回）；
        - 续跑回来：携带用户答复的 HumanMessage + `data.resolved=True`（图回 supervisor）；
        - 用户取消：终态文案 + `data.resolved=True`（图回 END）。
    """
    br = state.get("business_result") or {}
    data = br.get("data") if isinstance(br.get("data"), dict) else {}
    candidates = [str(c) for c in (data.get("candidates") or []) if str(c).strip()]
    candidates = candidates[:CLARIFY_MAX_CANDIDATES]
    original = data.get("original")

    if candidates and _hitl_enabled():
        from langgraph.types import interrupt  # 局部导入：未启用时不依赖运行期断点

        round_no = int(data.get("round") or 0)
        if round_no < MAX_CLARIFY_ROUNDS:
            logger.info("clarify 挂起等待用户答复（断点已随 checkpoint 落盘）")
            value = interrupt({
                "type": "clarification",
                "question": CLARIFY_ANSWER,
                "candidates": candidates,
                "original": original,
            })
            text = _resume_text(value)
            if text:
                logger.info("clarify 续跑：收到答复 %r，交回 supervisor 重新路由", text[:60])
                return {
                    "business_result": {
                        "branch": "clarify",
                        "answer": "",
                        "data": {"resolved": True, "round": round_no + 1,
                                 "answer": text, "original": original},
                    },
                    "messages": [HumanMessage(content=text)],
                }
            logger.info("clarify 续跑：用户未给出答复，按放弃处理")
            return {
                "business_result": {
                    "branch": "clarify",
                    "answer": CLARIFY_EMPTY_ANSWER,
                    "data": {"resolved": True, "round": round_no + 1,
                             "cancelled": True, "original": original},
                },
                "messages": [AIMessage(content=CLARIFY_EMPTY_ANSWER)],
            }

    answer = CLARIFY_ANSWER if candidates else CLARIFY_EMPTY_ANSWER
    return {
        "business_result": {
            "branch": "clarify",
            "answer": answer,
            "data": {
                "candidates": candidates,
                "original": original,
                "resolved": False,  # 同步终态：等前端答完重发，不是续跑
            },
        },
        "messages": [AIMessage(content=answer)],
    }


__all__ = [
    "CLARIFY_ANSWER",
    "CLARIFY_EMPTY_ANSWER",
    "CLARIFY_MAX_CANDIDATES",
    "MAX_CLARIFY_ROUNDS",
    "node_clarify",
]
