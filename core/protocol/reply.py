# 功能：AgentReply 出口薄协议 + business_result → AgentReply 纯映射。
"""出口薄协议（方案 Phase 2 / C-1，附录 C-0）。

业务节点内部返回结构**保持不变**；只在出口层（API/CLI）经 ``to_reply`` 转成
统一的 AgentReply。不含 price 渲染模板（那些是 price 域资产，P3 随引擎下沉）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from core.errors.codes import ErrorCode

ReplyType = Literal[
    "kb_answer", "price_records", "chat", "doc_qa", "deep_agent", "safety", "fallback"
]

# 节点 branch → 回复类型
_BRANCH_TO_REPLY: Dict[str, ReplyType] = {
    "knowledge_qa": "kb_answer",
    "price_inquiry": "price_records",
    "general_chat": "chat",
    "doc_qa": "doc_qa",
    # 第 3 项上线（CAPABILITY_ENABLED=deep_agent）后新增：**必须有独立类型** ——
    # 否则会落到 `.get(branch, "fallback")` 的兜底，前端把「agent 成功作答」
    # 当降级提示渲染（实测：intent=deep_agent、error_code=None，reply_type 却是 fallback）。
    "deep_agent": "deep_agent",
    "safety": "safety",
    "fallback": "fallback",
}


class AgentReply(BaseModel):
    """对外统一回复模型（结构化，前端/CLI 直接消费）。"""

    reply_type: ReplyType
    answer: str = ""
    citations: List[Dict[str, Any]] = Field(
        default_factory=list, description="知识问答引用（【来源N】结构化，可为空）"
    )
    data: Optional[Dict[str, Any]] = Field(
        default=None, description="分支附加数据（records/诊断等，可为空）"
    )
    trace: Dict[str, Any] = Field(
        default_factory=dict, description="intent / latency_ms / guardrail 等诊断"
    )
    error_code: Optional[str] = Field(
        None,
        description="统一错误码（W2-3；正常回答为 None，见 core.errors.codes.ErrorCode）",
    )


def _infer_error_code(business_result: Dict[str, Any], branch: str) -> Optional[str]:
    """从 business_result 推断错误码（显式优先，其次按已知标记推断）。"""
    data = business_result.get("data")
    if not isinstance(data, dict):
        return None

    explicit = data.get("error_code")
    if explicit:
        return str(explicit)

    if branch == "knowledge_qa":
        validation = data.get("citation_validation")
        if isinstance(validation, dict):
            if validation.get("is_refusal"):
                return ErrorCode.RETRIEVAL_EMPTY.value
            if validation.get("all_passed") is False:
                return ErrorCode.GUARDRAIL_FAILED.value

    if business_result.get("query_type") == "unified_guidance":
        return ErrorCode.NO_VALID_ENTITY.value
    if business_result.get("query_type") == "capability_boundary":
        return ErrorCode.UNSUPPORTED_CAPABILITY.value

    return None


def to_reply(
    business_result: Dict[str, Any],
    intent: str = "unknown",
    latency_ms: Optional[int] = None,
) -> AgentReply:
    """把节点 business_result 纯映射为 AgentReply（不改节点内部结构）。

    Args:
        business_result: 节点返回的 {"branch", "answer", "data", ...}。
        intent: router_intent（写入 trace）。
        latency_ms: 端到端耗时（毫秒），None 时不写入。
    """
    branch = business_result.get("branch", "fallback")
    reply_type = _BRANCH_TO_REPLY.get(branch, "fallback")
    data = business_result.get("data")
    data = data if isinstance(data, dict) else None

    citations: List[Dict[str, Any]] = []
    if data and isinstance(data.get("citations"), list):
        citations = [
            c for c in data["citations"] if isinstance(c, dict)
        ]

    trace: Dict[str, Any] = {"intent": intent}
    if latency_ms is not None:
        trace["latency_ms"] = latency_ms
    if data and isinstance(data.get("guardrails"), list):
        trace["guardrail_count"] = len(data["guardrails"])

    return AgentReply(
        reply_type=reply_type,
        answer=str(business_result.get("answer", "")),
        citations=citations,
        data=data,
        trace=trace,
        error_code=_infer_error_code(business_result, branch),
    )


__all__ = ["AgentReply", "ReplyType", "to_reply"]
