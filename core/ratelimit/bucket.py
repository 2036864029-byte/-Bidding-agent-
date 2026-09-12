# 功能：限流器接口 + 进程内实现（C4）。
"""限流器接口与进程内实现。

`Limiter` 是限流的统一接口；两种后端（进程内 / Redis）都实现它，
`api/middleware/rate_limit.py` 按 `Settings.rate_limit_backend` 选择 —— 业务无感。

进程内实现是**单进程**保护；多进程/多副本下各自计数，实际配额 ≈ N × 配置值，
因此多副本部署应切到 Redis 后端（见 `redis_bucket.py`）。
"""

from __future__ import annotations

import threading
from typing import Dict, Protocol


class Limiter(Protocol):
    """限流器接口：`allow` 返回本次是否放行。"""

    def allow(self, key: str, *, now: float, cost: float = 1.0) -> bool:
        ...


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


class InMemoryLimiter:
    """多键令牌桶（进程内）—— 每个 key 一个 ``TokenBucket``。"""

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self._capacity = max(1, int(capacity))
        self._refill_per_second = float(refill_per_second)
        self._lock = threading.Lock()
        self._buckets: Dict[str, TokenBucket] = {}

    def allow(self, key: str, *, now: float, cost: float = 1.0) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self._capacity, self._refill_per_second, now=now)
                self._buckets[key] = bucket
            return bucket.allow(now=now, cost=cost)


__all__ = ["Limiter", "TokenBucket", "InMemoryLimiter"]
