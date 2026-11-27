"""channels.feishu.handler — 事件处理：收消息 → 调后端 → 回复（主线 MVP）。

收到 ``im.message.receive_v1`` 后：
1. 先打 emoji ack（最轻的"已收到"信号，不产生消息噪声——单次问答 3~10s）；
2. 丢线程池处理（不阻塞长连接线程）；同 event_id 幂等去重；
3. 只处理 text 消息（MVP；图片/文件等后续 P4 再做）；
4. open_id 白名单校验（防非法字符进 session_id / 日志，见执行计划 D5 的坑）；
5. 调后端 /chat（带 X-Tenant-ID / X-Request-ID）→ 渲染 → 回复；
6. 后端不可用 / 处理异常 → 回友好提示，**不静默失败**。

client 为 lark_oapi.Client（回复/表情用，可注入假件），backend 为 BackendClient
（可注入 MockTransport）——hermetic 测试不真连飞书与后端。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lark_oapi.api.im.v1 import (
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    Emoji,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from core.feishu_binding import is_valid_open_id

from . import telemetry
from .backend import BackendClient, BackendError
from .render import render_card_reply
from .session import session_id_for, tenant_id_for

logger = logging.getLogger(__name__)

_ACK_EMOJI = "👀"          # 收到消息的即时反馈
_MAX_TEXT_CHARS = 1500     # 飞书单条文本上限内留余量
_MAX_SEEN = 1024           # 幂等去重集上限（有界，防无限增长）
_MAX_TEXT_MSG = 2000       # 后端 question 上限（ChatRequest max_length=2000）


class FeishuBot:
    """收到消息 → 后端问答 → 回发。线程池隔离，单条失败不影响其它消息。"""

    def __init__(
        self,
        client: Any,
        backend: BackendClient,
        *,
        max_workers: int = 4,
    ) -> None:
        self._client = client
        self._backend = backend
        self._max_workers = max(1, max_workers)
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="feishu-bot"
        )
        self._seen: set = set()
        self._seen_lock = threading.Lock()

    # ── SDK 事件入口（快速返回，处理丢线程池）───────────────────────

    def on_message(self, data: P2ImMessageReceiveV1) -> None:
        """lark-oapi 注册的 im.message.receive_v1 回调。"""
        event_id = ""
        try:
            event_id = (data.header and data.header.event_id) or ""
        except Exception:  # noqa: BLE001  事件字段缺失也继续
            pass
        with self._seen_lock:
            if event_id and event_id in self._seen:
                # 飞书超时重投很常见：重复事件必须丢弃，并计入 duplicate
                telemetry.record_message("duplicate")
                return
            if event_id:
                self._seen.add(event_id)
                if len(self._seen) > _MAX_SEEN:
                    self._seen = set(list(self._seen)[-_MAX_SEEN:])
        # 心跳里记"最近收到事件"，健康检查据此判断通道是否还活着
        try:
            telemetry.get_heartbeat().touch_event()
        except Exception:  # noqa: BLE001  遥测失败不影响消息处理
            pass
        self._pool.submit(self._handle, data)

    def _handle(self, data: P2ImMessageReceiveV1) -> None:
        t0 = time.perf_counter()
        result = "error"
        try:
            result = self._process(data)
        except Exception:  # noqa: BLE001  事件处理异常不拖垮长连接线程
            logger.exception("飞书消息处理异常")
        finally:
            telemetry.record_message(result, time.perf_counter() - t0)
            try:
                telemetry.get_heartbeat().touch_reply()
            except Exception:  # noqa: BLE001
                pass

    # ── 核心处理（同步，测试可直接调用）────────────────────────────

    def _process(self, data: P2ImMessageReceiveV1) -> str:
        """处理一条消息，返回结果标签（进指标）：ok / ignored / backend_error / error。"""
        msg = data.event.message
        if not msg or msg.message_type != "text":
            return "ignored"     # MVP：只处理文字（图片/文件后续 P4）
        try:
            text = (json.loads(msg.content or "{}").get("text") or "").strip()
        except (ValueError, TypeError):
            text = ""
        if not text:
            return "ignored"

        sender = data.event.sender
        open_id = (sender.sender_id and sender.sender_id.open_id) or ""
        if not is_valid_open_id(open_id):
            logger.warning("非法 open_id，忽略消息: %r", open_id[:32])
            return "ignored"

        message_id = msg.message_id or ""
        if message_id:
            self._ack(message_id)

        session_id = session_id_for(open_id, msg.chat_id or "", msg.chat_type or "p2p")
        tenant = tenant_id_for(open_id)
        event_id = ""
        try:
            event_id = (data.header and data.header.event_id) or ""
        except Exception:  # noqa: BLE001
            pass

        try:
            resp = self._backend.chat(
                text[: _MAX_TEXT_MSG],
                session_id=session_id,
                tenant_id=tenant,
                request_id=event_id,
            )
        except BackendError as exc:
            logger.warning("后端不可用: %s", exc)
            self._reply(message_id, "后端服务暂时不可用，请稍后再试。")
            return "backend_error"
        except Exception:  # noqa: BLE001  未知异常兜底
            logger.exception("调用后端失败")
            self._reply(message_id, "抱歉，处理出错，请稍后重试。")
            return "error"

        card = render_card_reply(resp)
        logger.info(
            "渲染 reply_type=%s tags=%s",
            resp.get("reply_type"),
            [e.get("tag") for e in card.get("elements", [])],
        )
        self._reply(message_id, card)
        return "ok"

    # ── 出站调用（可被测试断言）───────────────────────────────────

    def _ack(self, message_id: str) -> None:
        try:
            req = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(_ACK_EMOJI).build())
                    .build()
                )
                .build()
            )
            self._client.im.v1.message_reaction.create(req)
        except Exception:  # noqa: BLE001  ack 失败不影响主流程
            logger.debug("ack 失败 message_id=%s", message_id[:16])

    def _reply(self, message_id: str, payload: Any) -> None:
        """回发消息。payload 为 str → 文本消息；dict → interactive 卡片（成功回答）。"""
        if not message_id:
            telemetry.record_reply(False)
            return
        if isinstance(payload, dict):
            msg_type = "interactive"
            content = json.dumps(payload, ensure_ascii=False)
        else:
            text = str(payload)
            if len(text) > _MAX_TEXT_CHARS:
                text = text[: _MAX_TEXT_CHARS] + "\n…（内容过长已截断，请回电脑端查看）"
            msg_type = "text"
            content = json.dumps({"text": text}, ensure_ascii=False)
        try:
            req = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.reply(req)
            # lark-oapi 业务失败不抛异常，必须看响应码（卡片被拒是静默的）
            code = getattr(resp, "code", 0)
            if code not in (0, None):
                logger.warning(
                    "回复被飞书拒绝: code=%s msg=%s msg_type=%s",
                    code, getattr(resp, "msg", ""), msg_type,
                )
                telemetry.record_reply(False)
            else:
                logger.info(
                    "已回复 message_id=%s（%s，%d 字）",
                    message_id[:16], msg_type, len(content),
                )
                telemetry.record_reply(True)
        except Exception as exc:  # noqa: BLE001  回复失败记日志（如被移出群）
            logger.warning("回复失败 message_id=%s: %s", message_id[:16], exc)
            telemetry.record_reply(False)

    def stop(self) -> None:
        """停线程池（优雅退出用；长连接本身随进程退出关闭）。"""
        self._pool.shutdown(wait=False, cancel_futures=True)


__all__ = ["FeishuBot"]
