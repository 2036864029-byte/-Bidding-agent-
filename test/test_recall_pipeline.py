"""RecallPipeline 熔断状态机离线测试（方案 Phase 3 / D-1、D-9）。

全 fake stage，不触任何库/网络。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from core.retrieval.base import RecallOutcome, RecallStage
from core.retrieval.pipeline import RecallPipeline


class FakeStage(RecallStage):
    def __init__(self, name: str, fn: Callable[..., Tuple[bool, List[Dict[str, Any]]]], is_scan: bool = False):
        self.name = name
        self._fn = fn
        self.is_scan = is_scan
        self.called = False

    def run(self, ctx, *, deadline_ms: int):
        self.called = True
        return self._fn(ctx, deadline_ms=deadline_ms)


def _hit(rows):
    return True, rows


def _miss(*_a, **_k):
    return False, []


def _boom(*_a, **_k):
    raise TimeoutError("slow")


def test_first_hit_short_circuits():
    later = FakeStage("later", _miss)
    early = FakeStage("early", lambda *a, **k: _hit([{"id": 1}]))
    out = RecallPipeline([early, later], deadline_ms=1000).run({"q": "x"})
    assert out.ok and out.rows == [{"id": 1}]
    assert later.called is False  # 短路：命中后不再执行后续阶段
    assert "early" in out.stage_trace[0]


def test_all_miss_returns_not_ok_with_reason():
    stages = [FakeStage("a", _miss), FakeStage("b", _miss)]
    out = RecallPipeline(stages).run()
    assert out.ok is False
    assert out.reason
    assert "a:hit=False" in out.stage_trace[0]


def test_scan_stage_gated_by_allow_scan():
    scan = FakeStage("scan", lambda *a, **k: _hit([{"id": 9}]), is_scan=True)
    # 默认禁入：扫描被跳过 → 未命中
    out = RecallPipeline([scan], deadline_ms=1000).run()
    assert out.ok is False
    assert scan.called is False
    assert any("full_scan_disabled" in t for t in out.stage_trace)
    # 显式放行 → 执行命中
    scan2 = FakeStage("scan", lambda *a, **k: _hit([{"id": 9}]), is_scan=True)
    out2 = RecallPipeline([scan2], deadline_ms=1000, allow_scan=True).run()
    assert out2.ok and out2.rows == [{"id": 9}]
    assert scan2.called is True


def test_timeout_fuses_and_stops_later_stages():
    slow = FakeStage("slow", _boom)
    later = FakeStage("later", _miss)
    out = RecallPipeline([slow, later], deadline_ms=1000).run()
    assert out.ok is False
    assert any("timeout" in t for t in out.stage_trace)
    assert later.called is False  # 熔断：不进入后续阶段


def test_stage_exception_continues_not_fuse():
    bad = FakeStage("bad", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    good = FakeStage("good", lambda *a, **k: _hit([{"id": 2}]))
    out = RecallPipeline([bad, good], deadline_ms=1000).run()
    assert out.ok and out.rows == [{"id": 2}]
    assert any("bad:error:RuntimeError" in t for t in out.stage_trace)


def test_deadline_exceeded_stops():
    """全局 deadline：极短 deadline 下第一个阶段即超限 → 熔断空。"""
    stage = FakeStage("s", lambda *a, **k: _hit([{"id": 1}]))
    out = RecallPipeline([stage], deadline_ms=0).run()
    assert out.ok is False
    assert any("deadline_exceeded" in t for t in out.stage_trace)


# ── E4：全链路 span（检索层可见性）────────────────────────────


import pytest  # noqa: E402


@pytest.fixture
def exporter(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import core.observability.tracing as tracing

    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    monkeypatch.setattr(tracing, "get_tracer", lambda name="ztb": provider.get_tracer(name))
    return exp


def _spans(exp):
    return {s.name: s for s in exp.get_finished_spans()}


def test_pipeline_emits_pipeline_and_stage_spans(exporter):
    stages = [FakeStage("exact", _miss),
              FakeStage("fulltext", lambda *a, **k: _hit([{"id": 1}]))]
    out = RecallPipeline(stages, deadline_ms=1000).run({"table": "bid_project"})
    assert out.ok
    sp = _spans(exporter)
    assert "retrieval.recall_pipeline" in sp
    assert "recall.exact" in sp and "recall.fulltext" in sp
    pipe = sp["retrieval.recall_pipeline"]
    assert pipe.attributes["ztb.recall.table"] == "bid_project"
    assert pipe.attributes["ztb.recall.ok"] is True
    assert pipe.attributes["ztb.recall.hits"] == 1
    ft = sp["recall.fulltext"]
    assert ft.attributes["ztb.recall.stage"] == "fulltext"
    assert ft.attributes["ztb.recall.hit"] is True
    assert ft.attributes["ztb.recall.rows"] == 1
    assert sp["recall.exact"].attributes["ztb.recall.hit"] is False


def test_skipped_scan_stage_emits_no_span(exporter):
    scan = FakeStage("scan", lambda *a, **k: _hit([{"id": 9}]), is_scan=True)
    RecallPipeline([scan], deadline_ms=1000).run()  # 默认禁入 → skip
    assert "recall.scan" not in _spans(exporter)
