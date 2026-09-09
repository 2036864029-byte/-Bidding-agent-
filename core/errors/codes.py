"""统一错误码（W2-3）— 业务 / 系统 / 模型 / 依赖四类分级。

编号约定：`<类别前缀>_<4 位数字>`，便于前端与告警按前缀分流：
  biz_1xxx  业务错误（输入不合法、无有效实体、检索为空、护栏不通过）→ 4xx
  sys_2xxx  系统错误（节点异常、路由失败、未捕获）→ 5xx
  model_3xxx 模型错误（LLM / Embedding / Reranker 调用失败）→ 502/503
  dep_4xxx  依赖错误（Milvus / MySQL 不可用）→ 503

对外契约：`AgentReply.error_code` / `ChatResponse.error_code` 与错误信封
`{"error": {"code", "message", "request_id"}}` 均取本枚举的 value。
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """错误码（str 枚举，可直接 JSON 序列化）。"""

    # ── 业务错误 ──
    INVALID_REQUEST = "biz_1001"
    NO_VALID_ENTITY = "biz_1002"
    RETRIEVAL_EMPTY = "biz_1003"
    GUARDRAIL_FAILED = "biz_1004"
    UNSUPPORTED_CAPABILITY = "biz_1005"

    # ── 系统错误 ──
    NODE_FAILED = "sys_2001"
    ROUTE_FAILED = "sys_2002"
    INTERNAL = "sys_2500"

    # ── 模型错误 ──
    LLM_FAILED = "model_3001"
    EMBEDDING_FAILED = "model_3002"
    RERANK_FAILED = "model_3003"

    # ── 依赖错误 ──
    MILVUS_UNAVAILABLE = "dep_4001"
    MYSQL_UNAVAILABLE = "dep_4002"
    CONFIG_INVALID = "dep_4003"


class ErrorCategory(str, Enum):
    """错误分类（告警分流用）。"""

    BUSINESS = "business"
    SYSTEM = "system"
    MODEL = "model"
    DEPENDENCY = "dependency"


_CATEGORY_BY_PREFIX = {
    "biz": ErrorCategory.BUSINESS,
    "sys": ErrorCategory.SYSTEM,
    "model": ErrorCategory.MODEL,
    "dep": ErrorCategory.DEPENDENCY,
}


def category_of(code: "ErrorCode | str") -> ErrorCategory:
    """按编号前缀判定类别（未知前缀归为系统错误）。"""
    value = code.value if isinstance(code, ErrorCode) else str(code)
    prefix = value.split("_", 1)[0]
    return _CATEGORY_BY_PREFIX.get(prefix, ErrorCategory.SYSTEM)


__all__ = ["ErrorCode", "ErrorCategory", "category_of"]
