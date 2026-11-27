"""channels.feishu.push — 飞书主动消息推送（并行线 A）。

两条职责：
1. ``FeishuPusher``：``im.message.create`` 的薄封装（tenant_access_token 缓存）。
2. ``ProgressNotifier``：订阅 ``core.events`` 的进度事件 → 查绑定 →
   组装里程碑/结果摘要文案 → 线程池异步推送。

可测试性（hermetic）：
- ``http_client`` 可注入（``httpx.MockTransport`` 或假件），不真连飞书；
- 无 ``FEISHU_APP_ID`` 时 ``FeishuPusher`` 构造为 no-op（``enabled=False``），
  推送调用只记日志——本地未配凭据也能安全常驻。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import httpx

from core import events
from core.feishu_binding import lookup as binding_lookup

logger = logging.getLogger(__name__)

ANSWER_PREVIEW_CHARS = 60  # 结果摘要截断长度
_TOKEN_TTL_BUFFER_S = 120  # token 过期前提前刷新


def _record_push(result: str) -> None:
    """推送结果计数（遥测失败绝不影响推送本身）。

    ⚠️ 这个模块跑在 **API 进程内**，所以它的指标会出现在 API 的 `/metrics` 里；
    入站收消息在独立进程，指标另见 `channels/feishu/telemetry.py`。
    """
    try:
        from core.observability.metrics import record_feishu_push

        record_feishu_push(result)
    except Exception:  # noqa: BLE001
        pass


class FeishuPusher:
    """im.message.create 封装：tenant_access_token 缓存 + 发送文本消息。"""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        domain: str = "https://open.feishu.cn",
        http_client: Any = None,
        timeout: float = 15.0,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain.rstrip("/")
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self._timeout = timeout
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._token_expire_at: float = 0.0
        # 空 app_id/secret → no-op（不报错，本地未配凭据时可安全使用）
        self._enabled = bool(app_id and app_secret)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _fetch_token(self) -> Optional[str]:
        resp = self._http.post(
            f"{self._domain}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") not in (0, None):
            logger.warning("飞书 tenant_access_token 换取失败: code=%s msg=%s",
                           data.get("code"), data.get("msg"))
            return None
        return data.get("tenant_access_token") or data.get("data", {}).get(
            "tenant_access_token"
        )

    def _get_token(self) -> Optional[str]:
        with self._lock:
            if self._token and time.time() < self._token_expire_at:
                return self._token
            token = self._fetch_token()
            if token:
                self._token = token
                # 默认 7200s，留 buffer 提前刷
                self._token_expire_at = time.time() + 7200 - _TOKEN_TTL_BUFFER_S
            return token

    def send_text(self, open_id: str, text: str) -> bool:
        """向单个 open_id 发私聊文本消息；返回是否成功。"""
        if not self._enabled:
            logger.debug("飞书推送未启用（缺 app_id/secret），跳过 open_id=%s", open_id[:8])
            _record_push("disabled")
            return False
        token = self._get_token()
        if not token:
            _record_push("no_token")
            return False
        try:
            resp = self._http.post(
                f"{self._domain}/open-apis/im/v1/messages?receive_id_type=open_id",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": open_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") not in (0, None):
                logger.warning("飞书消息发送失败: code=%s msg=%s",
                               data.get("code"), data.get("msg"))
                _record_push("api_error")
                return False
            logger.info("飞书进度消息已推送 open_id=%s", open_id[:8])
            _record_push("ok")
            return True
        except Exception as exc:  # noqa: BLE001  推送失败不打断业务
            logger.warning("飞书推送异常（不影响业务）: %s", exc)
            _record_push("exception")
            return False


# ── 里程碑 → 文案 ─────────────────────────────────────────────────


def _preview(text: str, n: int = ANSWER_PREVIEW_CHARS) -> str:
    s = " ".join((text or "").split()).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def render_progress_text(payload: dict) -> Optional[str]:
    """事件 payload → 推送文案；不认识的事件返回 None。"""
    kind = payload.get("kind")
    file_name = str(payload.get("file_name") or "文件")
    if kind == "upload_started":
        return f"📄 开始解析「{file_name}」…\n完成后我会在这里提醒您。"
    if kind == "upload_succeeded":
        chunks = payload.get("chunk_count")
        detail = f"（{chunks} 块）" if chunks else ""
        return f"✅ 「{file_name}」解析完成{detail}。\n可回电脑查看结果。"
    if kind == "upload_failed":
        reason = str(payload.get("reason") or "未知原因")[:60]
        return f"❌ 「{file_name}」解析失败：{reason}"
    if kind == "chat_result":
        answer = _preview(str(payload.get("answer") or ""))
        return f"💬 回答就绪：{answer}\n完整内容请回电脑查看。"
    return None


# ── 事件 → 推送 ───────────────────────────────────────────────────


class ProgressNotifier:
    """订阅进度事件，绑定用户推送里程碑。emit 只做线程池提交，不阻塞业务线程。"""

    def __init__(
        self,
        pusher: FeishuPusher,
        *,
        lookup: Callable[[str], Optional[str]] = binding_lookup,
        max_workers: int = 2,
    ) -> None:
        self._pusher = pusher
        self._lookup = lookup
        self._max_workers = max(1, max_workers)
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="feishu-push"
        )
        self._subscribed = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._subscribed:
                return
            events.subscribe(self._handle)
            self._subscribed = True

    def stop(self) -> None:
        with self._lock:
            if self._subscribed:
                events.unsubscribe(self._handle)
                self._subscribed = False
        self._pool.shutdown(wait=False, cancel_futures=True)

    def flush(self) -> None:
        """等待已提交推送完成并重建线程池（测试/优雅停机用）。"""
        self._pool.shutdown(wait=True)
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="feishu-push"
        )

    def _handle(self, payload: dict) -> None:
        session_id = payload.get("session_id")
        if not session_id:
            return
        open_id = self._lookup(session_id)
        if not open_id:
            return  # 该会话未绑定飞书 → 不推
        self._pool.submit(self._push, open_id, payload)

    def _push(self, open_id: str, payload: dict) -> None:
        text = render_progress_text(payload)
        if not text:
            return
        try:
            self._pusher.send_text(open_id, text)
        except Exception:  # noqa: BLE001  推送失败不污染线程池
            logger.exception("飞书进度推送失败 payload.kind=%s", payload.get("kind"))


# ── 进程级单例 ────────────────────────────────────────────────────

_notifier: Optional[ProgressNotifier] = None
_notifier_lock = threading.Lock()


def get_progress_notifier() -> ProgressNotifier:
    """进程级进度推送器单例（懒加载；用 Settings 构造 FeishuPusher）。"""
    global _notifier
    if _notifier is None:
        with _notifier_lock:
            if _notifier is None:
                from core.config.settings import Settings

                s = Settings()
                pusher = FeishuPusher(
                    app_id=s.feishu_app_id,
                    app_secret=s.feishu_app_secret,
                    domain=s.feishu_domain,
                )
                _notifier = ProgressNotifier(
                    pusher, max_workers=s.feishu_push_max_workers
                )
                _notifier.start()
    return _notifier


def reset_notifier() -> None:
    """停掉并清空单例（测试用）。"""
    global _notifier
    with _notifier_lock:
        if _notifier is not None:
            _notifier.stop()
        _notifier = None


__all__ = ["FeishuPusher", "ProgressNotifier", "get_progress_notifier",
           "reset_notifier", "render_progress_text"]
