"""filestore 后端切换与多 worker 共享测试（B1）。

- `memory` / `redis`（fakeredis）两个后端跑**同一组行为断言**——接口等价是本次
  改造的核心契约（调用方零改动）；
- 另覆盖多 worker 验收点：worker A 写入 → worker B 能读到；
- Redis 不可达时 `_build_backend` 必须降级内存，而不是让上传功能整体失效。
"""

from __future__ import annotations

import fakeredis
import pytest

from core import filestore
from core.filestore import FileRecord, _MemoryBackend, _RedisBackend


def _record(
    fid: str = "f1",
    session: str = "s1",
    created_at: float | None = None,
) -> FileRecord:
    """默认 created_at = 当前时间（写死的过去时间戳会被 TTL 判为过期）。"""
    return FileRecord(
        file_id=fid,
        source_name="a.pdf",
        source_format="pdf",
        markdown="# 正文",
        preview="正文",
        chunk_count=3,
        session_id=session,
        created_at=filestore.time.time() if created_at is None else created_at,
    )


@pytest.fixture(params=["memory", "redis"])
def backend(request):
    """两个后端同一组用例（Redis 用 fakeredis，不依赖外部服务）。"""
    if request.param == "memory":
        return _MemoryBackend(ttl=filestore.FILE_TTL_SECONDS)
    return _RedisBackend(
        ttl=filestore.FILE_TTL_SECONDS, url="", client=fakeredis.FakeRedis()
    )


# ── 后端行为等价 ──────────────────────────────────────────────


def test_put_get_roundtrip(backend):
    rec = _record()
    backend.put(rec)
    got = backend.get("f1")
    assert got == rec          # 字段逐个往返（redis 走 pickle）


def test_get_unknown_returns_none(backend):
    assert backend.get("nope") is None


def test_list_by_session_filters_and_sorts_desc(backend):
    now = filestore.time.time()
    backend.put(_record(fid="old", session="s1", created_at=now - 200))
    backend.put(_record(fid="new", session="s1", created_at=now - 100))
    backend.put(_record(fid="other", session="s2", created_at=now))

    ids = [r.file_id for r in backend.list_by_session("s1")]
    assert ids == ["new", "old"], "按上传时间倒序，且不含其他会话"
    assert [r.file_id for r in backend.list_by_session("s2")] == ["other"]


def test_list_by_session_unknown_returns_empty(backend):
    assert backend.list_by_session("nobody") == []


def test_clear_empties_store(backend):
    backend.put(_record())
    backend.clear()
    assert backend.get("f1") is None
    assert backend.list_by_session("s1") == []


# ── 过期语义 ──────────────────────────────────────────────────


def test_memory_expired_record_dropped():
    """TTL 过期即在 get / list_by_session 两处都不可见。"""
    backend = _MemoryBackend(ttl=10)
    backend.put(_record(created_at=filestore.time.time() - 11))
    assert backend.get("f1") is None
    assert backend.list_by_session("s1") == []


def test_redis_put_sets_ttl_on_record_and_index():
    """Redis 侧过期由 SET/SADD 的 TTL 保证，不靠时间戳比对。"""
    client = fakeredis.FakeRedis()
    backend = _RedisBackend(ttl=123, url="", client=client)
    backend.put(_record())

    assert 0 < client.ttl(f"{filestore._REDIS_PREFIX}:file:f1") <= 123
    assert 0 < client.ttl(f"{filestore._REDIS_PREFIX}:session:s1") <= 123


def test_redis_list_by_session_prunes_stale_index_entries():
    """索引里残留的记录已过期 → 不出现在结果里，且顺手从集合剔除。"""
    client = fakeredis.FakeRedis()
    backend = _RedisBackend(ttl=123, url="", client=client)
    backend.put(_record(fid="gone"))
    client.delete(f"{filestore._REDIS_PREFIX}:file:gone")   # 模拟记录先过期

    assert backend.list_by_session("s1") == []
    assert client.smembers(f"{filestore._REDIS_PREFIX}:session:s1") == set()


# ── 多 worker 验收（B1 的核心）────────────────────────────────


def test_shared_redis_visible_across_workers():
    """worker A 上传 → worker B 提问能取到文件（内存后端做不到这件事）。"""
    client = fakeredis.FakeRedis()          # 同一 Redis 实例 = 同一部署
    worker_a = _RedisBackend(ttl=60, url="", client=client)
    worker_b = _RedisBackend(ttl=60, url="", client=client)

    worker_a.put(_record(fid="f1", session="s1"))

    assert worker_b.get("f1") is not None
    assert [r.file_id for r in worker_b.list_by_session("s1")] == ["f1"]


def test_memory_backend_is_not_shared():
    """反证：进程内后端天然不跨 worker（这正是 B1 的成因）。"""
    worker_a = _MemoryBackend(ttl=60)
    worker_b = _MemoryBackend(ttl=60)
    worker_a.put(_record())
    assert worker_b.get("f1") is None


# ── 模块级 API（调用方契约）──────────────────────────────────


def test_module_api_roundtrip_and_ownership(monkeypatch):
    monkeypatch.setattr(filestore, "_backend", _MemoryBackend(ttl=60))
    filestore.put(_record(session="s1"))

    assert filestore.get("f1") is not None
    assert filestore.get("f1", session_id="s1") is not None
    assert filestore.get("f1", session_id="s2") is None, "跨会话按不存在处理（W0-6）"
    assert [r.file_id for r in filestore.list_by_session("s1")] == ["f1"]
    assert filestore.list_by_session("s2") == []


# ── 后端选择与降级 ────────────────────────────────────────────


def test_build_backend_defaults_to_memory(monkeypatch):
    monkeypatch.delenv("FILE_STORE_BACKEND", raising=False)
    assert isinstance(filestore._build_backend(), _MemoryBackend)


def test_build_backend_redis_with_injected_url(monkeypatch):
    """backend=redis 且 Redis 可达 → 构造 Redis 后端。"""
    import redis as redis_mod

    monkeypatch.setenv("FILE_STORE_BACKEND", "redis")
    monkeypatch.setattr(
        redis_mod.Redis, "from_url", staticmethod(lambda *a, **k: fakeredis.FakeRedis())
    )
    assert isinstance(filestore._build_backend(), _RedisBackend)


def test_build_backend_falls_back_when_redis_unreachable(monkeypatch):
    """Redis 挂掉不能让上传功能整体失效：降级内存并留痕。"""
    monkeypatch.setenv("FILE_STORE_BACKEND", "redis")
    monkeypatch.setenv("FILE_STORE_REDIS_URL", "redis://127.0.0.1:1/0")

    assert isinstance(filestore._build_backend(), _MemoryBackend)


def test_ttl_setting_is_honored(monkeypatch):
    monkeypatch.setenv("FILE_STORE_BACKEND", "memory")
    monkeypatch.setenv("FILE_STORE_TTL_SECONDS", "42")
    backend = filestore._build_backend()
    assert backend._ttl == 42
