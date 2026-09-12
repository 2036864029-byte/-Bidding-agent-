"""core.cache — 版本化 TTL 缓存离线测试（W4-2）。"""

from __future__ import annotations

import threading

from core.cache import MISSING, VersionedTTLCache, normalize_key


class _Clock:
    """可控时钟（注入 TTLCache.timer，确定性地测过期）。"""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_hit_miss_and_stats():
    c = VersionedTTLCache("t", maxsize=4, ttl=60, version="v1")
    k = c.key("q")
    assert c.get(k) is MISSING
    c.set(k, [1, 2])
    assert c.get(k) == [1, 2]
    s = c.stats()
    assert s["hits"] == 1 and s["misses"] == 1 and s["hit_rate"] == 0.5


def test_version_change_invalidates_without_deleting():
    c1 = VersionedTTLCache("t", maxsize=4, ttl=60, version="corpusA")
    c1.set(c1.key("q"), "answerA")
    # 语料版本变更 → key 前缀不同 → 旧 key 自然失效（不必逐个删）
    c2 = VersionedTTLCache("t", maxsize=4, ttl=60, version="corpusB")
    assert c2.key("q") != c1.key("q")
    assert c2.get(c2.key("q")) is MISSING


def test_ttl_expiry():
    clock = _Clock()
    c = VersionedTTLCache("t", maxsize=4, ttl=10, version="v1", timer=clock)
    k = c.key("q")
    c.set(k, "v")
    clock.t = 5
    assert c.get(k) == "v"          # 未过期
    clock.t = 11
    assert c.get(k) is MISSING      # 过期


def test_maxsize_bounds_size():
    c = VersionedTTLCache("t", maxsize=2, ttl=60, version="v1")
    for name in ("a", "b", "c"):
        c.set(c.key(name), name)
    assert c.stats()["size"] <= 2


def test_normalize_key():
    assert normalize_key("  招标   方式\n有几种 ") == "招标 方式 有几种"
    assert normalize_key("") == ""


def test_disabled_returns_none(monkeypatch):
    from core.cache import get_cache, reset_caches

    monkeypatch.setenv("CACHE_ENABLED", "false")
    reset_caches()
    assert get_cache("kb") is None
    monkeypatch.setenv("CACHE_ENABLED", "true")
    reset_caches()
    assert get_cache("kb") is not None


def test_thread_safety_smoke():
    """并发读写不崩、计数自洽（cachetools 非线程安全，包装层加锁）。"""
    c = VersionedTTLCache("t", maxsize=64, ttl=60, version="v1")

    def worker(i: int) -> None:
        for j in range(50):
            k = c.key(f"k{j % 8}")
            c.get(k)
            c.set(k, (i, j))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s = c.stats()
    assert s["hits"] + s["misses"] == 8 * 50


def test_price_intent_key_stable_and_excludes_question():
    """price 缓存 key：同 intent 不同问句 → 同 key（SQL 只由 intent 决定）。"""
    from price_engine.models import HardFilters, SearchIntent
    from price_engine.recall import _intent_cache_key

    i1 = SearchIntent(hard_filters=HardFilters(), semantic_keywords=["办公家具"], original_question="推荐几家做办公家具的企业")
    i2 = SearchIntent(hard_filters=HardFilters(), semantic_keywords=["办公家具"], original_question="有哪些办公家具公司")
    assert _intent_cache_key(["company_info"], i1) == _intent_cache_key(["company_info"], i2)
    # 表列表不同 → key 不同
    assert _intent_cache_key(["company_info"], i1) != _intent_cache_key(["bid_project"], i1)


def test_kb_retrieve_uses_cache():
    """HybridRetriever.retrieve 命中缓存：同问题返回同结果、不回底层；归一化生效。"""
    from core.cache import reset_caches
    from public_kb.retrieval.retriever import HybridRetriever

    reset_caches()
    calls = {"n": 0}

    def _impl(question: str):
        calls["n"] += 1
        return {"result": question}

    r = HybridRetriever.__new__(HybridRetriever)   # 绕过 __init__（无需真实依赖）
    r._retrieve_impl = _impl
    r._attach_parent_context = lambda res, started=None: res

    out1 = r.retrieve("招标方式有哪些？")
    out2 = r.retrieve("招标方式有哪些？")
    out3 = r.retrieve("  招标方式有哪些？  ")   # 归一化后同 key
    out4 = r.retrieve("另一个问题")

    assert out1 is out2 is out3          # 三次命中同一条缓存
    assert out4 is not out1
    assert calls["n"] == 2               # 只真正检索了 2 次（两个不同问题）


# ── W4-2b Redis 共享缓存 ─────────────────────────────────────


def _fake_redis():
    import fakeredis

    return fakeredis.FakeRedis()


def test_redis_cache_hit_miss_and_version():
    from core.cache import RedisBackedCache

    c = RedisBackedCache("kb", ttl=60, version="v1", client=_fake_redis())
    k = c.key("问题")
    assert c.get(k) is MISSING
    c.set(k, {"chunks": [1]})
    assert c.get(k) == {"chunks": [1]}       # pickle 往返
    s = c.stats()
    assert s["hits"] == 1 and s["misses"] == 1

    # 版本化失效：同"问题"、版本变了 → 新 key 未命中
    c2 = RedisBackedCache("kb", ttl=60, version="v2", client=_fake_redis())
    assert c2.get(c2.key("问题")) is MISSING


def test_redis_cache_pickle_roundtrip_records():
    """缓存对象为检索结果（dict/list）→ pickle 往返一致。"""
    from core.cache import RedisBackedCache

    c = RedisBackedCache("price", ttl=60, version="v1", client=_fake_redis())
    k = c.key("i1")
    c.set(k, {"records": [{"company_name": "甲"}, {"company_name": "乙"}], "total_found": 2})
    out = c.get(k)
    assert out["total_found"] == 2 and out["records"][1]["company_name"] == "乙"
    # 返回的是反序列化副本，调用方改动不影响缓存
    out["records"].append({"company_name": "丙"})
    assert len(c.get(k)["records"]) == 2


def test_redis_cache_clear_and_prefix_scope():
    from core.cache import RedisBackedCache

    fake = _fake_redis()
    kb = RedisBackedCache("kb", ttl=60, version="v1", client=fake)
    pr = RedisBackedCache("price", ttl=60, version="v1", client=fake)
    kb.set(kb.key("q"), 1)
    pr.set(pr.key("q"), 2)
    kb.clear()
    assert kb.get(kb.key("q")) is MISSING
    assert pr.get(pr.key("q")) == 2          # 只清 kb 前缀，不误伤 price


def test_redis_cache_get_cache_backend_switch(monkeypatch):
    """CACHE_BACKEND=redis → get_cache 返回 Redis 后端；memory → 进程内。"""
    from core.cache import get_cache, reset_caches

    monkeypatch.setenv("CACHE_BACKEND", "redis")
    monkeypatch.setenv("CACHE_REDIS_URL", "redis://localhost:6379/9")
    reset_caches()
    c = get_cache("kb")
    assert c is not None and type(c).__name__ == "RedisBackedCache"

    monkeypatch.setenv("CACHE_BACKEND", "memory")
    reset_caches()
    assert type(get_cache("kb")).__name__ == "VersionedTTLCache"
    reset_caches()

