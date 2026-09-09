"""W2-5 限流测试：令牌桶语义 + 中间件 429 / 豁免 / 关闭。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.middleware.rate_limit import TokenBucket


class _FakeAgent:
    def invoke(self, question, thread_id="default", *, context_docs=None):
        return {
            "answer": "ok", "intent": "general_chat",
            "business_result": {"branch": "general_chat", "answer": "ok", "data": None},
        }


def _client() -> TestClient:
    from api.server import create_app

    return TestClient(create_app(get_agent_fn=lambda: _FakeAgent()))


# ── 令牌桶 ───────────────────────────────────────────────────

def test_token_bucket_allows_burst_then_blocks():
    bucket = TokenBucket(capacity=2, refill_per_second=0.0, now=0.0)
    assert bucket.allow(now=0.0) is True
    assert bucket.allow(now=0.0) is True
    assert bucket.allow(now=0.0) is False


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(capacity=1, refill_per_second=1.0, now=0.0)
    assert bucket.allow(now=0.0) is True
    assert bucket.allow(now=0.5) is False
    assert bucket.allow(now=1.0) is True


# ── 中间件 ───────────────────────────────────────────────────

def test_rate_limit_blocks_after_burst(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1")
    client = _client()

    assert client.post("/chat", json={"question": "q"}).status_code == 200

    blocked = client.post("/chat", json={"question": "q"})
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["error"]["code"] == "sys_2003"
    assert body["error"]["category"] == "system"


def test_rate_limit_exempts_health_and_metrics(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1")
    client = _client()

    for _ in range(3):
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200


def test_rate_limit_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1")
    client = _client()

    for _ in range(5):
        assert client.post("/chat", json={"question": "q"}).status_code == 200
