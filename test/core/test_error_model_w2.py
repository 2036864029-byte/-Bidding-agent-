"""W2-3 统一错误模型测试：错误码 / 异常族 / 出口映射 / API 处理器 / supervisor 兜底。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from core.errors import (
    ErrorCategory,
    ErrorCode,
    InvalidRequestError,
    category_of,
)
from core.protocol.reply import to_reply

# ── 错误码与分类 ─────────────────────────────────────────────

def test_error_code_category_mapping():
    assert category_of(ErrorCode.INVALID_REQUEST) is ErrorCategory.BUSINESS
    assert category_of(ErrorCode.NODE_FAILED) is ErrorCategory.SYSTEM
    assert category_of(ErrorCode.LLM_FAILED) is ErrorCategory.MODEL
    assert category_of(ErrorCode.MILVUS_UNAVAILABLE) is ErrorCategory.DEPENDENCY
    assert category_of("unknown_9999") is ErrorCategory.SYSTEM


def test_app_error_envelope_contains_code_and_request_id():
    exc = InvalidRequestError("参数不合法", detail={"field": "question"})
    envelope = exc.to_envelope("rid-1")
    assert exc.http_status == 400
    assert envelope["error"]["code"] == "biz_1001"
    assert envelope["error"]["category"] == "business"
    assert envelope["error"]["request_id"] == "rid-1"
    assert envelope["error"]["detail"] == {"field": "question"}


# ── 出口映射（to_reply）──────────────────────────────────────

def test_to_reply_maps_explicit_error_code():
    reply = to_reply({
        "branch": "fallback",
        "answer": "功能不可用",
        "data": {"error_code": ErrorCode.NODE_FAILED.value},
    })
    assert reply.error_code == "sys_2001"


def test_to_reply_infers_knowledge_refusal_and_guardrail():
    refusal = to_reply({
        "branch": "knowledge_qa", "answer": "暂无相关内容",
        "data": {"citation_validation": {"is_refusal": True, "all_passed": True}},
    })
    assert refusal.error_code == ErrorCode.RETRIEVAL_EMPTY.value

    failed = to_reply({
        "branch": "knowledge_qa", "answer": "答",
        "data": {"citation_validation": {"is_refusal": False, "all_passed": False}},
    })
    assert failed.error_code == ErrorCode.GUARDRAIL_FAILED.value


def test_to_reply_infers_price_guidance_and_boundary():
    guidance = to_reply({
        "branch": "price_inquiry", "query_type": "unified_guidance",
        "answer": "请提供公司全称", "data": {},
    })
    assert guidance.error_code == ErrorCode.NO_VALID_ENTITY.value

    boundary = to_reply({
        "branch": "price_inquiry", "query_type": "capability_boundary",
        "answer": "不支持产品查询", "data": {},
    })
    assert boundary.error_code == ErrorCode.UNSUPPORTED_CAPABILITY.value


def test_to_reply_normal_answer_has_no_error_code():
    reply = to_reply({"branch": "general_chat", "answer": "你好", "data": None})
    assert reply.error_code is None


# ── API 处理器 ───────────────────────────────────────────────

class _RaisingAgent:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def invoke(self, question, thread_id="default", *, context_docs=None):
        raise self._exc


class _SSERaisingAgent(_RaisingAgent):
    def stream(self, question, thread_id="default", *, context_docs=None):
        raise RuntimeError("SECRET-internal-detail")
        yield  # pragma: no cover


def _client(agent) -> TestClient:
    from api.server import create_app

    return TestClient(create_app(get_agent_fn=lambda: agent), raise_server_exceptions=False)


def test_app_error_returns_unified_envelope():
    client = _client(_RaisingAgent(InvalidRequestError("参数不合法")))
    response = client.post("/chat", json={"question": "q"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "biz_1001"
    assert body["error"]["category"] == "business"


def test_unhandled_error_hides_internal_detail():
    client = _client(_RaisingAgent(RuntimeError("SECRET-internal-detail")))
    response = client.post("/chat", json={"question": "q"})
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "sys_2500"
    assert "SECRET" not in response.text


def test_sse_error_event_hides_internal_detail():
    client = _client(_SSERaisingAgent(RuntimeError("SECRET")))
    response = client.post("/chat/stream", json={"question": "q", "stream": True})
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "sys_2500" in response.text
    assert "SECRET-internal-detail" not in response.text


# ── supervisor 节点兜底 ──────────────────────────────────────

def test_supervisor_node_is_wrapped_by_fallback():
    """L3 路由抛错时整图不崩，落到 fallback 分支。"""
    from orchestration.main_graph import build_supervisor_graph

    def _boom(state):
        raise RuntimeError("router exploded")

    graph = build_supervisor_graph(llm_router=_boom)
    out = graph.invoke(
        {"messages": [HumanMessage(content="随便问问")]},
        config={"configurable": {"thread_id": "w2-3"}},
    )
    assert out["business_result"]["branch"] == "fallback"
    assert out["business_result"]["answer"]
