# 功能：Redis 原子令牌桶（C4）—— 多进程/多副本共享限流状态。
"""Redis 原子令牌桶。

进程内限流器（`bucket.InMemoryLimiter`）在多副本下每份各算一套 → 实际配额 ≈
副本数 × 配置值。本实现把桶放进 Redis，用 **Lua 脚本原子完成**
「取状态 → 补令牌 → 判定 → 扣减 → 写回」，多副本共享同一配额。

故障语义：**fail-open**（Redis 不可达时放行并告警）。限流是保护措施而非业务
前置条件，Redis 抖动不该让整个服务拒绝请求 —— 与检索缓存的优雅降级一致。

键格式：`{prefix}:{key}`，TTL 按「桶装满所需时间 + 60s」自动回收，避免冷键堆积。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# KEYS[1]=桶键  ARGV[1]=容量  ARGV[2]=每秒补充  ARGV[3]=now(秒)  ARGV[4]=本次消耗
# 返回 1=放行 0=拒绝
_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now
end
local delta = now - ts
if delta < 0 then delta = 0 end
tokens = tokens + delta * refill
if tokens > capacity then tokens = capacity end
local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end
redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
-- TTL 自回收：refill 可能为 0（纯突发桶）→ 除零会得到 inf，EXPIRE 只收整数，
-- 因此显式兜底 + 用字符串格式化强制整数（Lua 数字皆是 double）。
local ttl = 3600
if refill > 0 then
  ttl = math.floor(capacity / refill) + 60
  if ttl < 60 then ttl = 60 end
end
redis.call('EXPIRE', key, string.format('%d', ttl))
return allowed
"""


class RedisLimiter:
    """跨进程共享的令牌桶限流器（实现 ``Limiter`` 接口）。"""

    def __init__(
        self,
        url: str,
        capacity: int,
        refill_per_second: float,
        *,
        prefix: str = "ztb:rl",
        socket_timeout: float = 2.0,
    ) -> None:
        self._prefix = prefix
        self._capacity = max(1, int(capacity))
        self._refill_per_second = float(refill_per_second)
        self._available = True
        self._client = None
        self._script = None
        try:
            import redis as _redis

            self._client = _redis.Redis.from_url(
                url,
                socket_connect_timeout=socket_timeout,
                socket_timeout=socket_timeout,
                decode_responses=True,
            )
            self._script = self._client.register_script(_LUA)
        except Exception as exc:  # noqa: BLE001  依赖缺失/URL 非法 → 退化为恒放行
            self._available = False
            logger.error("RedisLimiter 初始化失败（限流退化为恒放行）: %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    def allow(self, key: str, *, now: float, cost: float = 1.0) -> bool:
        if not self._available or self._script is None:
            return True
        try:
            result = self._script(
                keys=[f"{self._prefix}:{key}"],
                args=[self._capacity, self._refill_per_second, now, cost],
            )
            return bool(int(result))
        except Exception as exc:  # noqa: BLE001  运行期故障 → fail-open
            logger.warning("Redis 限流不可用，本次放行（fail-open）: %s", exc)
            return True


def build_limiter(
    backend: str,
    *,
    capacity: int,
    refill_per_second: float,
    redis_url: str = "",
    prefix: str = "ztb:rl",
) -> Optional[object]:
    """按后端名构造限流器；backend 为 ``memory`` 之外的未知值也回退 memory。"""
    from .bucket import InMemoryLimiter

    if (backend or "").strip().lower() == "redis" and redis_url:
        return RedisLimiter(redis_url, capacity, refill_per_second, prefix=prefix)
    return InMemoryLimiter(capacity, refill_per_second)


__all__ = ["RedisLimiter", "build_limiter"]
