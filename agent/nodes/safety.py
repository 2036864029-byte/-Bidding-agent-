"""agent.nodes.safety — 内容安全前置（W3-4）。

在进入路由前对用户问题做**确定性**检查（零 LLM 成本，绕过一切模型层）：
  1. 超长问题（> Settings.max_question_chars，默认 2000 字）→ 拒绝；
  2. 明确的可执行有害请求（伪造/篡改/删记录/收买评标等）→ 拒绝。

采用**动作导向**关键词：只拦「帮我伪造… / 删掉处罚记录 / 篡改…」这类可执行的
有害指令；不拦「围标串标 / 行贿受贿」等**合法话题词**（那些是知识库正常覆盖的
专业问题，误伤即能力受损）。关键词表可经 env `SAFETY_HARMFUL_KEYWORDS`
（逗号分隔）扩展。

命中 → ``business_result`` branch=safety + 统一安全回复 + ``safety_reason``；
未命中 → 返回空 dict（透传，由下游路由节点继续）。以 ``with_safety_gate`` 挂在
路由节点入口，不改变图拓扑（无新状态键、无新边）。
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Dict, Optional

from langchain_core.messages import AIMessage

from core.observability.metrics import record_guardrail_fail

logger = logging.getLogger(__name__)

SAFETY_REFUSAL_ANSWER = (
    "抱歉，该问题超出我能回答的范围。我可以为您解答招投标法规知识、企业信息与"
    "中标情报等合规问题，请换个问题。"
)
SAFETY_OVERSIZED_ANSWER = "抱歉，问题过长，请精简后重新提问（建议 2000 字以内）。"

# 安全闸门短路时的路由哨兵：图的条件边把该值映射到 END（见 graph.py / main_graph.py）。
SAFETY_BLOCKED_ROUTE = "safety_blocked"

_DEFAULT_MAX_CHARS = 2000


def _last_human_question(messages) -> str:
    for m in reversed(messages or []):
        if getattr(m, "type", None) == "human":
            return str(m.content or "")
    return ""


def _safety_settings():
    from core.config.settings import Settings

    return Settings()


def check_safety(question: str, *, settings: Optional[Any] = None) -> Optional[str]:
    """确定性内容安全检查。返回拒绝原因（None=放行）。

    Args:
        question: 用户问题。
        settings: 注入的 Settings（测试用；None 时经 _safety_settings 惰性读 .env）。

    Returns:
        命中时返回原因标识：``oversized`` / ``harmful_request``；未命中返回 None。
    """
    if not question:
        return None
    q = question.strip()
    if not q:
        return None

    settings = settings or _safety_settings()
    max_chars = getattr(settings, "max_question_chars", _DEFAULT_MAX_CHARS)
    if len(q) > int(max_chars):
        return "oversized"

    for kw in getattr(settings, "safety_harmful_keywords", []) or []:
        if kw and kw in q:
            logger.info("safety: 有害执行请求命中 keyword=%r", kw)
            return "harmful_request"
    return None


def with_safety_gate(node: Callable, *, route_key: str = "router_intent") -> Callable:
    """内容安全前置包装器：进入路由前检查问题，命中 → 短路返回安全回复。

    Args:
        node: 路由节点（router / supervisor），``(state, config=None) -> dict``。
        route_key: 短路时写入的路由通道键——单图 ``router_intent``；supervisor 主图
            传 ``supervisor_route``（图的条件边把 ``SAFETY_BLOCKED_ROUTE`` 映射到 END，
            避免短路后继续路由到业务节点覆盖安全结果）。

    Returns:
        包装后的节点。未命中时原样调用 node（零行为变化）。
    """

    @functools.wraps(node)
    def wrapper(state: dict, config: Optional[dict] = None) -> dict:
        question = _last_human_question(state.get("messages"))
        reason = check_safety(question)
        if reason is None:
            return node(state, config) if config is not None else node(state)

        record_guardrail_fail("safety_blocked")
        logger.warning("safety 拦截 reason=%s question=%.60s", reason, question)
        answer = SAFETY_OVERSIZED_ANSWER if reason == "oversized" else SAFETY_REFUSAL_ANSWER
        return {
            "business_result": {
                "branch": "safety",
                "answer": answer,
                "data": {"safety_reason": reason},
            },
            "messages": [AIMessage(content=answer)],
            "router_intent": "fallback",
            route_key: SAFETY_BLOCKED_ROUTE,
        }

    return wrapper


__all__ = [
    "SAFETY_REFUSAL_ANSWER",
    "SAFETY_OVERSIZED_ANSWER",
    "SAFETY_BLOCKED_ROUTE",
    "check_safety",
    "with_safety_gate",
]
