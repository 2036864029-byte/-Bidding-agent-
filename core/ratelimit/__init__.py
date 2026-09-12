# 功能：限流统一入口（C4）—— 进程内 / Redis 双后端。
"""core.ratelimit — 限流器（进程内 / Redis）。

- `bucket.Limiter`       统一接口
- `bucket.TokenBucket`   单键令牌桶（进程内）
- `bucket.InMemoryLimiter` 多键令牌桶（进程内，单副本）
- `redis_bucket.RedisLimiter` 跨进程共享（多副本）
- `redis_bucket.build_limiter` 按 `Settings.rate_limit_backend` 构造
"""

from __future__ import annotations

from .bucket import InMemoryLimiter, Limiter, TokenBucket
from .redis_bucket import RedisLimiter, build_limiter

__all__ = [
    "Limiter",
    "TokenBucket",
    "InMemoryLimiter",
    "RedisLimiter",
    "build_limiter",
]
