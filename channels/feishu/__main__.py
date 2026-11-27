"""python -m channels.feishu —— 飞书机器人问答常驻进程入口（主线 MVP）。

用法：``FEISHU_ENABLED=true .venv/Scripts/python.exe -m channels.feishu``

启动自检：app_id/secret 已配 + 总开关开启；随后建立 HTTP 客户端与长连接。
长连接为阻塞式 ``ws.start()``（自带断线重连），Ctrl+C 退出。

**可观测性（2026-11）**：本进程不再"哑跑"——
  - 周期性写心跳文件（`FEISHU_HEARTBEAT_PATH`），容器健康检查读它判活；
  - 可选暴露 Prometheus 抓取端口（`FEISHU_METRICS_PORT`，0 = 关）；
  - 把 lark SDK 的连接日志翻译成心跳里的 `state`（SDK 不提供连接回调）。
详见 `channels/feishu/telemetry.py`。
"""

from __future__ import annotations

import logging
import sys

from core.config.settings import Settings
from core.net import ensure_no_proxy_for_loopback
from core.observability.logging import setup_logging

from . import telemetry
from .backend import BackendClient
from .client import build_event_handler, build_http_client, build_ws_client
from .handler import FeishuBot

logger = logging.getLogger(__name__)


class _ConnectionStateHandler(logging.Handler):
    """把 lark SDK 的连接日志翻译成心跳的 `state`。

    SDK 只打日志、没有连接回调（`lark_oapi.ws.Client` 连 `stop()` 都没有），
    所以这里做一层最小解释器：连上 → connected，断开/重连 → disconnected。
    认不出时保持原状态（健康检查只要求心跳新鲜，不会因此误判）。
    """

    def __init__(self, heartbeat: telemetry.Heartbeat) -> None:
        super().__init__(level=logging.INFO)
        self._hb = heartbeat

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage().lower()
        except Exception:  # noqa: BLE001
            return
        try:
            if "connected to wss" in msg:
                self._hb.set_state("connected")
            elif "disconnect" in msg or "reconnect" in msg:
                self._hb.set_state("disconnected", note=msg[:120])
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    setup_logging(verbose=False, level="INFO")
    s = Settings()
    if not (s.feishu_app_id and s.feishu_app_secret):
        print("飞书未配置（缺 FEISHU_APP_ID/SECRET），退出", file=sys.stderr)
        return 2
    if not s.feishu_enabled:
        print("FEISHU_ENABLED=false，飞书通道未启用（设 true 后重跑）", file=sys.stderr)
        return 2

    # 环回地址直连（调本地后端 /chat 时绕开系统代理）。
    ensure_no_proxy_for_loopback()

    # ── 遥测：心跳 + 指标端口（失败都不影响通道）──
    hb = telemetry.get_heartbeat()
    hb.set_state("connecting")
    hb.start()
    metrics_port = telemetry.start_metrics_server()
    # 挂 root：lark-oapi 把自己的前缀（"[Lark] [时间] [INFO]"）拼进 message 后打到 root，
    # 只挂 `logging.getLogger("Lark")` 收不到（实测 state 一直停在 connecting）。
    conn_handler = _ConnectionStateHandler(hb)
    logging.getLogger().addHandler(conn_handler)

    print(f"飞书通道心跳文件: {hb.path}（每 {hb.interval_s:.0f}s 刷新）")
    if metrics_port:
        print(f"飞书指标端口: http://127.0.0.1:{metrics_port}/metrics")

    lark_client = build_http_client(s.feishu_app_id, s.feishu_app_secret, s.feishu_domain)
    backend = BackendClient(
        s.feishu_backend_base_url,
        api_key=s.feishu_backend_api_key,
        timeout=s.feishu_backend_timeout_s,
    )
    bot = FeishuBot(lark_client, backend, max_workers=s.feishu_max_concurrency)
    event_handler = build_event_handler(bot.on_message)
    ws = build_ws_client(s.feishu_app_id, s.feishu_app_secret, event_handler, s.feishu_domain)

    print(f"飞书通道已启动：长连接 {s.feishu_domain}（Ctrl+C 退出）")
    try:
        ws.start()  # 阻塞；自带断线重连
    except KeyboardInterrupt:
        print("收到中断，退出")
    except Exception as exc:  # noqa: BLE001  让容器日志明确写出死因
        logger.exception("飞书长连接异常退出")
        hb.set_state("failed", note=str(exc)[:200])
        hb.write()      # 立刻落盘，健康检查据此判 unhealthy
        return 1
    finally:
        bot.stop()
    hb.stop()           # 正常退出
    return 0


if __name__ == "__main__":
    sys.exit(main())
