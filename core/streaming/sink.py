"""core.streaming — Token 级流式通道（W3-2）。

问题：langgraph 的 ``stream_mode="messages"`` 只在**当前图运行**内捕获模型 token，
**不跨编译子图边界**（实测：父节点内 ``subgraph.invoke()`` 或直接 add_node 编译子图，
子图内部模型 token 均不会出现在父图 messages 流）——而 supervisor 默认模式的
knowledge/price 都是编译子图，token 流式在默认链路不可用。

做法：contextvar + 线程安全队列桥接。
  - 生成节点把链路的 ``.invoke()`` 改为 ``.stream()``：每收到一个 token 就经
    ``emit_token()`` 写入当前请求的 ``StreamSink``（contextvar 读取）；sink 缺省时
    退化为纯累积——非流式路径（``/chat`` invoke、CLI、离线单测）零开销、零行为变化。
  - SSE 端点创建 ``StreamSink`` 并置入 contextvar，图在独立 daemon 线程运行；
    **注意**：本 Python 环境的 ``threading.Thread`` 并不自动拷贝调用方 contextvars
    （实测），故端点显式 ``contextvars.copy_context().run(...)`` 把当前上下文
    （含 sink、request_id、session_id）带入图线程。端点侧从队列逐帧转发为 SSE event。
  - 跨 supervisor/single、跨编译子图统一成立；节点契约 ``(state) -> dict`` 不变，
    AgentState 不加字段（架构硬约束）。

约定：队列元素一律为 ``(kind, payload)`` 二元组，kind ∈ {token, result, error, done}。
"""

from __future__ import annotations

import queue
from contextvars import ContextVar
from typing import Optional

_ACTIVE: ContextVar[Optional["StreamSink"]] = ContextVar(
    "ztb_stream_sink", default=None
)

# 事件种类
KIND_TOKEN = "token"    # 生成节点的增量 token（payload=str）
KIND_RESULT = "result"  # 终态结构化响应（payload=dict，与 POST /chat 同构）
KIND_ERROR = "error"    # 图执行异常统一信封（payload=dict）
KIND_DONE = "done"      # 流结束（payload=None）

# SSE 端点轮询超时（秒）：客户端断开后至多等待该时长感知 done
POLL_TIMEOUT = 0.25


class StreamSink:
    """线程安全事件队列：生成节点写 token，图运行线程写终态，SSE 端点读。

    端点侧消费方只应使用 :meth:`get`；生产侧使用 :meth:`put_token` /
    :meth:`put_event`（跨线程安全）。
    """

    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple[str, object]]" = queue.Queue()

    def put_token(self, text: str) -> None:
        """写入一个增量 token（生成节点每收到一个 chunk 调用一次）。"""
        self._queue.put((KIND_TOKEN, text))

    def put_event(self, kind: str, payload: object) -> None:
        """写入一个控制事件（result / error / done）。"""
        self._queue.put((kind, payload))

    def get(self, timeout: float = POLL_TIMEOUT):
        """取下一个事件 ``(kind, payload)``；超时返回 ``None``（不抛 queue.Empty）。"""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None


def set_active_sink(sink: Optional[StreamSink]) -> object:
    """把 sink 置入当前上下文（SSE 端点调用；工作线程经 context 拷贝继承）。"""
    return _ACTIVE.set(sink)


def get_active_sink() -> Optional[StreamSink]:
    return _ACTIVE.get()


def reset_active_sink(token: object) -> None:
    _ACTIVE.reset(token)


def emit_token(text: str) -> None:
    """生成节点调用：把增量 token 写入当前活动 sink（无 sink 时 no-op）。

    非流式路径（无活动 sink）时本函数零开销返回，生成结果仍由调用方累积。
    """
    sink = _ACTIVE.get()
    if sink is not None:
        sink.put_token(text)


__all__ = [
    "KIND_TOKEN",
    "KIND_RESULT",
    "KIND_ERROR",
    "KIND_DONE",
    "POLL_TIMEOUT",
    "StreamSink",
    "set_active_sink",
    "get_active_sink",
    "reset_active_sink",
    "emit_token",
]
