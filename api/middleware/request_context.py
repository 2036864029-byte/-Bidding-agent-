"""请求上下文中间件（W2-2）— request_id 生成/透传 + 访问日志。

- 客户端可通过 `X-Request-ID` 透传（便于跨服务串联）；未带则生成。
- request_id / session_id 写入 contextvars，日志 filter 自动注入（见
  `core.observability.logging.RequestContextFilter`）。
- 响应头回传 `X-Request-ID`，便于用户报障时定位。
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from core.observability.context import new_request_id, set_request_id, set_session_id
from core.observability.tracing import set_span_attrs, span

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求建立请求上下文（并开启根 span，E0-3）。"""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        set_request_id(request_id)
        set_session_id(None)  # session 由端点解析后设置

        started = time.perf_counter()
        # 根 span：子 span（节点 / LLM / 检索）经 contextvars 自动挂到本 span 下。
        with span(
            f"{request.method} {request.url.path}",
            **{
                "http.request.method": request.method,
                "url.path": request.url.path,
                "ztb.request_id": request_id,
            },
        ) as _sp:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception(
                    "请求异常 method=%s path=%s", request.method, request.url.path,
                )
                raise

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            set_span_attrs(
                _sp,
                **{
                    "http.response.status_code": response.status_code,
                    "ztb.latency_ms": elapsed_ms,
                },
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "http %s %s -> %d %dms",
                request.method, request.url.path, response.status_code, elapsed_ms,
            )
            return response


__all__ = ["REQUEST_ID_HEADER", "RequestContextMiddleware"]
