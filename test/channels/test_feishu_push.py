"""channels.feishu.push 测试（并行线 A）—— 假 http / 假 pusher，不真连飞书。"""

from __future__ import annotations

import httpx

from channels.feishu.push import (
    FeishuPusher,
    ProgressNotifier,
    render_progress_text,
)
from core import events

# ── 文案映射 ────────────────────────────────────────────────────


def test_render_progress_text_all_kinds():
    assert "开始解析" in render_progress_text(
        {"kind": "upload_started", "file_name": "a.pdf"}
    )
    assert "解析完成" in render_progress_text(
        {"kind": "upload_succeeded", "file_name": "a.pdf", "chunk_count": 3}
    )
    assert "解析失败" in render_progress_text(
        {"kind": "upload_failed", "file_name": "a.pdf", "reason": "boom"}
    )
    assert "回答就绪" in render_progress_text(
        {"kind": "chat_result", "answer": "招标方式包括公开招标与邀请招标。"}
    )
    assert render_progress_text({"kind": "unknown"}) is None


def test_answer_preview_truncated():
    long = "答" * 200
    text = render_progress_text({"kind": "chat_result", "answer": long})
    assert "完整内容请回电脑查看" in text
    # 摘要本身被截断到 60 字
    assert "答" * 60 not in text


# ── FeishuPusher ────────────────────────────────────────────────


def _mock_client() -> httpx.Client:
    def handler(request):
        url = str(request.url)
        if "/tenant_access_token/internal" in url:
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            )
        if "/im/v1/messages" in url:
            return httpx.Response(200, json={"code": 0, "data": {}})
        return httpx.Response(404, json={"code": 1, "msg": "unexpected"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pusher_send_text_ok():
    captured: dict = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.read().decode("utf-8")
        if "/tenant_access_token/internal" in captured["url"]:
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "t-1", "expire": 7200},
            )
        return httpx.Response(200, json={"code": 0, "data": {}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pusher = FeishuPusher(app_id="app", app_secret="sec", http_client=client)
    assert pusher.send_text("ou_user", "你好") is True
    assert "/im/v1/messages?receive_id_type=open_id" in captured["url"]
    assert captured["auth"] == "Bearer t-1"
    assert "ou_user" in captured["body"]
    assert "你好" in captured["body"]


def test_pusher_token_fetch_failure_falls_through():
    def handler(request):
        return httpx.Response(200, json={"code": 1, "msg": "bad app_id"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pusher = FeishuPusher(app_id="app", app_secret="sec", http_client=client)
    assert pusher.send_text("ou_user", "x") is False


def test_pusher_disabled_without_credentials():
    pusher = FeishuPusher(app_id="", app_secret="")
    assert pusher.enabled is False
    assert pusher.send_text("ou_user", "x") is False


# ── ProgressNotifier：事件 → 绑定用户推送 ─────────────────────────


class _FakePusher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, open_id: str, text: str) -> bool:
        self.sent.append((open_id, text))
        return True


def test_notifier_pushes_only_bound_users():
    fake = _FakePusher()
    n = ProgressNotifier(fake, lookup=lambda sid: "ou_user" if sid == "s1" else None)
    n.start()
    try:
        events.emit({"kind": "upload_started", "session_id": "s1", "file_name": "a.pdf"})
        events.emit({"kind": "upload_started", "session_id": "unbound", "file_name": "b.pdf"})
        events.emit({"kind": "chat_result", "session_id": "s1", "answer": "摘要"})
        n.flush()
    finally:
        n.stop()
    open_ids = [o for o, _ in fake.sent]
    assert open_ids == ["ou_user", "ou_user"]  # 只推绑定用户，未绑定不推
    assert "开始解析" in fake.sent[0][1]
    assert "回答就绪" in fake.sent[1][1]


def test_notifier_unknown_kind_noop():
    fake = _FakePusher()
    n = ProgressNotifier(fake, lookup=lambda sid: "ou_user")
    n.start()
    try:
        events.emit({"kind": "mystery", "session_id": "s1"})
        n.flush()
    finally:
        n.stop()
    assert fake.sent == []


def test_notifier_emit_is_fast_and_non_blocking():
    """emit 只做线程池提交，不因推送同步阻塞调用方。"""
    import time

    fake = _FakePusher()
    n = ProgressNotifier(fake, lookup=lambda sid: "ou_user")
    n.start()
    try:
        t0 = time.perf_counter()
        for i in range(50):
            events.emit({"kind": "upload_started", "session_id": "s1", "file_name": f"f{i}.pdf"})
        elapsed = time.perf_counter() - t0
        n.flush()
        assert elapsed < 0.5, f"emit 阻塞了调用方: {elapsed:.3f}s"
        assert len(fake.sent) == 50
    finally:
        n.stop()
