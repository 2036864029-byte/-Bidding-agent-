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


@pytest.fixture(autouse=True)
def _hermetic_backends(monkeypatch):
    """C4：把缓存/限流后端钉在进程内，让测试与开发机的 .env 解耦。

    为什么必须钉：若开发者把 .env 切成 `CACHE_BACKEND=redis` / `RATE_LIMIT_BACKEND=redis`，
    测试会连带产生两个问题：
      1. **依赖外部 Redis**（CI/他人机器上没有 → 大面积失败）；
      2. **跨用例污染** —— `reset_caches()` 只清进程内注册表，**不清 Redis**，
         上一轮（甚至上一次运行）的缓存条目会短路本用例的预期路径；
         且 Redis 后端每次反序列化 → 返回**新对象**，断言 `is` 身份的用例会失败。

    需要真实 Redis 的用例自行显式构造（见 test/core/test_ratelimit.py 的集成段）。
    """
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    # 检查点同理：默认 memory（不落盘、不依赖 PG）。需要真后端的用例显式构造
    # （见 test/core/test_checkpointer_postgres.py）。
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "memory")
    from core.cache import reset_caches

    reset_caches()
    yield
    reset_caches()
