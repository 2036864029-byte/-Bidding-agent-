"""请求限流中间件（W2-5）— 进程内令牌桶，按客户端键限流。

定位：单实例/单进程的**入口保护**，防止单客户端打满 LLM/检索资源；
分布式限流（Redis 令牌桶）留待多实例部署时替换本实现。

配置（Settings）：`rate_limit_per_minute`（0 = 关闭）、`rate_limit_burst`。
豁免路径：`/health`、`/metrics`（运维探活与抓取不应被限流）。
超限返回 429 + 统一错误信封（`sys_2003`）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Iterable, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.errors.codes import ErrorCode
from core.observability.context import get_request_id

logger = logging.getLogger(__name__)

DEFAULT_EXEMPT_PATHS: Tuple[str, ...] = ("/health", "/metrics")


class TokenBucket:
    """单键令牌桶（线程安全）。"""

    __slots__ = ("capacity", "tokens", "refill_per_second", "updated")

    def __init__(self, capacity: int, refill_per_second: float, *, now: float) -> None:
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self.updated = now

    def allow(self, *, now: float, cost: float = 1.0) -> bool:
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端键限流（默认取 X-Forwarded-For 首跳 / 直连 IP）。"""

    def __init__(
        self,
        app,
        *,
        per_minute: int = 60,
        burst: int | None = None,
        exempt_paths: Iterable[str] = DEFAULT_EXEMPT_PATHS,
    ) -> None:
        super().__init__(app)
        self._per_minute = int(per_minute)
        self._capacity = max(1, int(burst if burst is not None else per_minute))
        self._exempt = set(exempt_paths)
        self._lock = threading.Lock()
        self._buckets: Dict[str, TokenBucket] = {}

    @property
    def enabled(self) -> bool:
        return self._per_minute > 0

    @staticmethod
    def client_key(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _allow(self, key: str, *, now: float) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(
                    self._capacity, self._per_minute / 60.0, now=now,
                )
                self._buckets[key] = bucket
            return bucket.allow(now=now)

    async def dispatch(self, request: Request, call_next):
        if not self.enabled or request.url.path in self._exempt:
            return await call_next(request)

        key = self.client_key(request)
        if not self._allow(key, now=time.monotonic()):
            logger.warning("限流触发 key=%s path=%s", key, request.url.path)
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": ErrorCode.RATE_LIMITED.value,
                        "message": "请求过于频繁，请稍后重试。",
                        "category": "system",
                        "request_id": get_request_id() or "-",
                    }
                },
            )
        return await call_next(request)


__all__ = ["RateLimitMiddleware", "TokenBucket", "DEFAULT_EXEMPT_PATHS"]
