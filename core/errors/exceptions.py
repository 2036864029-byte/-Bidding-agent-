"""应用异常族（W2-3）— 携带错误码与 HTTP 状态，供全局处理器统一出参。

约定：
  - 业务/依赖代码抛 AppError 子类（而不是裸 ValueError/RuntimeError），
    全局处理器据此产出统一错误信封；
  - 未捕获异常由处理器兜底为 `sys_2500`，对外只回通用文案（细节只进日志）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .codes import ErrorCategory, ErrorCode, category_of


class AppError(Exception):
    """应用异常基类。"""

    code: ErrorCode = ErrorCode.INTERNAL
    http_status: int = 500
    #: 对外文案（None 时用实例 message）
    public_message: Optional[str] = None

    def __init__(self, message: str = "", *, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message or self.code.value)
        self.message = message or self.code.value
        self.detail = detail or {}

    @property
    def category(self) -> ErrorCategory:
        return category_of(self.code)

    def to_envelope(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        """统一错误信封。"""
        payload: Dict[str, Any] = {
            "code": self.code.value,
            "message": self.public_message or self.message,
            "category": self.category.value,
        }
        if request_id:
            payload["request_id"] = request_id
        if self.detail:
            payload["detail"] = self.detail
        return {"error": payload}


class InvalidRequestError(AppError):
    """请求不合法（参数/输入问题）→ 400。"""

    code = ErrorCode.INVALID_REQUEST
    http_status = 400


class UnsupportedCapabilityError(AppError):
    """能力不支持/已下线 → 400。"""

    code = ErrorCode.UNSUPPORTED_CAPABILITY
    http_status = 400


class ModelInvocationError(AppError):
    """模型调用失败（LLM/Embedding/Reranker）→ 502。"""

    code = ErrorCode.LLM_FAILED
    http_status = 502
    public_message = "模型服务暂时不可用，请稍后重试。"


class DependencyUnavailableError(AppError):
    """外部依赖不可用（Milvus/MySQL）→ 503。"""

    code = ErrorCode.MILVUS_UNAVAILABLE
    http_status = 503
    public_message = "依赖服务暂时不可用，请稍后重试。"


class ConfigInvalidError(AppError):
    """配置与运行环境不一致（如 embedding 与集合约定不符）→ 500。"""

    code = ErrorCode.CONFIG_INVALID
    http_status = 500
    public_message = "服务配置异常，请联系管理员。"


__all__ = [
    "AppError",
    "ConfigInvalidError",
    "DependencyUnavailableError",
    "InvalidRequestError",
    "ModelInvocationError",
    "UnsupportedCapabilityError",
]
