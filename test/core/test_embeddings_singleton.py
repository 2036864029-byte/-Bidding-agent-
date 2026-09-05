"""9.3 Track A 验收：core.embeddings 进程级单例（9.3 代码优化执行计划 / A-1、A-4）。

断言 get_embeddings 全工程共享同一实例、惰性仅创建一次、reset 后重建；
create_embeddings 工厂签名不变（兼容既有导入面）。
"""

from __future__ import annotations

import pytest

import core.embeddings as emb


@pytest.fixture(autouse=True)
def _clean_singleton():
    emb.reset_embeddings()
    yield
    emb.reset_embeddings()


def test_get_embeddings_returns_same_instance():
    a = emb.get_embeddings()
    b = emb.get_embeddings()
    assert a is b


def test_get_embeddings_created_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        emb.bge_m3,
        "create_embeddings",
        lambda settings: calls.append(settings) or object(),
    )
    emb.get_embeddings()
    emb.get_embeddings()
    assert len(calls) == 1


def test_reset_embeddings_rebuilds():
    first = emb.get_embeddings()
    emb.reset_embeddings()
    second = emb.get_embeddings()
    assert first is not second


def test_create_embeddings_factory_still_exported():
    """A-2 兼容面：core.embeddings 包与 bge_m3 子模块导出同一工厂。"""
    from core.embeddings import create_embeddings as pkg_create
    from core.embeddings.bge_m3 import create_embeddings as bge_create

    assert pkg_create is bge_create
    assert callable(pkg_create)
