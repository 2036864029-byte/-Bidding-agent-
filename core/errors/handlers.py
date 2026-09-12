"""全局异常处理器（W2-3）— 统一错误信封，不向客户端泄露内部细节。

覆盖：
  - `AppError`（业务/依赖/配置类，主动抛出）→ 按 `http_status` + 统一信封；
  - 未捕获异常 → 500 + `sys_2500` 通用文案（真实异常只进日志）。

不覆盖 FastAPI `HTTPException`：显式 4xx（参数校验、404 文件不存在等）保留
FastAPI 默认 `{"detail": ...}` 契约，避免破坏既有前端联调。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.observability.context import get_request_id

from .codes import ErrorCode
from .exceptions import AppError

logger = logging.getLogger(__name__)

UNHANDLED_MESSAGE = "服务内部错误，请稍后重试。"


def unhandled_envelope() -> dict:
    """未捕获异常的统一信封（SSE 等非 JSONResponse 场景复用）。"""
    return {
        "error": {
            "code": ErrorCode.INTERNAL.value,
            "message": UNHANDLED_MESSAGE,
            "category": "system",
            "request_id": get_request_id() or "-",
        }
    }


def error_envelope(exc: AppError) -> dict:
    """把 AppError 渲染为统一信封（SSE 等非 JSONResponse 场景复用）。"""
    return exc.to_envelope(get_request_id())


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器（在 create_app 内调用一次）。"""

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "AppError %s %s -> %s: %s",
            request.method, request.url.path, exc.code.value, exc.message,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_envelope(get_request_id()),
            headers=exc.headers or None,
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常 %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content=unhandled_envelope())


__all__ = [
    "register_exception_handlers",
    "unhandled_envelope",
    "error_envelope",
    "UNHANDLED_MESSAGE",
]
