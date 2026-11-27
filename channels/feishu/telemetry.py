"""channels.feishu.telemetry — 飞书通道的心跳与指标（入站进程内）。

**为什么需要**：飞书通道是独立进程、靠一条出站长连接收消息。它挂掉时**没有任何人知道**
——用户发消息没反应，而 API 的 `/metrics` 与 `/health` 完全正常（因为它根本不在这进程里）。
本模块补三件事：

1. `Heartbeat`：每 N 秒把 `{state, pid, updated_at, last_event_at, last_reply_at}`
   原子写进一个 JSON 文件。**判活看 `updated_at` 是否新鲜**（而不是看进程存在），
   这样"进程还在但长连接僵死"也能被发现。
2. 进程内 Prometheus 指标（`feishu_*`）：消息结果计数、处理耗时、最后事件时间。
   独立进程的指标不会被 API 的 `/metrics` 抓到，所以可选地在本进程起一个抓取端口
   （`FEISHU_METRICS_PORT`，0 = 关）。
3. `python -m channels.feishu.telemetry --check <path>`：给容器健康检查用的退出码。

设计约束：
- 心跳写入必须**原子**（临时文件 + replace），否则健康检查可能读到半个 JSON。
- 任何遥测失败都**不能影响收发消息**：全部 try/except 吞掉并降级为日志。
"""

from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 默认心跳文件（容器里由 compose 覆盖为 /app/DATA/feishu_heartbeat.json）
DEFAULT_HEARTBEAT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "DATA",
    "feishu_heartbeat.json",
)
DEFAULT_INTERVAL_S = 10.0
DEFAULT_MAX_AGE_S = 45.0     # 45s 没更新即判定僵死（≥ 4 个心跳周期）

#: 视为"不健康"的状态；`disconnected` 不算——SDK 自带重连，短暂断连是正常态
UNHEALTHY_STATES = {"stopped", "failed"}


# ══════════════════════════════════════════════════════════
# 心跳文件
# ══════════════════════════════════════════════════════════


class Heartbeat:
    """把进程状态周期性落到 JSON 文件（原子写）。"""

    def __init__(self, path: Optional[str] = None, *, interval_s: float = DEFAULT_INTERVAL_S):
        self.path = Path(path or os.getenv("FEISHU_HEARTBEAT_PATH") or DEFAULT_HEARTBEAT_PATH)
        self.interval_s = max(1.0, float(interval_s))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._data: dict[str, Any] = {
            "state": "starting",
            "pid": os.getpid(),
            "started_at": time.time(),
            "updated_at": time.time(),
            "last_event_at": None,
            "last_reply_at": None,
            "events": 0,
            "replies": 0,
            "note": "",
        }

    # ── 状态更新（异步信号安全：只改内存 + 打标脏）──────────────

    def set_state(self, state: str, note: str = "") -> None:
        with self._lock:
            self._data["state"] = state
            if note:
                self._data["note"] = note

    def touch_event(self) -> None:
        with self._lock:
            self._data["last_event_at"] = time.time()
            self._data["events"] = int(self._data.get("events") or 0) + 1

    def touch_reply(self) -> None:
        with self._lock:
            self._data["last_reply_at"] = time.time()
            self._data["replies"] = int(self._data.get("replies") or 0) + 1

    # ── 落盘 ────────────────────────────────────────────────

    def write(self) -> None:
        """原子写一次快照。失败只记日志，绝不抛出。"""
        try:
            with self._lock:
                snapshot = dict(self._data)
            snapshot["updated_at"] = time.time()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)      # 同目录 rename 是原子的
        except Exception as exc:  # noqa: BLE001  遥测不能影响主流程
            logger.debug("心跳写入失败（忽略）: %s", exc)

    def start(self) -> None:
        """起后台线程周期写盘（幂等）。"""
        if self._thread is not None:
            return
        self.write()

        def _loop() -> None:
            while not self._stop.wait(self.interval_s):
                self.write()

        self._thread = threading.Thread(target=_loop, name="feishu-heartbeat", daemon=True)
        self._thread.start()

    def stop(self, *, final_state: str = "stopped") -> None:
        self.set_state(final_state)
        self._stop.set()
        self.write()


#: 进程内单例：入口与 handler 共用同一份状态（handler 只 `touch_event`）
_heartbeat: Optional[Heartbeat] = None
_heartbeat_lock = threading.Lock()


def get_heartbeat() -> Heartbeat:
    """取进程内心跳单例（懒创建，路径读 ENV）。"""
    global _heartbeat
    with _heartbeat_lock:
        if _heartbeat is None:
            _heartbeat = Heartbeat()
        return _heartbeat


def set_heartbeat(hb: Optional[Heartbeat]) -> None:
    """注入/重置单例（测试用；传 None 则下次懒创建）。"""
    global _heartbeat
    with _heartbeat_lock:
        _heartbeat = hb


