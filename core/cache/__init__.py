"""core.cache — 检索结果缓存（W4-2）。"""

from __future__ import annotations

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
    "normalize_key",
    "get_cache",
    "cache_stats",
    "reset_caches",
]
