# 功能：节点级异常兜底（W2-1 自 agent/graph.py 下沉）。
"""节点级异常兜底 —— 任何未捕获异常 → 友好降级，不中断整图。

W2-1 下沉理由：`orchestration/main_graph.py` 原先 `from agent.graph import
_with_fallback`（上层依赖下层的实现细节，且与 `agent/graph.py` 的
`from orchestration.main_graph import ...` 构成循环）。兜底策略属基础设施，
放 `core` 后两条主图都依赖它，依赖方向变为单向。

约定：
  - 包装后签名固定 `(state, config=None) -> dict`（LangGraph 按签名注入 config）。
  - 是否把 config 继续下传由 `accepts_config` 判定（单参节点照旧 `node_fn(state)`）。
  - 节点抛错时返回 `branch="fallback"` + 友好文案 + `data.{error, failed_branch}`。
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from typing import Callable, Dict, Optional

from langchain_core.messages import AIMessage

from core.observability.metrics import record_branch_latency
from core.observability.tracing import observation_io, set_span_attrs
from core.observability.tracing import span as _span

from .codes import ErrorCode

logger = logging.getLogger(__name__)

#: 节点函数名 → 用户可读的能力名（降级文案用）
DEFAULT_BRANCH_LABELS: Dict[str, str] = {
    "node_knowledge_qa": "专业知识问答",
    "node_price_inquiry": "智能询价",
    "node_general_chat": "通用对话",
    "node_doc_qa": "文档问答",
}

_FALLBACK_TEMPLATE = (
    "抱歉，「{label}」功能暂时不可用，请稍后重试或尝试其他问题。\n\n"
    "您可以：\n"
    "  - 咨询招投标法规知识\n"
    "  - 查询历史中标价格\n"
    "  - 进行通用对话"
)


def accepts_config(node_fn: Callable) -> bool:
    """节点是否接受可选的 config 参数（决定 config 是否下传）。"""
    try:
        params = inspect.signature(node_fn).parameters.values()
    except (TypeError, ValueError):
        return False
    has_var = any(
        p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for p in params
    )
    positional = sum(
        p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for p in params
    )
    return has_var or positional >= 2


def _last_user_text(state: dict) -> str:
    """取会话中最后一条用户消息文本（E4-1 节点 span 的 input）。"""
    for m in reversed(list(state.get("messages") or [])):
        if type(m).__name__ == "HumanMessage" or getattr(m, "type", "") == "human":
            content = getattr(m, "content", "")
            if isinstance(content, str) and content.strip():
                return content
    return ""


def with_fallback(
    node_fn: Callable,
    *,
    branch_labels: Optional[Dict[str, str]] = None,
) -> Callable:
    """包装业务节点：任何未捕获异常 → 自动降级返回友好提示。

    Args:
        node_fn: 原始业务节点函数。
        branch_labels: 节点名 → 能力名映射（缺省用 DEFAULT_BRANCH_LABELS）。

    Returns:
        包装后的节点函数 `(state, config=None) -> dict`。
    """
    labels = branch_labels or DEFAULT_BRANCH_LABELS

    @functools.wraps(node_fn)
    def wrapped(state: dict, config: Optional[Dict] = None) -> dict:
        _t0 = time.perf_counter()
        node_name = getattr(node_fn, "__name__", "unknown")
        _question = _last_user_text(state)  # E4-1：节点 span 的 input
        # E0-3：节点 span（所有业务节点都经本包装器，单点覆盖全链路）
        with _span(f"node.{node_name}", **{"ztb.node": node_name}) as _sp:
            try:
                if accepts_config(node_fn):
                    result = node_fn(state, config)
                else:
                    result = node_fn(state)
            except Exception as e:
                label = labels.get(node_name, "当前")
                logger.error("节点 [%s] 执行失败: %s", node_name, e, exc_info=True)

                error_msg = _FALLBACK_TEMPLATE.format(label=label)
                latency = (time.perf_counter() - _t0) * 1000
                record_branch_latency("fallback", latency)
                set_span_attrs(
                    _sp,
                    **{"ztb.branch": "fallback", "ztb.latency_ms": round(latency, 2)},
                    **observation_io(_question, error_msg),
                )
                return {
                    "business_result": {
                        "branch": "fallback",
                        "answer": error_msg,
                        "data": {
                            # W2-3：统一错误码（出口经 to_reply 透出给 API/CLI）
                            "error_code": ErrorCode.NODE_FAILED.value,
                            "error": str(e)[:200],
                            "failed_branch": node_name,
                        },
                    },
                    "messages": [AIMessage(content=error_msg)],
                }

            branch = (result.get("business_result") or {}).get("branch", "?")
            latency = (time.perf_counter() - _t0) * 1000
            answer = (result.get("business_result") or {}).get("answer", "")
            set_span_attrs(
                _sp,
                **{"ztb.branch": branch, "ztb.latency_ms": round(latency, 2)},
                **observation_io(_question, answer),
            )
        record_branch_latency(branch, latency)
        return result

    return wrapped


def fallback_answer(label: str) -> str:
    """降级文案（供需要单独构造降级结果的调用方复用）。"""
    return _FALLBACK_TEMPLATE.format(label=label)


__all__ = [
    "DEFAULT_BRANCH_LABELS",
    "accepts_config",
    "with_fallback",
    "fallback_answer",
]
