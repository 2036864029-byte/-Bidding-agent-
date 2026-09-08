# 功能：日志配置单一入口（Wave 0 / W0-7；W2-2 扩展为 JSON + request_id）。
"""日志配置 —— 服务入口与 CLI 共用，避免"应用 INFO 日志在服务态不落盘"。

背景（代码审计 W0-7）：`api/server.py` 的 `main()` 只调 `uvicorn.run(app, host, port)`，
未传 `log_config` 也未配置根 logger。uvicorn 默认日志配置只处理 `uvicorn.*` 三个
logger，根 logger 无 handler → 全仓 `logger.info(...)` 埋点（分支耗时、SQL profile、
检索埋点）在服务态下丢失。

约定：
  - 只配置根 logger（幂等；已有 handler 时不重复添加）。
  - uvicorn 以 `log_config=None` 启动，其自身日志经传播走同一出口，口径一致。
  - 级别可用环境变量 `LOG_LEVEL` 覆盖（默认 INFO，`verbose=True` 时 DEBUG）。
"""

from __future__ import annotations

import logging
import os

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%H:%M:%S"

_LEVEL_NAMES = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def resolve_level(verbose: bool = False, level: str | None = None) -> str:
    """解析日志级别：显式参数 > 环境变量 LOG_LEVEL > verbose 推导。"""
    candidate = (level or os.getenv("LOG_LEVEL") or "").upper()
    if candidate in _LEVEL_NAMES:
        return candidate
    return "DEBUG" if verbose else "INFO"


def setup_logging(verbose: bool = False, *, level: str | None = None) -> None:
    """配置根 logger（幂等）。

    Args:
        verbose: True 时默认级别为 DEBUG（可被 level / LOG_LEVEL 覆盖）。
        level: 显式级别名（如 "DEBUG"）；非法值忽略并回退。
    """
    resolved = resolve_level(verbose, level)
    root = logging.getLogger()
    if root.handlers:
        # 已被配置（如 uvicorn 以自带 log_config 启动）→ 只对齐级别，不重复加 handler
        root.setLevel(resolved)
        return
    logging.basicConfig(
        level=resolved,
        format=DEFAULT_FORMAT,
        datefmt=DEFAULT_DATE_FORMAT,
    )


def reset_logging() -> None:
    """清空根 logger 的 handler（测试用）。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
