"""检索结果缓存的写入纪律 —— 降级结果不得入缓存（驱动真实的 HybridRetriever.retrieve）。

真实踩坑（2026-09-14）：模型隧道断线期间检索降级为空（``hybrid_error:APIConnectionError``），
该结果被写进缓存 → **隧道恢复后同一个问题仍然答不出来**，直到 TTL 过期；
换个说法问同一件事却完全正常。诊断极易跑偏（"服务明明是通的"）。
"""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

import core.cache
from core.cache import MISSING
from core.contracts.contracts import RetrievalDiagnostics, RetrievalMode
from public_kb.retrieval.retriever import HybridRetriever, RetrievalResult


class _FakeCache:
    def __init__(self) -> None:
        self.stored: dict[str, Any] = {}

    def key(self, value: str) -> str:
        return f"k:{value}"

    def get(self, key: str) -> Any:
        return self.stored.get(key, MISSING)

    def set(self, key: str, value: Any) -> None:
        self.stored[key] = value


def _result(fallback_reason: str | None, docs: List[Tuple[Any, float]] | None = None) -> RetrievalResult:
    return RetrievalResult(
        docs=docs if docs is not None else [("chunk", 0.9)],
        diagnostics=RetrievalDiagnostics(
            retrieval_mode=RetrievalMode.DENSE_NATIVE,
            fallback_reason=fallback_reason,
        ),
    )


def _retriever_returning(result: RetrievalResult) -> HybridRetriever:
    """绕过 __init__ 造一个只实现 retrieve 依赖的实例（测的就是 retrieve 本身）。"""
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._attach_parent_context = lambda res, started: res  # type: ignore[method-assign]
    retriever._retrieve_impl = lambda question: result  # type: ignore[method-assign]
    return retriever


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch) -> _FakeCache:
    fake = _FakeCache()
    monkeypatch.setattr(core.cache, "get_cache", lambda name: fake)
    return fake


class TestCacheWriteDiscipline:
    def test_健康结果写入缓存(self, cache: _FakeCache) -> None:
        retriever = _retriever_returning(_result(None))
        retriever.retrieve("招标方式有哪些")
        assert len(cache.stored) == 1

    def test_依赖抖动导致的降级不写入(self, cache: _FakeCache) -> None:
        retriever = _retriever_returning(_result("hybrid_error:APIConnectionError"))
        out = retriever.retrieve("招标方式有哪些")
        assert out.diagnostics.fallback_reason == "hybrid_error:APIConnectionError"
        assert cache.stored == {}, "降级结果入缓存会把这个问题毒化到 TTL 过期"

    def test_集合缺失导致的降级不写入(self, cache: _FakeCache) -> None:
        retriever = _retriever_returning(_result("native_collection_or_embeddings_missing"))
        retriever.retrieve("招标方式有哪些")
        assert cache.stored == {}

    def test_空结果但非降级仍写入(self, cache: _FakeCache) -> None:
        """问题确实没有相关内容 —— 这是有效答案，缓存它没问题。"""
        retriever = _retriever_returning(_result(None, docs=[]))
        retriever.retrieve("一个知识库里确实没有的问题")
        assert len(cache.stored) == 1
