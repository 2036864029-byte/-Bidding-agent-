# 功能：CPU 密集任务的独立进程池（避免与请求链路抢 GIL）。
"""CPU 密集任务的独立进程执行。

**为什么需要**（实测）：PDF 解析是 CPU / GIL 密集（PyMuPDF + 段落化/分块逻辑）。
跑在应用进程内时，5 个并发上传会把同期提问的延迟 p95 从 **3.41s 拉到 6.29s（+84%）**
—— 请求链路本身是 IO 密集（LLM/检索都是远端），却被解析抢走 GIL 时间片。
把解析放到独立进程后，主进程的 GIL 不再被解析占用。

**边界**：只用于「纯计算、输入输出都可序列化」的任务。任何需要访问应用进程内状态
（file_store / 会话 / 图实例）的逻辑**必须留在主进程**——本模块只跑解析、把结果
（dict）带回来，落库仍由主进程做。

⚠️ **平台差异（重要）**：
  - Linux：默认 `fork` → 子进程直接继承父进程内存，**无重导入开销**（部署环境即此）。
  - Windows：只有 `spawn` → 子进程会**重新 import 父进程的 `__main__`**（即
    `api.server`，含模块级 `app = create_app()`），每个子进程一次性付 ~2-5s 建图开销。
    本地开发可接受；也可设 `CPU_POOL_ENABLED=false` 退回主进程执行。

worker 函数必须是**模块级顶层函数**（可 pickle），且本模块保持轻量——重型依赖
在函数体内惰性导入，避免子进程 import 本模块时被牵连。
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Optional

logger = logging.getLogger(__name__)

_pool: Optional[ProcessPoolExecutor] = None


def enabled() -> bool:
    return os.getenv("CPU_POOL_ENABLED", "true").lower() in {"1", "true", "yes"}


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        workers = max(1, int(os.getenv("CPU_POOL_WORKERS", "2")))
        _pool = ProcessPoolExecutor(max_workers=workers)
        logger.info("CPU_POOL 初始化: max_workers=%d", workers)
    return _pool


def run_cpu_task(fn: Any, *args: Any) -> Any:
    """把 CPU 密集函数提交到独立进程执行并等待结果。

    `CPU_POOL_ENABLED=false` 时退回**当前线程同步执行**（本地调试/排查用）。
    """
    if not enabled():
        return fn(*args)
    return _get_pool().submit(fn, *args).result()


def reset_cpu_pool() -> None:
    """关闭池（测试/关停用）。"""
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


# ── 供提交的顶层 worker（必须模块级，才能被 pickle 到子进程）────────

def parse_file_worker(data: bytes, name: str) -> dict:
    """在独立进程里解析上传文件，返回**可序列化**的结果。

    只做解析、不碰任何进程内状态（file_store 等由主进程负责落库）。
    """
    from public_kb.ingestion.parse_tool import parse_file

    r = parse_file(data, source_name=name)
    return {
        "source_name": r.source_name,
        "source_format": r.source_format,
        "markdown": r.markdown,
        "preview": r.preview,
        "chunk_count": r.chunk_count,
    }


__all__ = [
    "enabled", "run_cpu_task", "reset_cpu_pool", "parse_file_worker",
]
