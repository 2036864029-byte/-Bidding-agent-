"""core.errors — 统一错误与容错基础设施（W2-3）。

注意：不在此 re-export `handlers`（依赖 FastAPI，属 API 层），
需要时 `from core.errors.handlers import register_exception_handlers`。
"""

from .codes import ErrorCategory, ErrorCode, category_of
from .exceptions import (
    AppError,
    ConfigInvalidError,
    DependencyUnavailableError,
    InvalidRequestError,
    ModelInvocationError,
    UnsupportedCapabilityError,
)
from .fallback import accepts_config, fallback_answer, with_fallback

__all__ = [
    "ErrorCategory",
    "ErrorCode",
    "category_of",
    "AppError",
    "InvalidRequestError",
    "UnsupportedCapabilityError",
    "ModelInvocationError",
    "DependencyUnavailableError",
    "ConfigInvalidError",
    "accepts_config",
    "fallback_answer",
    "with_fallback",
]
