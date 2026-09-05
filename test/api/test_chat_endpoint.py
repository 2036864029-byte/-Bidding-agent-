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
    assert fake.last_thread == "s1"  # session_id → thread_id


def test_chat_default_session_is_default():
    client, fake = _client()
    client.post("/chat", json={"question": "q"})
    assert fake.last_thread == "default"


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
    assert "event: done" in text
    assert "knowledge_qa" in text  # 图事件逐块转发
    assert fake.last_thread == "default"


def test_chat_stream_false_degrades_to_invoke():
    client, _ = _client()
    r = client.post("/chat/stream", json={"question": "q", "stream": False})
    assert r.status_code == 200
    assert r.json()["answer"].startswith("mock 答案")
