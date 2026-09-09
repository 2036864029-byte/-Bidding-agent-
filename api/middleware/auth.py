"""API Key 鉴权中间件（W3-0）。

约定：
  - Key 取自 `X-API-Key` 头，或 `Authorization: Bearer <key>`。
  - 配置 `API_KEYS`（逗号分隔）——**为空表示关闭鉴权**（本地开发/内网），启动日志会提示。
  - 比较使用 `hmac.compare_digest`（避免时序侧信道）。
  - 鉴权通过后把 Key 的**指纹**（sha256 前 8 位）写入 contextvar，供审计日志标识调用方；
    原始 Key 不落任何日志。
  - 豁免路径（默认 /health、/metrics、/docs、/openapi.json、/redoc）不走鉴权。
  - 未通过 → 401 + 统一错误信封（`biz_1006`）。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Iterable, Optional, Set

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.errors.codes import ErrorCode
from core.observability.context import get_request_id, set_api_key_id

logger = logging.getLogger(__name__)

DEFAULT_EXEMPT_PATHS: tuple[str, ...] = (
    "/health", "/metrics", "/docs", "/openapi.json", "/redoc",
)


def key_fingerprint(api_key: str) -> str:
    """API Key 指纹（sha256 前 8 位）——用于审计标识调用方，不可反推。"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """基于静态 API Key 的入口鉴权。"""

    def __init__(
        self,
        app,
        *,
        keys: Iterable[str],
        exempt_paths: Iterable[str] = DEFAULT_EXEMPT_PATHS,
    ) -> None:
        super().__init__(app)
        self._keys: Set[str] = {k.strip() for k in keys if k and k.strip()}
        self._exempt = set(exempt_paths)

    @property
    def enabled(self) -> bool:
        return bool(self._keys)

    @staticmethod
    def extract_key(request: Request) -> Optional[str]:
        """从请求头提取 Key（X-API-Key 优先，其次 Authorization: Bearer）。"""
        header = request.headers.get("X-API-Key")
        if header and header.strip():
            return header.strip()
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[len("bearer "):].strip() or None
        return None

    def _is_valid(self, key: str) -> bool:
        return any(hmac.compare_digest(key, known) for known in self._keys)

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or request.url.path in self._exempt:
            return await call_next(request)

        key = self.extract_key(request)
        if not key or not self._is_valid(key):
            logger.warning(
                "鉴权失败 path=%s key_present=%s", request.url.path, bool(key),
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": ErrorCode.UNAUTHORIZED.value,
                        "message": "缺少或无效的 API Key。",
                        "category": "business",
                        "request_id": get_request_id() or "-",
                    }
                },
            )

        set_api_key_id(key_fingerprint(key))
        return await call_next(request)


__all__ = ["ApiKeyAuthMiddleware", "DEFAULT_EXEMPT_PATHS", "key_fingerprint"]
