"""agent.guardrails.output — 输出侧护栏：business_result 形状校验（W3-4）。

背景：knowledge_qa 已有 R1~R7 引用护栏（失败可重试/拒答）；其余分支（price /
general_chat / doc_qa / fallback / deep_agent / doc_agent）无输出侧校验。本模块
给全部分支补「格式/必填」校验（审计 C6 未接线机制的扩面落地）：

  - 顶层必填：branch / answer；
  - answer 非空（strip 后）；
  - data 为 dict（分支声明 data_required 时）且必填 data 键存在。

失败 → ``record_guardrail_fail`` 指标 + ``data.output_guardrail`` 标记（可观测）
+ 统一降级文案（可降级）。重试由分支自身承担（knowledge 引用护栏已有；本层不自动
重跑节点，避免重复 SQL/LLM 副作用）。
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Dict, List, Optional

from core.observability.metrics import record_guardrail_fail

logger = logging.getLogger(__name__)

# 输出降级文案（校验失败时替换 answer；保留 data 供诊断）
_OUTPUT_DEGRADE_ANSWER = "抱歉，本次回答未通过输出校验，请换个问法重试。"

# 分支 → 输出契约：required=顶层必填；data_required=data 必填键（None=不要求 data 形状）。
# 字段以各分支真实返回为准（见 agent/nodes/*/subgraph.py 与 node.py 的 data 形状），
# 只列恒存在的键，避免对引导/拒答/超时等正常出口误报。
BRANCH_OUTPUT_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "knowledge_qa": {"required": ["branch", "answer"], "data_required": ["sources"]},
    "price_inquiry": {"required": ["branch", "answer"], "data_required": ["records"]},
    "general_chat": {"required": ["branch", "answer"], "data_required": None},
    "doc_qa": {"required": ["branch", "answer"], "data_required": ["status"]},
    "doc_agent": {"required": ["branch", "answer"], "data_required": ["tool_trace"]},
    "deep_agent": {"required": ["branch", "answer"], "data_required": ["trace"]},
    "safety": {"required": ["branch", "answer"], "data_required": None},
    "fallback": {"required": ["branch", "answer"], "data_required": None},
    # Phase 3（2026-09-16）：澄清式反问 —— 候选必须随 data 下发（前端渲染 chips）
    "clarify": {"required": ["branch", "answer"], "data_required": ["candidates"]},
}


def validate_business_result(business_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """校验 business_result 输出形状，返回失败规则列表（空=通过）。

    每个失败项：{"rule_id", "name", "detail"}。
    """
    if not isinstance(business_result, dict):
        return [{
            "rule_id": "output_shape",
            "name": "business_result 非 dict",
            "detail": type(business_result).__name__,
        }]

    branch = str(business_result.get("branch", ""))
    contract = BRANCH_OUTPUT_CONTRACTS.get(branch)
    required = ["branch", "answer"] if contract is None else contract["required"]
    data_required = None if contract is None else contract["data_required"]

    failed: List[Dict[str, Any]] = []

    # 1. 顶层必填
    for key in required:
        if key not in business_result:
            failed.append({
                "rule_id": "output_required",
                "name": f"缺少顶层字段 {key}",
                "detail": branch,
            })

    # 2. answer 非空
    answer = business_result.get("answer", "")
    if not isinstance(answer, str) or not answer.strip():
        failed.append({
            "rule_id": "output_answer_empty",
            "name": "answer 为空",
            "detail": branch,
        })

    # 3. data 形状 + 必填 data 键
    data = business_result.get("data")
    if data_required is not None:
        if not isinstance(data, dict):
            failed.append({
                "rule_id": "output_data_shape",
                "name": "data 非 dict",
                "detail": branch,
            })
        else:
            for key in data_required:
                if key not in data:
                    failed.append({
                        "rule_id": "output_data_missing",
                        "name": f"data 缺 {key}",
                        "detail": branch,
                    })

    return failed


def apply_output_guardrails(business_result: Dict[str, Any]) -> Dict[str, Any]:
    """校验并降级：通过 → 原样返回；失败 → 指标 + data 标记 + 替换 answer。

    Returns:
        处理后的 business_result（失败时 answer 为统一降级文案，data 保留诊断）。
    """
    failed = validate_business_result(business_result)
    if not failed:
        return business_result

    branch = str(business_result.get("branch", "?"))
    record_guardrail_fail(f"output_{branch}")
    logger.warning("输出护栏失败 branch=%s rules=%s",
                   branch, [f["rule_id"] for f in failed])

    data = business_result.get("data")
    data = dict(data) if isinstance(data, dict) else {}
    data["output_guardrail"] = {"failed": failed, "degraded": True}
    return {**business_result, "answer": _OUTPUT_DEGRADE_ANSWER, "data": data}


def with_output_guardrails(node: Callable) -> Callable:
    """输出侧护栏包装器：节点返回后校验 business_result，失败降级但保留诊断。

    Args:
        node: 业务节点 ``(state, config=None) -> dict``。

    Returns:
        包装后的节点。无 business_result 的节点（rewrite/router 等）原样透传。
    """

    @functools.wraps(node)
    def wrapper(state: dict, config: Optional[dict] = None) -> dict:
        result = node(state, config) if config is not None else node(state)
        business_result = result.get("business_result")
        if not isinstance(business_result, dict):
            return result
        degraded = apply_output_guardrails(business_result)
        if degraded is business_result:
            return result
        # 同步末条 AIMessage，保持 messages 与 business_result.answer 一致
        messages = result.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if getattr(last, "type", None) == "ai":
                from langchain_core.messages import AIMessage

                try:
                    messages = [*messages[:-1], AIMessage(content=_OUTPUT_DEGRADE_ANSWER)]
                except Exception:  # noqa: BLE001  消息构造异常不影响降级主路径
                    messages = messages
        return {**result, "business_result": degraded, "messages": messages}

    return wrapper


__all__ = [
    "BRANCH_OUTPUT_CONTRACTS",
    "validate_business_result",
    "apply_output_guardrails",
    "with_output_guardrails",
]
