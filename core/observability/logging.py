# 功能：日志配置单一入口（W0-7 建；W2-2 增 JSON 格式与 request_id 注入）。
"""日志配置 —— 服务入口与 CLI 共用。

背景：
  - W0-7：`api/server.py` 只调 `uvicorn.run(...)`，未配置根 logger → 服务态下
    应用 INFO 埋点不落盘。
  - W2-2：增加 JSON 结构化格式与 request_id / session_id 注入，日志可按请求串联。

约定：
  - 只配置根 logger（幂等；已有 handler 时只对齐级别，不重复添加）。
  - uvicorn 以 `log_config=None` 启动，其日志经传播走同一出口，口径一致。
  - 级别：`LOG_LEVEL` env > 显式参数 > verbose 推导（默认 INFO）。
  - 格式：`LOG_FORMAT` env = "json" 走 JSON，否则文本（默认）。
"""

from __future__ import annotations

import json
import logging
import os

from .context import MISSING, get_request_id, get_session_id, get_tenant_id

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%H:%M:%S"

_LEVEL_NAMES = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def resolve_level(verbose: bool = False, level: str | None = None) -> str:
    """解析日志级别：显式参数 > 环境变量 LOG_LEVEL > verbose 推导。"""
    candidate = (level or os.getenv("LOG_LEVEL") or "").upper()
    if candidate in _LEVEL_NAMES:
        return candidate
    return "DEBUG" if verbose else "INFO"


def resolve_json(json_output: bool | None = None) -> bool:
    """是否输出 JSON：显式参数 > LOG_FORMAT=json > 默认文本。"""
    if json_output is not None:
        return json_output
    return (os.getenv("LOG_FORMAT", "text").strip().lower() == "json")


class RequestContextFilter(logging.Filter):
    """把 request_id / session_id / tenant_id 注入 LogRecord（供 formatter 取用）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or MISSING
        record.session_id = get_session_id() or MISSING
        record.tenant_id = get_tenant_id()
        return True


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志（可被采集器直接解析）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", MISSING),
            "session_id": getattr(record, "session_id", MISSING),
            "tenant_id": getattr(record, "tenant_id", MISSING),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _build_formatter(json_output: bool) -> logging.Formatter:
    if json_output:
        return JsonFormatter()
    return logging.Formatter(DEFAULT_FORMAT, datefmt=DEFAULT_DATE_FORMAT)


def setup_logging(
    verbose: bool = False,
    *,
    level: str | None = None,
    json_output: bool | None = None,
) -> None:
    """配置根 logger（幂等）。

    Args:
        verbose: True 时默认级别为 DEBUG（可被 level / LOG_LEVEL 覆盖）。
        level: 显式级别名（如 "DEBUG"）；非法值忽略并回退。
        json_output: 是否 JSON 格式；None 时读 LOG_FORMAT。
    """
    resolved = resolve_level(verbose, level)
    formatter = _build_formatter(resolve_json(json_output))

    root = logging.getLogger()
    if root.handlers:
        # 已被配置（如 uvicorn 以自带 log_config 启动）→ 对齐级别与格式，不重复加 handler
        root.setLevel(resolved)
        for handler in root.handlers:
            handler.setFormatter(formatter)
            if not any(isinstance(f, RequestContextFilter) for f in handler.filters):
                handler.addFilter(RequestContextFilter())
        return

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(RequestContextFilter())
    root.addHandler(handler)
    root.setLevel(resolved)


def reset_logging() -> None:
    """清空根 logger 的 handler（测试用）。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


__all__ = [
    "DEFAULT_FORMAT",
    "DEFAULT_DATE_FORMAT",
    "JsonFormatter",
    "RequestContextFilter",
    "resolve_level",
    "resolve_json",
    "setup_logging",
    "reset_logging",
]
