"""请求限流中间件（W2-5）— 进程内令牌桶，按客户端键限流。

定位：单实例/单进程的**入口保护**，防止单客户端打满 LLM/检索资源；
分布式限流（Redis 令牌桶）留待多实例部署时替换本实现。

配置（Settings）：`rate_limit_per_minute`（0 = 关闭）、`rate_limit_burst`。
豁免路径：`/health`、`/metrics`（运维探活与抓取不应被限流）。
超限返回 429 + 统一错误信封（`sys_2003`）。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Iterable, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.errors.codes import ErrorCode
from core.observability.context import get_request_id

# C4：限流器实现下沉到 core.ratelimit（进程内 / Redis 双后端）。
# TokenBucket 在此 re-export，保持既有 import 路径（test/api/test_rate_limit.py）可用。
from core.ratelimit import TokenBucket, build_limiter

logger = logging.getLogger(__name__)

DEFAULT_EXEMPT_PATHS: Tuple[str, ...] = ("/health", "/metrics")

#: 轮询型端点：廉价的只读查询（内存查表，无 LLM/DB 开销），但前端会**高频**轮询。
#: 跑按客户端限流会让正常轮询直接 429（实测：0.5s 间隔轮询 5 个文件即打满 burst）。
#: 限流本意是保护昂贵资源，对这类端点弊大于利，故豁免。
_POLL_EXEMPT_RE = re.compile(r"^/files/[^/]+/status$")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """两级限流：**全局总量**（服务端） + **按客户端**（X-Forwarded-For 首跳 / 直连 IP）。

    - **客户端级**：配额按租户/客户端计（W4-3），防单客户端打满资源；
    - **全局级**：整个服务端的请求速率上限（成本保护，`global_per_minute=0` 关闭）；
    - **后端**：`memory`（进程内，单副本）/ `redis`（跨进程共享，**多副本必备**——
      否则每副本各算一套，实际配额 ≈ 副本数 × 配置值）。
    """

    GLOBAL_KEY = "__global__"

    def __init__(
        self,
        app,
        *,
        per_minute: int = 60,
        burst: int | None = None,
        global_per_minute: int = 0,
        global_burst: int | None = None,
        backend: str = "memory",
        redis_url: str = "",
        exempt_paths: Iterable[str] = DEFAULT_EXEMPT_PATHS,
    ) -> None:
        super().__init__(app)
        self._per_minute = int(per_minute)
        self._global_per_minute = int(global_per_minute)
        self._backend = backend
        self._exempt = set(exempt_paths)

        capacity = max(1, int(burst if burst is not None else per_minute))
        self._client_limiter: Optional[object] = (
            build_limiter(
                backend, capacity=capacity, refill_per_second=per_minute / 60.0,
                redis_url=redis_url, prefix="ztb:rl:client",
            )
            if self._per_minute > 0
            else None
        )
        global_capacity = max(
            1, int(global_burst if global_burst is not None else global_per_minute)
        )
        self._global_limiter: Optional[object] = (
            build_limiter(
                backend, capacity=global_capacity,
                refill_per_second=global_per_minute / 60.0,
                redis_url=redis_url, prefix="ztb:rl:global",
            )
            if self._global_per_minute > 0
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._client_limiter is not None or self._global_limiter is not None

    @property
    def backend(self) -> str:
        return self._backend

    @staticmethod
    def client_key(request: Request) -> str:
        """限流键：**租户优先**（W4-3）——配额按租户计；未带租户时回退 IP。"""
        tenant = (request.headers.get("X-Tenant-ID") or "").strip()
        if tenant:
            return f"tenant:{tenant}"
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _reject() -> JSONResponse:
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

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (not self.enabled) or path in self._exempt or _POLL_EXEMPT_RE.match(path):
            return await call_next(request)

        now = time.monotonic()
        # 全局总量先判：整服务端过载时，与是谁无关
        if self._global_limiter is not None and not self._global_limiter.allow(
            self.GLOBAL_KEY, now=now
        ):
            logger.warning("全局限流触发 path=%s", request.url.path)
            return self._reject()

        if self._client_limiter is not None:
            key = self.client_key(request)
            if not self._client_limiter.allow(key, now=now):
                logger.warning("客户端限流触发 key=%s path=%s", key, request.url.path)
                return self._reject()

        return await call_next(request)



__all__ = ["RateLimitMiddleware", "TokenBucket", "DEFAULT_EXEMPT_PATHS"]