def read_heartbeat(path: Optional[str] = None) -> Optional[dict]:
    """读心跳快照；文件不存在/坏 JSON 返回 None（不抛）。"""
    p = Path(path or os.getenv("FEISHU_HEARTBEAT_PATH") or DEFAULT_HEARTBEAT_PATH)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001
        return None


def check_heartbeat(
    path: Optional[str] = None, *, max_age_s: float = DEFAULT_MAX_AGE_S, now: Optional[float] = None
) -> tuple[bool, str]:
    """健康判定：(ok, 原因)。

    判据顺序：文件存在 → 可解析 → 状态不是 stopped/failed → `updated_at` 未过期。
    特意**不**把 `disconnected` 判为不健康：SDK 自己会重连，短暂断连不该触发重启风暴。
    """
    data = read_heartbeat(path)
    if data is None:
        return False, "心跳文件缺失或不可解析（进程可能没起）"

    state = str(data.get("state") or "")
    if state in UNHEALTHY_STATES:
        return False, f"状态={state}（{data.get('note') or '无说明'}）"

    now = time.time() if now is None else now
    try:
        age = now - float(data.get("updated_at") or 0)
    except (TypeError, ValueError):
        return False, "updated_at 非法"
    if age > max_age_s:
        return False, f"心跳过期 {age:.0f}s > {max_age_s:.0f}s"
    return True, f"state={state} age={age:.0f}s events={data.get('events')}"


# ══════════════════════════════════════════════════════════
# 进程内指标（bot 进程独享；API 的 /metrics 抓不到）
# ══════════════════════════════════════════════════════════

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    MESSAGES_TOTAL = Counter(
        "feishu_messages_total",
        "飞书入站消息处理结果",
        ["result"],   # ok | backend_error | reply_failed | ignored | duplicate | error
    )
    REPLY_TOTAL = Counter(
        "feishu_reply_total",
        "飞书回复发送结果",
        ["result"],   # ok | failed
    )
    HANDLE_SECONDS = Histogram(
        "feishu_handle_seconds",
        "单条消息从收到到回复完成的耗时（秒）",
    )
    LAST_EVENT_TS = Gauge(
        "feishu_last_event_timestamp",
        "最后一条入站消息的时间戳（epoch 秒）",
    )
    # 预置 0 值序列：Prometheus 的带标签 Counter 在首次 .labels() 之前**不会出现在
    # 抓取结果里**，那样面板上就看不到"通道一条消息都没收到"（缺序列 = 隐藏故障）。
    for _r in ("ok", "ignored", "duplicate", "backend_error", "reply_failed", "error"):
        MESSAGES_TOTAL.labels(result=_r)
    for _r in ("ok", "failed"):
        REPLY_TOTAL.labels(result=_r)
    _METRICS_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001  prometheus_client 缺失也不该拖垮通道
    logger.warning("prometheus_client 不可用，飞书指标关闭: %s", _exc)
    _METRICS_AVAILABLE = False


def record_message(result: str, seconds: Optional[float] = None) -> None:
    """记一条入站消息的处理结果（永不抛出）。"""
    if not _METRICS_AVAILABLE:
        return
    try:
        MESSAGES_TOTAL.labels(result=result).inc()
        LAST_EVENT_TS.set(time.time())
        if seconds is not None:
            HANDLE_SECONDS.observe(max(0.0, seconds))
    except Exception:  # noqa: BLE001
        pass


def record_reply(ok: bool) -> None:
    if not _METRICS_AVAILABLE:
        return
    try:
        REPLY_TOTAL.labels(result="ok" if ok else "failed").inc()
    except Exception:  # noqa: BLE001
        pass


def start_metrics_server(port: Optional[int] = None) -> Optional[int]:
    """起 Prometheus 抓取端口；`port<=0` 或失败则返回 None（不影响主流程）。"""
    if not _METRICS_AVAILABLE:
        return None
    try:
        p = int(os.getenv("FEISHU_METRICS_PORT", "9100")) if port is None else int(port)
    except ValueError:
        p = 9100
    if p <= 0:
        logger.info("FEISHU_METRICS_PORT<=0，飞书指标端口不启动")
        return None
    try:
        start_http_server(p)
        logger.info("飞书指标端口已启动: :%d/metrics", p)
        return p
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", -1)):
            logger.warning("飞书指标端口 %d 已被占用，跳过（不影响通道）", p)
        else:
            logger.warning("飞书指标端口启动失败（忽略）: %s", exc)
        return None


# ══════════════════════════════════════════════════════════
# 健康检查 CLI（给容器用）
# ══════════════════════════════════════════════════════════


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="飞书通道健康检查")
    ap.add_argument("--check", metavar="PATH", help="心跳文件路径")
    ap.add_argument(
        "--max-age", type=float, default=DEFAULT_MAX_AGE_S, help="心跳最大允许年龄（秒）"
    )
    args = ap.parse_args(argv)

    path = args.check or os.getenv("FEISHU_HEARTBEAT_PATH") or DEFAULT_HEARTBEAT_PATH
    ok, reason = check_heartbeat(path, max_age_s=args.max_age)
    print(("OK " if ok else "FAIL ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
