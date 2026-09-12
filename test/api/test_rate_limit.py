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


def test_client_key_prefers_tenant():
    """W4-3：限流键**租户优先**（配额按租户计）；无租户回退 IP。"""
    from starlette.requests import Request

    from api.middleware.rate_limit import RateLimitMiddleware

    def _req(headers: dict) -> Request:
        return Request({
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("1.2.3.4", 123),
        })

    assert RateLimitMiddleware.client_key(_req({"X-Tenant-ID": "acme"})) == "tenant:acme"
    assert RateLimitMiddleware.client_key(_req({})) == "1.2.3.4"
    assert RateLimitMiddleware.client_key(_req({"X-Forwarded-For": "9.9.9.9, 8.8.8.8"})) == "9.9.9.9"


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


# ── C4：全局限流（服务端总量）────────────────────────────────

def test_global_limit_ignores_client_identity(monkeypatch):
    """全局限流是**服务端总量**：换不同客户端也照样拦（与按客户端配额不同）。"""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")       # 客户端级宽松
    monkeypatch.setenv("RATE_LIMIT_BURST", "20")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_PER_MINUTE", "60")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_BURST", "1")
    client = _client()

    first = client.post("/chat", json={"question": "q"},
                        headers={"X-Forwarded-For": "1.1.1.1"})
    assert first.status_code == 200

    # 换一个"客户端"仍被拦 → 说明拦的是全局总量，不是客户端配额
    blocked = client.post("/chat", json={"question": "q"},
                          headers={"X-Forwarded-For": "2.2.2.2"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "sys_2003"


def test_global_limit_disabled_by_default(monkeypatch):
    """`RATE_LIMIT_GLOBAL_PER_MINUTE=0`（默认）→ 不做全局限流。"""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("RATE_LIMIT_BURST", "20")
    monkeypatch.setenv("RATE_LIMIT_GLOBAL_PER_MINUTE", "0")
    client = _client()

    for i in range(5):
        r = client.post("/chat", json={"question": "q"},
                        headers={"X-Forwarded-For": f"3.3.3.{i}"})
        assert r.status_code == 200


def test_middleware_accepts_redis_backend_and_global_params():
    """后端与全局参数可注入（Redis 不可用时应 fail-open 而非报错）。"""
    from api.middleware.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware(
        app=None,
        per_minute=60, burst=5,
        global_per_minute=120, global_burst=10,
        backend="redis", redis_url="redis://127.0.0.1:1/0",   # 故意不可达
    )
    assert mw.backend == "redis"
    assert mw.enabled is True
    # fail-open：Redis 不可达时放行，不抛错
    assert mw._global_limiter.allow(mw.GLOBAL_KEY, now=0.0) is True


def test_status_polling_is_exempt_from_rate_limit(monkeypatch):
    """轮询型端点 `GET /files/{id}/status` 豁免限流 —— 前端高频轮询不该 429。

    背景：该端点是廉价内存查表（无 LLM/DB 开销），但前端要按秒级轮询；
    实跑发现 0.5s 间隔轮询 5 个文件即打满按客户端令牌桶，全部 429。
    """
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("RATE_LIMIT_BURST", "1")     # 极紧：豁免之外的请求都会 429
    client = _client()

    # 先确认限流确实在生效
    assert client.post("/chat", json={"question": "q"}).status_code == 200
    assert client.post("/chat", json={"question": "q"}).status_code == 429

    # 轮询端点应豁免：连打 5 次都是 404（文件不存在），绝不能是 429
    for _ in range(5):
        r = client.get("/files/whatever-id/status")
        assert r.status_code == 404, f"轮询端点不应被限流，实得 {r.status_code}"


