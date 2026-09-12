"""core.cache — 检索结果缓存（W4-2 / W4-2b）。"""

from __future__ import annotations

from .redis_backend import RedisBackedCache
from .ttl_cache import (
    MISSING,
    VersionedTTLCache,
    cache_stats,
    get_cache,
    normalize_key,
    reset_caches,
)

__all__ = [
    "MISSING",
    "VersionedTTLCache",
    "RedisBackedCache",
    "normalize_key",
    "get_cache",
    "cache_stats",
    "reset_caches",
]
