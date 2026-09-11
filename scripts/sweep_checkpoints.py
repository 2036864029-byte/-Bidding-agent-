"""检查点过期清理（W4-4）— 多副本部署下的定时任务。

背景：`SessionRegistry` 是**进程内**登记表，多副本时每个副本只见自己处理过的会话
→ 只被已下线副本处理过的会话检查点永远不会被清理。本脚本从**检查点源头**枚举线程
（任一副本都能看到全部），适合以 cron / k8s CronJob 周期执行。

用法：
    python scripts/sweep_checkpoints.py                 # TTL 取 settings.session_ttl_seconds
    python scripts/sweep_checkpoints.py --ttl 3600
    python scripts/sweep_checkpoints.py --dry-run       # 只列出将删的 thread
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.checkpointer import create_checkpointer, list_threads, sweep_expired_threads  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="检查点过期清理")
    parser.add_argument("--ttl", type=int, default=0, help="TTL 秒（0=取 settings.session_ttl_seconds）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from core.config.settings import Settings

    s = Settings()
    backend = os.getenv("CHECKPOINTER_BACKEND", s.checkpointer_backend)
    ttl = args.ttl or s.session_ttl_seconds
    if ttl <= 0:
        raise SystemExit("TTL<=0（会话不过期），无需清理")

    checkpointer = create_checkpointer(backend)
    threads = list_threads(checkpointer)
    print(f"后端={backend} TTL={ttl}s 现有 thread {len(threads)} 个")

    if args.dry_run:
        import time as _t

        now = _t.time()
        stale = [tid for tid, ts in threads.items() if now - ts > ttl]
        print(f"[dry] 将删除 {len(stale)} 个过期 thread: {stale[:10]}")
        return

    expired = sweep_expired_threads(checkpointer, ttl)
    print(f"已删除 {len(expired)} 个过期 thread")


if __name__ == "__main__":
    main()
