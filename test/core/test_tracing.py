"""core.observability.tracing — OTel 追踪离线测试（E0-3）。

不触网：用 InMemorySpanExporter 验证 span 属性、父子嵌套、跨线程（copy_context）
传播、未启用时的 no-op 行为，以及四个埋点点位（节点 / LLM / 检索 / 请求）。
"""

from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)


@pytest.fixture
def exporter(monkeypatch):
    """把 tracing.get_tracer 指向带 InMemorySpanExporter 的测试 provider。"""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import core.observability.tracing as tracing

    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    monkeypatch.setattr(tracing, "get_tracer", lambda name="ztb": provider.get_tracer(name))
    return exp


def _names(exp):
    return {s.name: s for s in exp.get_finished_spans()}


# ── 基础契约 ──────────────────────────────────────────────────

def test_span_records_attributes(exporter):
    from core.observability.tracing import span

    with span("demo", **{"ztb.node": "router", "ztb.intent": "knowledge_qa"}):
        pass

    sp = _names(exporter)["demo"]
    assert sp.attributes["ztb.node"] == "router"
    assert sp.attributes["ztb.intent"] == "knowledge_qa"


def test_nested_spans_have_parent(exporter):
    from core.observability.tracing import span

    with span("parent"):
        with span("child"):
            pass

    spans = _names(exporter)
    assert spans["child"].parent.span_id == spans["parent"].context.span_id


def test_span_nests_across_copied_context(exporter):
    """SSE 路径：daemon 线程经 copy_context().run() 带入上下文，子 span 仍挂父 span。"""
    import contextvars
    import threading

    from core.observability.tracing import span

    done = {}

    def worker():
        with span("child"):
            done["ok"] = True

    with span("parent"):
        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(worker))
        t.start()
        t.join()

    spans = _names(exporter)
    assert done["ok"] is True
    assert spans["child"].parent.span_id == spans["parent"].context.span_id


def test_span_records_exception(exporter):
    from core.observability.tracing import span

    with pytest.raises(ValueError):
        with span("boom"):
            raise ValueError("bad")

    sp = _names(exporter)["boom"]
    assert sp.status.status_code.name == "ERROR"
    assert len(sp.events) >= 1  # record_exception 写入事件


def test_setup_tracing_disabled_by_default():
    """默认配置下不启用（不触网、零开销）。"""
    from core.config.settings import Settings
    from core.observability.tracing import setup_tracing

    s = Settings(enable_tracing=False)
    assert setup_tracing(s) is False


def test_setup_tracing_requires_keys():
    from core.config.settings import Settings
    from core.observability.tracing import setup_tracing

    s = Settings(enable_tracing=True, langfuse_public_key="", langfuse_secret_key="")
    assert setup_tracing(s) is False


# ── 埋点点位 ──────────────────────────────────────────────────

def test_node_span_from_with_fallback(exporter):
    from core.errors.fallback import with_fallback

    def my_node(state, config=None):
        return {"business_result": {"branch": "general_chat", "answer": "ok", "data": None}}

    out = with_fallback(my_node)({"messages": []})

    assert out["business_result"]["branch"] == "general_chat"
    sp = _names(exporter)["node.my_node"]
    assert sp.attributes["ztb.branch"] == "general_chat"
    assert "ztb.latency_ms" in sp.attributes


def test_node_span_marks_fallback_on_error(exporter):
    from core.errors.fallback import with_fallback

    def boom(state, config=None):
        raise RuntimeError("db down")

    out = with_fallback(boom)({"messages": []})

    assert out["business_result"]["branch"] == "fallback"
    assert _names(exporter)["node.boom"].attributes["ztb.branch"] == "fallback"


def test_llm_callback_emits_span(exporter):
    from core.observability.callbacks import LLMMetricsCallback

    cb = LLMMetricsCallback("kb_generation", "deepseek-chat")
    cb.on_chat_model_start({}, [], run_id="r1")

    class _Resp:
        llm_output = {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}}

    cb.on_llm_end(_Resp(), run_id="r1")

    sp = _names(exporter)["llm.call"]
    assert sp.attributes["gen_ai.request.model"] == "deepseek-chat"
    assert sp.attributes["gen_ai.usage.input_tokens"] == 100
    assert sp.attributes["gen_ai.usage.output_tokens"] == 50
    assert sp.attributes["ztb.slot"] == "kb_generation"


def test_kb_search_emits_retrieval_span(exporter, monkeypatch):
    from types import SimpleNamespace

    from langchain_core.documents import Document

    import public_kb.tools as tools_mod

    docs = [(Document(page_content="内容", metadata={"doc_name": "法", "chapter": "章", "chunk_id": 1}), 0.9)]
    fake = SimpleNamespace(
        retrieve=lambda q: SimpleNamespace(
            docs=docs, diagnostics=SimpleNamespace(to_dict=lambda: {"retrieval_mode": "hybrid_rerank"})
        )
    )
    monkeypatch.setattr(tools_mod, "_get_retriever", lambda: fake)

    tools_mod.kb_search("测试", top_k=3)

    sp = _names(exporter)["retrieval.kb_search"]
    assert sp.attributes["ztb.retrieval.docs"] == 1
    assert sp.attributes["ztb.retrieval.mode"] == "hybrid_rerank"
