"""节点护栏钩子 — with_guardrails 装饰器（方案 Phase 1 / B-7）。

通用后置护栏：业务节点正常返回后，对 business_result 依次执行 validators。
- 全部通过 → 原样返回（零侵入）。
- 任一失败 → 默认在 business_result.data 附 guardrails 报告（不阻断回答）；
  raise_on_fail=True 时抛 GuardrailError 交由外层 _with_fallback 降级。

Validator 签名：(business_result: dict) -> (ok: bool, detail: str)

设计说明：不改节点返回结构（branch/answer/messages 契约不变），护栏只做
"检查 + 附注"。knowledge_qa 工具链路的引用校验（R1~R7）即护栏实例，由
chain.py 内联执行；本装饰器用于后续 Phase 把护栏声明式挂到任意节点。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence, Tuple

from langchain_core.messages import AIMessage

from .state import AgentState

logger = logging.getLogger(__name__)

Validator = Callable[[Dict[str, Any]], Tuple[bool, str]]


class GuardrailError(RuntimeError):
    """raise_on_fail=True 且护栏失败时抛出。"""


@dataclass
class GuardrailReportItem:
    index: int
    ok: bool
    detail: str


def _run_validators(
    business_result: Dict[str, Any],
    validators: Sequence[Validator],
) -> List[GuardrailReportItem]:
    reports: List[GuardrailReportItem] = []
    for i, validator in enumerate(validators, 1):
        try:
            ok, detail = validator(business_result)
        except Exception as exc:  # 护栏自身异常按失败处理，不阻断
            logger.warning("guardrail[%d] 执行异常: %s", i, exc)
            ok, detail = False, f"validator_error: {exc}"
        reports.append(GuardrailReportItem(index=i, ok=bool(ok), detail=str(detail)))
    return reports


def with_guardrails(
    node_fn: Callable[[AgentState], dict],
    validators: Sequence[Validator],
    *,
    raise_on_fail: bool = False,
) -> Callable[[AgentState], dict]:
    """包装节点：返回后跑 validators；失败仅附注（raise_on_fail 才抛错）。

    Args:
        node_fn: 业务节点 (AgentState) -> dict。
        validators: 护栏校验器列表。
        raise_on_fail: 任一失败是否抛 GuardrailError（默认只附注不阻断）。

    Returns:
        包装后的节点函数，接口与原节点一致。
    """

    def wrapped(state: AgentState) -> dict:
        result = node_fn(state)
        business_result = result.get("business_result")
        if business_result is None:
            return result  # 非业务节点返回（如 router），护栏不适用

        reports = _run_validators(business_result, validators)
        if all(r.ok for r in reports):
            return result

        # 失败 → 附注 guardrails 报告到 data（保持 branch/answer/messages 不变）
        data = business_result.setdefault("data", {})
        data["guardrails"] = [
            {"index": r.index, "ok": r.ok, "detail": r.detail} for r in reports
        ]

        if raise_on_fail:
            failed = [r for r in reports if not r.ok]
            raise GuardrailError(f"guardrails failed: {failed}")
        return result

    return wrapped
