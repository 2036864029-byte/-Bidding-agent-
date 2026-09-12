"""pytest 全局夹具。

W4-2：检索缓存（core.cache）是**进程级单例**，跨用例会互相污染
（同一问题命中上一用例的缓存结果 → 断言拿到过期/异构数据）。
每个用例前后清空缓存注册表，保证用例隔离。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_retrieval_cache():
    from core.cache import reset_caches

    reset_caches()
    yield
    reset_caches()


@pytest.fixture(autouse=True)
def _reset_inflight_gate():
    """C1：在飞闸门是进程级单例，跨用例会残留计数与配置 → 每个用例前后重置。"""
    from core.concurrency.limits import reset_inflight_gate

    reset_inflight_gate()
    yield
    reset_inflight_gate()
