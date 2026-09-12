"""C6：Postgres 检查点集成测试 —— 跨实例共享会话（多副本前提）。

需要本机 PostgreSQL（`docker/infra/docker-compose.yml`）；不可用或未配置连接串则 skip，
保证无 PG 的环境仍能跑全量。
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def pg_url():
    from core.config.settings import Settings

    url = Settings().checkpointer_postgres_url
    if not url:
        pytest.skip("未配置 CHECKPOINTER_POSTGRES_URL")
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=2) as conn:
            conn.execute("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres 不可用（{url}）: {exc}")
    return url


@pytest.fixture
def saver(pg_url):
    """建好表的 PostgresSaver；用后清理本用例写入的 thread。"""
    from agent.checkpointer import create_checkpointer, delete_thread

    s = create_checkpointer("postgres", connection_string=pg_url)
    created: list[str] = []
    s._test_created = created  # 供用例登记 thread_id
    yield s
    for tid in created:
        delete_thread(s, tid)


def test_postgres_saver_setup_creates_tables(pg_url):
    from agent.checkpointer import create_checkpointer

    saver = create_checkpointer("postgres", connection_string=pg_url)
    assert type(saver).__name__ == "PostgresSaver"


def test_postgres_saver_rejects_empty_connection_string():
    from agent.checkpointer import create_checkpointer

    with pytest.raises(ValueError, match="connection_string"):
        create_checkpointer("postgres", connection_string="")


def test_checkpoint_visible_across_instances(pg_url, saver):
    """核心断言（C6）：A 实例写入的会话，B 实例能枚举到 —— 即跨副本/跨机共享。"""
    from langgraph.checkpoint.base import empty_checkpoint

    from agent.checkpointer import create_checkpointer, list_threads

    tid = f"c6-test-{uuid.uuid4().hex[:8]}"
    saver._test_created.append(tid)

    config = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
    saver.put(config, empty_checkpoint(), {"source": "input", "step": 0, "writes": {}}, {})

    # 新实例（模拟另一个副本/另一台机器）
    other = create_checkpointer("postgres", connection_string=pg_url)
    threads = list_threads(other)
    assert tid in threads, "另一实例应能看到该 thread（跨副本共享检查点）"


def test_delete_thread_removes_checkpoint(pg_url, saver):
    """过期清理（多副本安全的 sweep 依赖它）。"""
    from langgraph.checkpoint.base import empty_checkpoint

    from agent.checkpointer import delete_thread, list_threads

    tid = f"c6-del-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
    saver.put(config, empty_checkpoint(), {"source": "input", "step": 0, "writes": {}}, {})
    assert tid in list_threads(saver)

    assert delete_thread(saver, tid) is True
    assert tid not in list_threads(saver)
