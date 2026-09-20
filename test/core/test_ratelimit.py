"""C4 限流器单测 —— 进程内 / Redis 双后端语义与工厂回退。

Redis 后端的**核心风险在 Lua 脚本**（原子性、TTL、跨实例共享），故用真实 Redis
做集成测试（不可用则 skip，保证无 Redis 的环境仍可跑全量）。
测试键统一带随机前缀，避免污染共享 Redis。
"""

from __future__ import annotations

import uuid

import pytest
import redis as redis_mod

from core.ratelimit import InMemoryLimiter, RedisLimiter, build_limiter

# ── 进程内限流器 ─────────────────────────────────────────────

def test_inmemory_allows_burst_then_blocks():
    lim = InMemoryLimiter(capacity=2, refill_per_second=0.0)
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=0.0) is False


def test_inmemory_keys_are_independent():
    lim = InMemoryLimiter(capacity=1, refill_per_second=0.0)
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=0.0) is False
    assert lim.allow("b", now=0.0) is True      # 不同键独立配额


def test_inmemory_refills_over_time():
    lim = InMemoryLimiter(capacity=1, refill_per_second=1.0)
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=0.5) is False
    assert lim.allow("a", now=1.0) is True


# ── 工厂后端选择与回退 ───────────────────────────────────────

def test_build_limiter_backend_selection():
    assert type(build_limiter(
        "memory", capacity=1, refill_per_second=1.0)).__name__ == "InMemoryLimiter"
    assert type(build_limiter(
        "redis", capacity=1, refill_per_second=1.0,
        redis_url="redis://localhost:6379/0")).__name__ == "RedisLimiter"


def test_build_limiter_redis_without_url_falls_back_to_memory():
    """带 redis 后端但没给 URL → 回退进程内，不静默失效。"""
    assert type(build_limiter(
        "redis", capacity=1, refill_per_second=1.0, redis_url="")).__name__ == "InMemoryLimiter"


def test_build_limiter_unknown_backend_falls_back_to_memory():
    assert type(build_limiter(
        "nonsense", capacity=1, refill_per_second=1.0)).__name__ == "InMemoryLimiter"


# ── Redis 后端的降级路径（不需真实 Redis）────────────────────

def test_redis_limiter_fail_open_on_runtime_error(monkeypatch):
    """运行期 Redis 故障 → 放行（fail-open），不阻断业务。"""
    lim = RedisLimiter("redis://localhost:6379/0", capacity=1, refill_per_second=0.0)

    def _boom(**kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(lim, "_script", _boom)
    assert lim.allow("k", now=0.0) is True


def test_redis_limiter_unavailable_when_init_fails(monkeypatch):
    """初始化失败（依赖缺失/URL 非法）→ available=False 且恒放行。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("bad url")

    monkeypatch.setattr(redis_mod.Redis, "from_url", staticmethod(_boom))
    lim = RedisLimiter("redis://bad", capacity=1, refill_per_second=0.0)
    assert lim.available is False
    assert lim.allow("k", now=0.0) is True


# ── Redis 后端 Lua 语义（真实 Redis 集成；不可用则跳过）──────

@pytest.fixture
def real_redis_url():
    """项目配置里的 Redis（走 Settings，确保 .env 已加载）。

    注意：**不要**用 `os.getenv` 裸读——本模块不 import settings 时 `.env` 尚未加载，
    会静默落到默认的 6379（本机那是 Langfuse 的 Redis，不能碰）。
    """
    from core.config.settings import Settings

    url = Settings().cache_redis_url
    try:
        client = redis_mod.Redis.from_url(
            url, socket_connect_timeout=1, socket_timeout=1
        )
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"本机 Redis 不可用（{url}）: {exc}")
    return url


@pytest.fixture
def rl_prefix(real_redis_url):
    """每次测试用独立前缀，结束后清理，避免污染共享 Redis。"""
    prefix = f"ztb:test:rl:{uuid.uuid4().hex[:10]}"
    yield prefix
    try:
        client = redis_mod.Redis.from_url(real_redis_url, decode_responses=True)
        for k in client.keys(f"{prefix}:*"):
            client.delete(k)
    except Exception:  # noqa: BLE001
        pass


def test_redis_lua_burst_then_block(real_redis_url, rl_prefix):
    lim = RedisLimiter(real_redis_url, 2, 0.0, prefix=rl_prefix)
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=0.0) is True
    assert lim.allow("a", now=0.0) is False
    assert lim.allow("b", now=0.0) is True      # 键独立


def test_redis_lua_refills(real_redis_url, rl_prefix):
    lim = RedisLimiter(real_redis_url, 1, 1.0, prefix=rl_prefix)
    assert lim.allow("a", now=1000.0) is True
    assert lim.allow("a", now=1000.5) is False
    assert lim.allow("a", now=1001.0) is True   # 1 秒补 1 个


def test_redis_lua_shared_across_instances(real_redis_url, rl_prefix):
    """多副本语义：两个实例（模拟两个进程）共享同一配额。"""
    a = RedisLimiter(real_redis_url, 2, 0.0, prefix=rl_prefix)
    b = RedisLimiter(real_redis_url, 2, 0.0, prefix=rl_prefix)
    assert a.allow("k", now=500.0) is True
    assert b.allow("k", now=500.0) is True      # 读的是同一个桶
    assert a.allow("k", now=500.0) is False     # 配额已用尽（跨实例）


def test_redis_lua_sets_ttl(real_redis_url, rl_prefix):
    """桶键须有过期时间，避免冷键无限堆积。"""
    lim = RedisLimiter(real_redis_url, 60, 1.0, prefix=rl_prefix)
    assert lim.allow("k", now=0.0) is True
    client = redis_mod.Redis.from_url(real_redis_url, decode_responses=True)
    ttl = client.ttl(f"{rl_prefix}:k")
    assert ttl > 0
