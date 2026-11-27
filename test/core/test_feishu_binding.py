"""core.feishu_binding 绑定存储测试（并行线 A）—— 纯离线，不触网。"""

from __future__ import annotations

import time

import fakeredis

from core import feishu_binding as fb
from core.feishu_binding import _MemoryBackend, _RedisBackend


def test_bind_lookup_unbind_roundtrip():
    fb.reset_bindings()
    assert fb.bind("s1", "ou_abc123") is True
    assert fb.lookup("s1") == "ou_abc123"
    assert fb.is_bound("s1")
    fb.unbind("s1")
    assert fb.lookup("s1") is None
    assert not fb.is_bound("s1")


def test_bind_is_scoped_per_session():
    fb.reset_bindings()
    fb.bind("s1", "ou_one")
    fb.bind("s2", "ou_two")
    assert fb.lookup("s1") == "ou_one"
    assert fb.lookup("s2") == "ou_two"


def test_invalid_open_id_rejected():
    fb.reset_bindings()
    for bad in ("bad id!<>", "", "ou_" * 40, "带中文", "x" * 65):
        assert fb.bind("s", bad) is False, bad
    assert not fb.is_bound("s")


def test_valid_open_id_shapes_accepted():
    fb.reset_bindings()
    for ok in ("ou_Abc123", "ou_7dab8a3d3cdcc9da365777c7ad535d62"):
        assert fb.bind("s", ok) is True, ok


def test_ttl_expiry(monkeypatch):
    fb.reset_bindings()
    monkeypatch.setenv("FEISHU_BINDING_TTL_S", "0")  # 0 → 立即过期
    fb.bind("s", "ou_x")
    time.sleep(0.01)
    assert fb.lookup("s") is None


def test_reset_clears_all():
    fb.reset_bindings()
    fb.bind("s1", "ou_a")
    fb.bind("s2", "ou_b")
    fb.reset_bindings()
    assert fb.lookup("s1") is None
    assert fb.lookup("s2") is None


# ── Redis 后端（多 worker 共享，fakeredis 注入，不触网）────────────


def test_redis_backend_crud_roundtrip():
    backend = _RedisBackend(ttl=60, url="", client=fakeredis.FakeRedis())
    backend.set("s1", "ou_x")
    assert backend.get("s1") == "ou_x"          # 写入键：ztb:feishu:bind:s1
    assert backend.get("missing") is None
    backend.delete("s1")
    assert backend.get("s1") is None
    backend.set("s1", "ou_a")
    backend.set("s2", "ou_b")
    backend.clear()
    assert backend.get("s1") is None
    assert backend.get("s2") is None


def test_redis_backend_sets_ttl():
    client = fakeredis.FakeRedis()
    backend = _RedisBackend(ttl=123, url="", client=client)
    backend.set("s1", "ou_x")
    assert 0 < client.ttl("ztb:feishu:bind:s1") <= 123


def test_redis_backend_swallows_client_errors():
    """Redis 故障不阻断绑定：方法吞异常并告警，不向外抛。"""

    class _ExplodingClient:
        def set(self, *a, **k):
            raise RuntimeError("down")

        def get(self, *a, **k):
            raise RuntimeError("down")

        def delete(self, *a, **k):
            raise RuntimeError("down")

        def scan_iter(self, *a, **k):
            raise RuntimeError("down")

    backend = _RedisBackend(ttl=60, url="", client=_ExplodingClient())
    backend.set("s", "ou_x")     # 不抛
    assert backend.get("s") is None
    backend.delete("s")           # 不抛
    backend.clear()               # 不抛


def test_build_backend_redis_with_injected_redis(monkeypatch):
    """backend=redis 且 Redis 可达 → 探测通过，返回 Redis 后端。"""
    import redis as redis_mod

    monkeypatch.setattr(
        redis_mod.Redis,
        "from_url",
        staticmethod(lambda *a, **k: fakeredis.FakeRedis()),
    )
    store = fb._build_backend(60, "redis", "redis://fake")
    assert isinstance(store, _RedisBackend)
    store.set("s", "ou_x")
    assert store.get("s") == "ou_x"


def test_build_backend_falls_back_when_redis_unreachable(monkeypatch):
    """Redis 挂掉不能阻断绑定：降级进程内后端。

    用 from_url 假件让 ping 抛错——**不真连 127.0.0.1**（hermetic 闸门会拦下真连接，
    "降级后碰巧通过"的测试换台机器行为不一致）。
    """
    import redis as redis_mod

    class _DownClient:
        def ping(self):
            raise ConnectionError("redis down")

    monkeypatch.setattr(
        redis_mod.Redis,
        "from_url",
        staticmethod(lambda *a, **k: _DownClient()),
    )
    store = fb._build_backend(60, "redis", "redis://fake")
    assert isinstance(store, _MemoryBackend)


def test_module_api_uses_redis_backend():
    """模块级 bind/lookup 走注入的 Redis 后端（等价于 memory）。"""
    fb._backend = _RedisBackend(ttl=60, url="", client=fakeredis.FakeRedis())
    assert fb.bind("s", "ou_x") is True
    assert fb.lookup("s") == "ou_x"
    assert fb.is_bound("s")
    fb.unbind("s")
    assert fb.lookup("s") is None
    fb.reset_bindings()
