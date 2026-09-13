"""core.jobs — 轻量任务队列（W4-3 队列解耦）。

把耗时任务（如大 PDF 解析、图片 OCR）从同步 HTTP 请求里拆出来：
接口立即返回 task_id，后台 worker 线程执行，调用方轮询状态取结果。

**执行本地、状态共享**（B1）：任务仍在「提交到的那个 worker 进程」上执行
（闭包捕获的入参不可跨进程序列化），但**状态表可切 Redis**——
`JOB_QUEUE_BACKEND=redis` 时任一 worker 都能答出 `status(task_id)`。
进程内状态表在多 worker 下会让轮询落到没有该任务的进程 → 404
「任务不存在（或已过期）」（实测复现：1.6MB 上传 + 2 worker，首次轮询即 404）。

语义：
  - `TaskQueue.submit(fn, *args, **kwargs) -> task_id`：提交即返回，后台执行。
  - `TaskQueue.status(task_id) -> dict`：{status, error?, done, created_at, finished_at}
    status ∈ pending | processing | done | error；未知/过期返回 None。
  - **线程安全**（提交与 worker 并发更新任务表加锁）。
  - **worker 失败不崩**：异常捕获记到状态表的 error 字段，状态置 error。

worker 数经 `Settings.job_queue_workers`（默认 2）配置。任务完成后即从进程内
在飞表移除，释放闭包捕获的上传字节；状态按 `job_queue_ttl_seconds` 过期。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

PENDING = "pending"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"

#: 当前 worker 正在执行的任务 id（供任务回调读取自己的 task_id，如异步上传存 file_store）
_current_task_id: ContextVar[Optional[str]] = ContextVar("ztb_job_task_id", default=None)

#: 「未提供 result」哨兵（区别于 result=None 的合法结果）
_UNSET = object()


@dataclass
class Task:
    id: str
    fn: Callable[..., Any]
    args: tuple = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ── 状态表后端（B1：多 worker 共享）──────────────────────────
#
# 只存状态元数据（JSON 化），**不存 fn/args**——闭包捕获的上传字节不可跨进程
# 序列化，且执行本就发生在提交进程内。


class _MemoryStatusStore:
    """进程内状态表（默认；单 worker）。"""

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            rec = self._data.get(task_id)
            if rec is None:
                return None
            if time.time() - rec["created_at"] > self._ttl:
                self._data.pop(task_id, None)
                return None
            return rec

    def set(self, task_id: str, data: dict) -> None:
        with self._lock:
            self._data[task_id] = data

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class _RedisStatusStore:
    """Redis 状态表：多 worker / 多副本共享。

    值用 JSON（状态元数据都是基本类型，不必引入 pickle）；TTL 由 ``SET`` 的 ex
    保证。Redis 故障：读按未知、写跳过（日志留痕），服务不中断。
    """

    _PREFIX = "ztb:jobtask"

    def __init__(self, ttl: int, url: str, client: Optional[object] = None) -> None:
        self._ttl = ttl
        if client is not None:                      # 测试注入（fakeredis）
            self._client = client
        else:
            import redis as _redis

            self._client = _redis.Redis.from_url(url, decode_responses=False)
            self._client.ping()                     # 探测失败 → _build_status_store 降级

    def _key(self, task_id: str) -> str:
        return f"{self._PREFIX}:{task_id}"

    def get(self, task_id: str) -> Optional[dict]:
        try:
            raw = self._client.get(self._key(task_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("任务状态表 Redis 读取失败: %s", exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001  脏数据 → 删掉按未知
            logger.warning("任务状态表反序列化失败，删除脏 key: %s", task_id[:12])
            try:
                self._client.delete(self._key(task_id))
            except Exception:  # noqa: BLE001
                pass
            return None

    def set(self, task_id: str, data: dict) -> None:
        try:
            self._client.set(
                self._key(task_id),
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
                ex=self._ttl,
            )
        except Exception as exc:  # noqa: BLE001  Redis 故障不阻断任务
            logger.warning("任务状态表 Redis 写入失败: %s", exc)

    def clear(self) -> None:
        try:
            for rk in self._client.scan_iter(match=f"{self._PREFIX}:*"):
                self._client.delete(rk)
        except Exception as exc:  # noqa: BLE001
            logger.warning("任务状态表 Redis 清空失败: %s", exc)


def _build_status_store(ttl: int, backend: str, url: str):
    """按配置构造状态表；Redis 不可达则降级为进程内（fail-open）。"""
    if (backend or "memory").lower() == "redis":
        try:
            store = _RedisStatusStore(ttl, url)
            probe = f"__probe__{uuid.uuid4().hex}"
            store.set(probe, {"status": PENDING, "created_at": time.time()})
            if store.get(probe) is None:        # 写入没回读 → 视为不可用
                raise RuntimeError("Redis 探测未回读")
            return store
        except Exception as exc:  # noqa: BLE001
            logger.warning("任务状态表 Redis 后端不可用（%s），降级为进程内后端", exc)
    return _MemoryStatusStore(ttl)


class TaskQueue:
    """轻量任务队列（后台 worker 线程 + 可切换的状态表）。"""

    def __init__(self, max_workers: int = 2, status_store: Optional[Any] = None) -> None:
        self._max_workers = max(1, int(max_workers))
        self._executor: Optional[ThreadPoolExecutor] = None
        self._tasks: Dict[str, Task] = {}       # 仅在飞任务（完成即移除）
        self._lock = threading.Lock()
        self._status = status_store if status_store is not None else _default_status_store()

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            with self._lock:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix="ztb-job",
                    )
        return self._executor

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """提交任务，立即返回 task_id。"""
        task = Task(id=uuid.uuid4().hex, fn=fn, args=args, kwargs=kwargs)
        with self._lock:
            self._tasks[task.id] = task
        self._write(task, status=PENDING)
        try:
            self._ensure_executor().submit(self._run, task)
        except Exception as exc:  # noqa: BLE001  提交失败 → 任务立即置 error
            self._finish(task, error=f"submit_failed: {type(exc).__name__}: {exc}")
        return task.id

    def _run(self, task: Task) -> None:
        self._write(task, status=PROCESSING)
        token = _current_task_id.set(task.id)
        try:
            result = task.fn(*task.args, **task.kwargs)
        except Exception as exc:  # noqa: BLE001  任务失败 → error（不崩 worker）
            logger.warning("任务 %s 失败: %s", task.id[:8], exc)
            self._finish(task, error=f"{type(exc).__name__}: {exc}")
            return
        finally:
            _current_task_id.reset(token)
        self._finish(task, result=result)

    def _write(
        self,
        task: Task,
        *,
        status: str,
        error: Optional[str] = None,
        finished_at: Optional[float] = None,
        result: Any = _UNSET,
    ) -> None:
        data: Dict[str, Any] = {
            "status": status,
            "error": error,
            "created_at": task.created_at,
            "finished_at": finished_at,
        }
        if result is not _UNSET:
            # 结果只在可 JSON 化时落表（跨进程状态表不承载任意对象）
            try:
                json.dumps(result)
                data["result"] = result
            except (TypeError, ValueError):
                logger.warning("任务 %s 的结果不可 JSON 化，仅保留状态", task.id[:8])
        self._status.set(task.id, data)

    def _finish(
        self, task: Task, *, result: Any = None, error: Optional[str] = None
    ) -> None:
        self._write(
            task, status=ERROR if error else DONE, error=error,
            finished_at=time.time(), result=result,
        )
        with self._lock:
            self._tasks.pop(task.id, None)      # 释放 fn/args（含上传文件字节）

    def status(self, task_id: str) -> Optional[dict]:
        rec = self._status.get(task_id)
        if rec is None:
            return None
        return {
            "task_id": task_id,
            "status": rec.get("status"),
            "error": rec.get("error"),
            "done": rec.get("status") == DONE,
            "created_at": rec.get("created_at"),
            "finished_at": rec.get("finished_at"),
        }

    def result(self, task_id: str) -> Any:
        """任务完成结果（未完成/失败/结果不可序列化返回 None）。

        跨进程状态表只承载可 JSON 化的结果；失败原因见 ``status().error``。
        """
        rec = self._status.get(task_id)
        if rec is None or rec.get("status") != DONE:
            return None
        return rec.get("result")

    def pending_count(self) -> int:
        """在飞任务数（本进程；进程内表只留未完成的）。"""
        with self._lock:
            return len(self._tasks)

    def clear_status(self) -> None:
        """清空状态表（测试 / 运维用）。"""
        self._status.clear()

    def shutdown(self) -> None:
        with self._lock:
            ex = self._executor
            self._executor = None
        if ex is not None:
            ex.shutdown(wait=False, cancel_futures=True)


#: 进程级单例
_queue: Optional[TaskQueue] = None
_queue_lock = threading.Lock()


def _default_status_store():
    from core.config.settings import Settings

    s = Settings()
    return _build_status_store(
        int(getattr(s, "job_queue_ttl_seconds", 3600) or 3600),
        getattr(s, "job_queue_backend", "memory"),
        getattr(s, "job_queue_redis_url", "") or "redis://localhost:6379/0",
    )


def get_queue() -> TaskQueue:
    """进程级任务队列单例（懒加载；worker 数与状态表后端取 Settings）。"""
    global _queue
    if _queue is None:
        with _queue_lock:
            if _queue is None:
                from core.config.settings import Settings

                _queue = TaskQueue(max_workers=Settings().job_queue_workers)
    return _queue


def reset_queue() -> None:
    """清空单例（测试用）。"""
    global _queue
    with _queue_lock:
        if _queue is not None:
            _queue.clear_status()
            _queue.shutdown()
        _queue = None


__all__ = ["TaskQueue", "Task", "get_queue", "reset_queue",
           "_build_status_store", "_current_task_id",
           "PENDING", "PROCESSING", "DONE", "ERROR"]
