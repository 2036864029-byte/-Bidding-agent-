"""api.feishu_oauth 端点测试（并行线 A）—— 换 token 打桩，不真连飞书。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.server import create_app
from core import feishu_binding as fb


def _client() -> TestClient:
    # follow_redirects=False：callback 端点 302 到前端，不跟随（跟随会落到后端
    # POST-only 的 /chat 或后端不存在的 /，得到 405/404 而看不到原始 302）。
    return TestClient(create_app(get_agent_fn=lambda: None), follow_redirects=False)


# ── auth_url ────────────────────────────────────────────────────


def test_auth_url_503_without_config(monkeypatch):
    # 不依赖"本机恰好没配飞书"（.env 可能已有真实凭据，见执行进度 §4.4 的教训）：
    # 显式清掉三个配置键，断言"无配置 → 503"才有意义。
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_REDIRECT_URI", raising=False)
    r = _client().get("/feishu/auth_url", params={"session_id": "s1"})
    assert r.status_code == 503


def test_auth_url_ok(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_testapp")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-test")
    monkeypatch.setenv("FEISHU_REDIRECT_URI", "http://localhost:5199/feishu/cb")
    r = _client().get("/feishu/auth_url", params={"session_id": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert "authorize" in body["auth_url"]
    assert "client_id=cli_testapp" in body["auth_url"]
    assert "redirect_uri=" in body["auth_url"]
    assert "state=" in body["auth_url"]
    assert body["state"]


# ── oauth/callback ──────────────────────────────────────────────


def test_callback_binds_open_id(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_testapp")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-test")
    from api import feishu_oauth as fo

    monkeypatch.setattr(fo, "_exchange_token", lambda code, s: "ou_bound_user")
    st = fo._make_state("s1", "secret-test")
    fb.reset_bindings()
    r = _client().get(
        "/feishu/oauth/callback",
        params={"code": "auth-code", "state": st, "redirect": "/chat"},
    )
    assert r.status_code == 302
    assert "feishu=bound" in r.headers["location"]
    assert "/chat" in r.headers["location"]
    assert fb.lookup("s1") == "ou_bound_user"


def test_callback_rejects_forged_state(monkeypatch):
    monkeypatch.setenv("FEISHU_APP_ID", "cli_testapp")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-test")
    fb.reset_bindings()
    r = _client().get(
        "/feishu/oauth/callback",
        params={"code": "x", "state": "s1.forged", "redirect": "/"},
    )
    assert r.status_code == 400
    assert not fb.is_bound("s1")


def test_callback_503_without_config(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    fb.reset_bindings()
    r = _client().get(
        "/feishu/oauth/callback", params={"code": "x", "state": "a.b"}
    )
    assert r.status_code == 503


def test_callback_sanitizes_redirect_target(monkeypatch):
    """防 open redirect：危险 redirect 值一律回落默认目标（FEISHU_FRONTEND_URL）。

    FEISHU_FRONTEND_URL 显式设置，避免从 .env 读到机器相关的地址（否则换台机器就红）。
    """
    monkeypatch.setenv("FEISHU_APP_ID", "cli_testapp")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-test")
    monkeypatch.setenv("FEISHU_FRONTEND_URL", "http://192.168.0.101:5173")
    from api import feishu_oauth as fo

    monkeypatch.setattr(fo, "_exchange_token", lambda code, s: "ou_bound_user")
    st = fo._make_state("s1", "secret-test")
    fb.reset_bindings()
    for evil in (
        "//evil.example",       # 协议相对（双斜杠）
        "/\\evil.example",      # 浏览器把 \ 归一化成 // 的变体
        "javascript:alert(1)",  # 非 http 协议
        "data:text/html,x",     # 非 http 协议
        "http://evil.example",  # 跨源绝对地址（open redirect 主向量）
        "https://evil.example/x",
        "http:/evil.example",   # 单斜杠畸形绝对地址
    ):
        r = _client().get(
            "/feishu/oauth/callback",
            params={"code": "c", "state": st, "redirect": evil},
        )
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc == "http://192.168.0.101:5173?feishu=bound", f"redirect 未净化: {loc}"


def test_callback_allows_same_origin_absolute(monkeypatch):
    """同源绝对地址是合法回跳目标（用户显式指定时）。"""
    monkeypatch.setenv("FEISHU_APP_ID", "cli_testapp")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-test")
    monkeypatch.setenv("FEISHU_FRONTEND_URL", "http://192.168.0.101:5173")
    from api import feishu_oauth as fo

    monkeypatch.setattr(fo, "_exchange_token", lambda code, s: "ou_bound_user")
    st = fo._make_state("s1", "secret-test")
    fb.reset_bindings()
    r = _client().get(
        "/feishu/oauth/callback",
        params={"code": "c", "state": st, "redirect": "http://192.168.0.101:5173/chat"},
    )
    assert r.status_code == 302
    assert r.headers["location"] == "http://192.168.0.101:5173/chat?feishu=bound"


def test_callback_falls_back_to_frontend_url(monkeypatch):
    """飞书回调**只带 code+state、不带 redirect** —— 回跳必须回落 FEISHU_FRONTEND_URL。"""
    monkeypatch.setenv("FEISHU_APP_ID", "cli_testapp")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-test")
    monkeypatch.setenv("FEISHU_FRONTEND_URL", "http://192.168.0.101:5173")
    from api import feishu_oauth as fo

    monkeypatch.setattr(fo, "_exchange_token", lambda code, s: "ou_bound_user")
    st = fo._make_state("s1", "secret-test")
    fb.reset_bindings()
    r = _client().get(
        "/feishu/oauth/callback", params={"code": "auth-code", "state": st}
    )
    assert r.status_code == 302
    assert r.headers["location"] == "http://192.168.0.101:5173?feishu=bound"
    assert fb.lookup("s1") == "ou_bound_user"


# ── binding 查询 ────────────────────────────────────────────────


def test_binding_returns_bound_state():
    fb.reset_bindings()
    client = _client()
    assert client.get("/feishu/binding", params={"session_id": "s1"}).json()["bound"] is False
    fb.bind("s1", "ou_x")
    body = client.get("/feishu/binding", params={"session_id": "s1"}).json()
    assert body["bound"] is True
