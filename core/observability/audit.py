"""操作审计（W3-0）— 谁在何时调用了什么。

输出：独立 logger `audit`，每条记录一行 JSON（`%(message)s` 格式，不与其他日志混排）。
  - 默认输出到 stderr（与应用日志同屏）；
  - 配置 `AUDIT_LOG_PATH` 后**同时**追加写入该文件（便于长期留存/合规取证）。

字段：ts / action / status / request_id / api_key_id（Key 指纹）/ session_id /
latency_ms / error_code / question（截断，可用 `AUDIT_LOG_QUESTIONS=false` 关闭）。

注意：`question` 为用户输入，可能含企业名等个人信息；默认记录但截断 120 字符，
如需严格合规请关闭该字段。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from core.observability.context import get_api_key_id, get_request_id, get_session_id

logger = logging.getLogger("audit")

_QUESTION_MAX_CHARS = 120
_lock = threading.Lock()
_configured_path: Optional[str] = None


def _ensure_configured(path: Optional[str] = None) -> None:
    """惰性配置 audit logger（只配一次；path 变化时补挂文件 handler）。"""
    global _configured_path

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

    if path and path != _configured_path:
        with _lock:
            if path != _configured_path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(path, encoding="utf-8")
                file_handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(file_handler)
                _configured_path = path
                # 用模块 logger 记录（audit logger 只写 JSON 记录，避免混入提示行）
                logging.getLogger(__name__).info("审计日志落盘: %s", path)


def _settings():
    from core.config.settings import Settings

    return Settings()


def record_audit(
    *,
    action: str,
    status: str = "ok",
    session_id: Optional[str] = None,
    question: Optional[str] = None,
    latency_ms: Optional[int] = None,
    error_code: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """写入一条审计记录（action：chat / chat_stream / upload / ...）。"""
    settings = _settings()
    _ensure_configured(settings.audit_log_path or None)

    payload: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "action": action,
        "status": status,
        "request_id": get_request_id() or "-",
        "api_key_id": get_api_key_id() or "-",
        "session_id": session_id or get_session_id() or "-",
    }
    if latency_ms is not None:
        payload["latency_ms"] = latency_ms
    if error_code:
        payload["error_code"] = error_code
    if question and settings.audit_log_questions:
        payload["question"] = question[:_QUESTION_MAX_CHARS]
    if extra:
        payload.update(extra)

    logger.info(json.dumps(payload, ensure_ascii=False, default=str))


def reset_audit() -> None:
    """移除所有 handler（测试用）。"""
    global _configured_path
    with _lock:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        _configured_path = None


__all__ = ["record_audit", "reset_audit"]
