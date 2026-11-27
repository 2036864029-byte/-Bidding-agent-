"""channels.feishu.handler — 事件处理链路（假 client/假 backend，不真连）。"""

import json

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from channels.feishu.backend import BackendError
from channels.feishu.handler import FeishuBot

# ── 假件 ─────────────────────────────────────────────────────────


class _FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def reply(self, req):
        self._owner.replies.append(req)


class _FakeMessageReaction:
    def __init__(self, owner):
        self._owner = owner

    def create(self, req):
        self._owner.reactions.append(req)


class _FakeV1:
    def __init__(self, owner):
        self.message = _FakeMessages(owner)
        self.message_reaction = _FakeMessageReaction(owner)


class _FakeIm:
    def __init__(self, owner):
        self.v1 = _FakeV1(owner)


class FakeLarkClient:
    """模仿 lark.Client 的调用链：client.im.v1.message.reply(...)。"""

    def __init__(self):
        self.replies = []
        self.reactions = []
        self.im = _FakeIm(self)


class FakeBackend:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def chat(self, question, *, session_id, tenant_id, request_id=""):
        self.calls.append((question, session_id, tenant_id, request_id))
        if self.error is not None:
            raise self.error
        return self.response


def _event(
    text="你好",
    *,
    open_id="ou_user_a",
    message_id="om_1",
    event_id="ev_1",
    chat_type="p2p",
    chat_id="oc_1",
    message_type="text",
):
    d = {
        "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": open_id}, "sender_type": "user"},
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": chat_type,
                "message_type": message_type,
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }
    return P2ImMessageReceiveV1(d)


def _reply_text(bot) -> str:
    assert len(bot._client.replies) == 1
    req = bot._client.replies[0]
    return req.request_body.content  # 原始 content（文本或卡片 JSON 字符串）


# ── 用例 ─────────────────────────────────────────────────────────


def test_text_message_routes_to_backend_and_replies():
    client = FakeLarkClient()
    backend = FakeBackend({"answer": "招标方式包括**公开招标**。", "reply_type": "chat"})
    bot = FeishuBot(client, backend)

    bot._process(_event("招标方式有哪些？"))

    assert backend.calls == [
        ("招标方式有哪些？", "fs-p2p-ou_user_a", "feishu:ou_user_a", "ev_1")
    ]
    assert len(client.reactions) == 1  # ack
    req = client.replies[0]
    assert req.request_body.msg_type == "interactive"  # 成功回答发卡片（markdown 可渲染）
    assert "公开招标" in _reply_text(bot)


def test_non_text_message_ignored():
    client = FakeLarkClient()
    backend = FakeBackend({"answer": "不该回复", "reply_type": "chat"})
    bot = FeishuBot(client, backend)

    bot._process(_event(message_type="image"))
    assert backend.calls == []
    assert client.replies == []
    assert client.reactions == []


def test_empty_text_ignored():
    client = FakeLarkClient()
    backend = FakeBackend({"answer": "不该回复", "reply_type": "chat"})
    bot = FeishuBot(client, backend)

    bot._process(_event("   "))
    assert backend.calls == []
    assert client.replies == []


def test_invalid_open_id_ignored():
    client = FakeLarkClient()
    backend = FakeBackend({"answer": "不该回复", "reply_type": "chat"})
    bot = FeishuBot(client, backend)

    bot._process(_event(open_id="ou_bad/../path"))
    assert backend.calls == []
    assert client.replies == []
    assert client.reactions == []


def test_group_message_uses_group_session():
    client = FakeLarkClient()
    backend = FakeBackend({"answer": "ok", "reply_type": "chat"})
    bot = FeishuBot(client, backend)

    bot._process(_event(chat_type="group", chat_id="oc_group1"))
    assert backend.calls[0][1] == "fs-group-oc_group1"
    assert backend.calls[0][2] == "feishu:ou_user_a"


def test_backend_unavailable_replies_friendly():
    client = FakeLarkClient()
    backend = FakeBackend(error=BackendError("connect refused"))
    bot = FeishuBot(client, backend)

    bot._process(_event())
    assert "后端服务暂时不可用" in _reply_text(bot)


def test_backend_returns_error_code_shows_message():
    client = FakeLarkClient()
    backend = FakeBackend(
        {"answer": "", "reply_type": "fallback", "error_code": "sys_2003"}
    )
    bot = FeishuBot(client, backend)

    bot._process(_event())
    assert "频繁" in _reply_text(bot)


def test_same_event_id_deduplicated():
    client = FakeLarkClient()
    backend = FakeBackend({"answer": "ok", "reply_type": "chat"})
    bot = FeishuBot(client, backend, max_workers=1)

    ev = _event()
    bot.on_message(ev)
    bot.on_message(ev)  # 同 event_id 第二次应被去重（同步判断，不丢进池）
    assert len(bot._seen) == 1
    bot.stop()


def test_on_message_submits_to_pool():
    """on_message 快速返回（不阻塞长连接），处理在池里进行。"""
    import time

    client = FakeLarkClient()
    backend = FakeBackend({"answer": "ok", "reply_type": "chat"})
    bot = FeishuBot(client, backend, max_workers=1)

    bot.on_message(_event())
    deadline = time.time() + 5
    while not backend.calls and time.time() < deadline:
        time.sleep(0.01)
    assert backend.calls
    bot.stop()
