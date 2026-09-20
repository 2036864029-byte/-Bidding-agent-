"""/chat + /health + /chat/stream 端点测试（mock AgentGraph，不触网络）（方案 Phase 2 / C-5、C-9）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.server import create_app


class FakeAgent:
    """mock AgentGraph：记录 thread_id，返回固定结构。"""

    def __init__(self) -> None:
        self.last_thread: str | None = None

    def invoke(self, question: str, thread_id: str = "default", *, context_docs=None) -> dict:
        self.last_thread = thread_id
        return {
            "answer": "mock 答案【来源1】",
            "intent": "knowledge_qa",
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "mock 答案【来源1】",
                "data": {
                    "sources": [{"doc": "法A"}],
                    "citations": [{"context_index": 1, "doc_name": "法A", "text": "第一条"}],
                },
            },
        }

    def stream(self, question: str, thread_id: str = "default", *, context_docs=None):
        self.last_thread = thread_id
        yield {"type": "node", "node": "knowledge_qa"}
        yield {"type": "final", "answer": "ok"}


class _TokenFakeAgent(FakeAgent):
    """invoke 期间经流式 sink 逐 token 输出（模拟生成节点 .stream 行为，W3-2）。"""

    def invoke(self, question: str, thread_id: str = "default", *, context_docs=None) -> dict:
        from core.streaming.sink import emit_token

        self.last_thread = thread_id
        for tok in ["mock", "答案", "【来源1】"]:
            emit_token(tok)
        return {
            "answer": "mock 答案【来源1】",
            "intent": "knowledge_qa",
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "mock 答案【来源1】",
                "data": {"sources": [{"doc": "法A"}]},
            },
        }


def _client() -> TestClient:
    fake = FakeAgent()
    return TestClient(create_app(get_agent_fn=lambda: fake)), fake


def test_health_ok():
    client, _ = _client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_chat_invoke_structure_and_session_mapping():
    client, fake = _client()
    r = client.post("/chat", json={"question": "招标方式有哪些？", "session_id": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("mock 答案")
    assert body["reply_type"] == "kb_answer"
    assert body["intent"] == "knowledge_qa"
    assert body["citations"][0]["doc_name"] == "法A"
    assert isinstance(body["latency_ms"], int)
    assert body["session_id"] == "s1"
    assert fake.last_thread == "default::s1"  # W4-3：thread_id = 租户作用域(session_id)


def test_chat_without_session_id_generates_isolated_session():
    """W0-6：未传 session_id 时不再共用 "default"，服务端生成并回传。"""
    client, fake = _client()
    r = client.post("/chat", json={"question": "q"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert sid and sid != "default"
    assert fake.last_thread == f"default::{sid}"  # W4-3：租户作用域 thread_id


def test_tenant_header_isolates_thread_id():
    """W4-3：同一 session_id 在不同租户下 → 不同 thread_id（会话/检查点隔离）。"""
    client, fake = _client()
    r1 = client.post(
        "/chat", json={"question": "q", "session_id": "s1"},
        headers={"X-Tenant-ID": "tenantA"},
    )
    assert r1.status_code == 200
    assert r1.json()["session_id"] == "s1"  # 回传客户端的是原始 id（不带租户前缀）
    t_a = fake.last_thread

    client.post(
        "/chat", json={"question": "q", "session_id": "s1"},
        headers={"X-Tenant-ID": "tenantB"},
    )
    t_b = fake.last_thread

    assert t_a == "tenantA::s1" and t_b == "tenantB::s1"
    assert t_a != t_b  # 同 session_id 跨租户互不可见


def test_two_anonymous_requests_do_not_share_thread():
    """W0-6：两个匿名请求必须落在不同会话，否则对话历史互相串扰。"""
    client, fake = _client()
    first = client.post("/chat", json={"question": "q1"}).json()["session_id"]
    second = client.post("/chat", json={"question": "q2"}).json()["session_id"]
    assert first != second


def test_chat_empty_question_422():
    client, _ = _client()
    assert client.post("/chat", json={"question": ""}).status_code == 422
    assert client.post("/chat", json={"question": "x" * 2001}).status_code == 422


def test_chat_stream_flag_returns_400():
    client, _ = _client()
    r = client.post("/chat", json={"question": "q", "stream": True})
    assert r.status_code == 400


def test_chat_stream_sse():
    client, fake = _client()
    r = client.post("/chat/stream", json={"question": "q", "stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    text = r.text
    assert "event: session" in text  # W0-6：首个事件回传会话 id
    assert "event: done" in text
    # W3-2：终态经 result 事件回传（与 POST /chat 同构），intent 可见
    assert "event: result" in text
    assert "knowledge_qa" in text
    assert "mock 答案" in text
    assert fake.last_thread and fake.last_thread != "default"


def test_chat_stream_emits_tokens_before_result():
    """W3-2：生成节点逐 token 输出 → SSE 先 token 事件后 result 终态。"""
    client = TestClient(create_app(get_agent_fn=lambda: _TokenFakeAgent()))
    r = client.post("/chat/stream", json={"question": "q", "stream": True})
    assert r.status_code == 200
    text = r.text
    assert '"delta"' in text  # token 事件负载
    assert text.count("event: token") == 3
    # 三个 token 全部在 result 终态之前按序输出
    idx_delta = [i for i in range(len(text)) if text.startswith('"delta"', i)]
    idx_result = text.index("event: result")
    assert len(idx_delta) == 3
    assert all(i < idx_result for i in idx_delta)
    assert text.index('"mock"') < text.index('"答案"') < text.index('"【来源1】"')


def test_chat_stream_false_degrades_to_invoke():
    client, _ = _client()
    r = client.post("/chat/stream", json={"question": "q", "stream": False})
    assert r.status_code == 200
    assert r.json()["answer"].startswith("mock 答案")
