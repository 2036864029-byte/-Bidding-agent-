"""Redis 版检索缓存（W4-2b）— 多副本共享。

实现与 `core.cache.ttl_cache.VersionedTTLCache` 相同的接口（name/version/key/get/set/
clear/stats），供 `get_cache()` 按 `settings.cache_backend` 切换；**业务代码零改动**。

与内存版的差异（设计上明说）：
  - TTL 由 Redis `SETEX` 天然保证；**版本化失效**同内存版（key 前缀含版本）。
  - **LRU/maxsize 不做**：Redis 无内置 per-key LRU；容量靠 Redis 实例的
    maxmemory 策略（allkeys-lru）或由调用方自行管理。stats 的 size/maxsize 返回 None。
  - **命中统计持久化**：hits/misses 用 Redis INCR，跨副本累计。
  - **序列化 pickle**：缓存对象为检索结果（含 langchain Document / records dict），
    均 pickle 可序列化。跨版本/跨环境的 pickle 兼容性由调用方负责（换语料版本即换 key，
    旧 key 自然过期，不反读）。
  - **Redis 不可用时优雅降级**：get 失败按未命中处理、set 失败跳过 → 业务照常（仅缓存失效）。

启用：`.env` 设 `CACHE_BACKEND=redis` + `CACHE_REDIS_URL=redis://host:6379/0`。
"""

from __future__ import annotations

import logging
import pickle
from typing import Any, Optional

from .ttl_cache import MISSING  # 复用同一哨兵（避免各实现各有一个 MISSING）

logger = logging.getLogger(__name__)


class RedisBackedCache:
    """Redis 版缓存（版本化 TTL + 持久命中统计；无 LRU）。"""

    def __init__(
        self,
        name: str,
        *,
        ttl: int,
        version: str = "",
        url: str = "redis://localhost:6379/0",
        prefix: str = "ztb:cache",
        namespace: str = "retrieval",
        client: Optional[Any] = None,
    ) -> None:
        self._name = name
        self._version = version or "-"
        self._ttl = max(1, int(ttl))
        self._prefix = f"{prefix}:{namespace}:{name}"
        if client is not None:                      # 测试注入（fakeredis）
            self._client = client
        else:
            try:
                import redis as _redis

                self._client = _redis.Redis.from_url(url, decode_responses=False)
            except Exception as exc:                # redis 库缺失/URL 非法 → 构造期降级
                logger.warning("Redis 缓存不可用（%s），本次进程降级为空缓存", exc)
                self._client = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    def key(self, *parts: Any) -> str:
        """构造带版本前缀的 key（与内存版一致；版本变更 → 旧 key 全 miss）。"""
        return f"{self._version}:" + "|".join(str(p) for p in parts)

    def _rkey(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def get(self, key: str) -> Any:
        """命中返回反序列化值；未命中/异常返回 MISSING。"""
        if self._client is None:
            self._record(hit=False)
            return MISSING
        try:
            raw = self._client.get(self._rkey(key))
        except Exception as exc:  # noqa: BLE001  Redis 故障 → 视为未命中，不阻断业务
            logger.warning("Redis get 失败: %s", exc)
            self._record(hit=False)
            return MISSING
        if raw is None:
            self._record(hit=False)
            return MISSING
        try:
            value = pickle.loads(raw)
        except Exception:  # noqa: BLE001  反序列化失败（跨版本残留）→ 删掉按未命中
            logger.warning("缓存反序列化失败，删除脏 key: %s", key[:60])
            try:
                self._client.delete(self._rkey(key))
            except Exception:  # noqa: BLE001
                pass
            self._record(hit=False)
            return MISSING
        self._record(hit=True)
        return value

    def set(self, key: str, value: Any) -> None:
        if self._client is None:
            return
        try:
            self._client.set(self._rkey(key), pickle.dumps(value), ex=self._ttl)
        except Exception as exc:  # noqa: BLE001  写失败跳过（缓存失效但业务照常）
            logger.warning("Redis set 失败: %s", exc)

    def clear(self) -> None:
        """清空本缓存全部 key（按前缀扫描；版本切换通常无需主动清，旧 key 自然过期）。"""
        if self._client is None:
            return
        try:
            for rk in self._client.scan_iter(match=f"{self._prefix}:*"):
                self._client.delete(rk)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis clear 失败: %s", exc)

    def stats(self) -> dict:
        hits, misses = 0, 0
        if self._client is not None:
            try:
                hits = int(self._client.get(f"{self._prefix}:hits") or 0)
                misses = int(self._client.get(f"{self._prefix}:misses") or 0)
            except Exception:  # noqa: BLE001
                pass
        total = hits + misses
        return {
            "name": self._name,
            "version": self._version,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 4) if total else None,
            "size": None,        # Redis 无 per-key size 概念
            "maxsize": None,     # LRU 由 Redis 实例 maxmemory 策略管理
        }

    def _record(self, *, hit: bool) -> None:
        if self._client is not None:
            try:
                self._client.incr(f"{self._prefix}:{'hits' if hit else 'misses'}")
            except Exception:  # noqa: BLE001  计数失败不影响主路径
                pass
        try:
            from core.observability.metrics import record_cache_access

            record_cache_access(self._name, hit=hit)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["RedisBackedCache"]
