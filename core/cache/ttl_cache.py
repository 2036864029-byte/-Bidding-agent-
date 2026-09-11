"""版本化 TTL 缓存（W4-2）。

设计要点
--------
1. **版本化 key**：`{version}:{normalized}`。数据更新时换 version → 旧 key 整体自然失效，
   不必逐个删除（避免"漏删导致脏读"）。
2. **只缓存事实层**：缓存检索结果（chunks/records），**不缓存 LLM 最终答案**——
   生成层上下文相关（同事实不同措辞），且答案快照会过期。
3. **进程内**：当前单进程部署够用；多副本阶段换 Redis（见 W4-4）。
4. **线程安全**：cachetools 的 cache 非线程安全，读写加锁。
5. **命中统计** → Prometheus（`core.observability.metrics`）。
6. **未启用即 no-op**：`cache_enabled=False` 时 `get_cache()` 返回 None，调用方直接跳过。

多轮说明：多轮追问经 `agent/nodes/rewrite.py`（W3-1）改写为**自包含问题**后才进入业务节点，
故缓存 key 天然是归一化后的问题——"那它的期限呢"改写为规范问题后与单轮提问命中同一条。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)

#: 缓存未命中的哨兵（区分"命中且值为 None"）
MISSING = object()

_caches: Dict[str, "VersionedTTLCache"] = {}
_registry_lock = threading.Lock()


def normalize_key(text: str) -> str:
    """问题归一化：折叠空白、去首尾（不改大小写，避免误合并中文/编号）。"""
    return " ".join((text or "").split())


class VersionedTTLCache:
    """带版本前缀的 TTL+LRU 缓存（线程安全），附命中统计。"""

    def __init__(self, name: str, *, maxsize: int, ttl: int, version: str = "",
                 timer: Optional[Any] = None) -> None:
        self._name = name
        self._version = version or "-"
        kwargs: Dict[str, Any] = {"maxsize": max(1, int(maxsize)), "ttl": max(1, int(ttl))}
        if timer is not None:
            kwargs["timer"] = timer  # 测试注入用（默认 time.monotonic）
        self._cache: TTLCache = TTLCache(**kwargs)
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str:
        return self._version

    def key(self, *parts: Any) -> str:
        """构造带版本前缀的 key（版本变更 → 旧 key 全 miss）。"""
        return f"{self._version}:" + "|".join(str(p) for p in parts)

    def get(self, key: str) -> Any:
        """命中返回值；未命中返回 MISSING。"""
        with self._lock:
            try:
                value = self._cache[key]
            except KeyError:
                self._misses += 1
                self._record(hit=False)
                return MISSING
            self._hits += 1
            self._record(hit=True)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "name": self._name,
                "version": self._version,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total else None,
                "size": len(self._cache),
                "maxsize": self._cache.maxsize,
            }

    def _record(self, *, hit: bool) -> None:
        try:
            from core.observability.metrics import record_cache_access

            record_cache_access(self._name, hit=hit)
        except Exception:  # noqa: BLE001  埋点失败不影响缓存主路径
            pass


def _build_caches() -> Dict[str, "VersionedTTLCache"]:
    """按 Settings 构造 KB / price 两个缓存（版本信号不同）。"""
    from core.config.settings import Settings

    s = Settings()
    corpus_version = s.cache_corpus_version or str(getattr(s, "collection_name", "") or "")
    return {
        "kb": VersionedTTLCache(
            "kb", maxsize=s.cache_maxsize, ttl=s.cache_kb_ttl_seconds,
            version=corpus_version or "kb-v1",
        ),
        "price": VersionedTTLCache(
            "price", maxsize=s.cache_maxsize, ttl=s.cache_price_ttl_seconds,
            version=s.cache_price_version,
        ),
    }


def get_cache(name: str) -> Optional[VersionedTTLCache]:
    """取缓存实例；未启用返回 None（调用方直接跳过缓存）。"""
    from core.config.settings import Settings

    if not Settings().cache_enabled:
        return None
    global _caches
    if name not in _caches:
        with _registry_lock:
            if not _caches:
                _caches = _build_caches()
    return _caches.get(name)


def cache_stats() -> list[dict]:
    """全部缓存统计（诊断 / 报告用）。"""
    with _registry_lock:
        return [c.stats() for c in _caches.values()]


def reset_caches() -> None:
    """清空并重建注册表（测试 / 版本切换用）。"""
    global _caches
    with _registry_lock:
        _caches = {}


__all__ = [
    "MISSING", "VersionedTTLCache", "normalize_key",
    "get_cache", "cache_stats", "reset_caches",
]
