"""core.streaming — Token 级流式通道（W3-2）。"""

from core.streaming.sink import (
    KIND_DONE,
    KIND_ERROR,
    KIND_RESULT,
    KIND_TOKEN,
    POLL_TIMEOUT,
    StreamSink,
    emit_token,
    get_active_sink,
    reset_active_sink,
    set_active_sink,
)

__all__ = [
    "KIND_DONE",
    "KIND_ERROR",
    "KIND_RESULT",
    "KIND_TOKEN",
    "POLL_TIMEOUT",
    "StreamSink",
    "emit_token",
    "get_active_sink",
    "reset_active_sink",
    "set_active_sink",
]
