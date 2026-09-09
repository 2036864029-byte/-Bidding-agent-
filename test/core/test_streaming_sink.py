"""core/streaming/sink 单元测试（W3-2 真流式通道）。

覆盖：
  1. emit_token 无活动 sink 时 no-op（非流式路径零开销、零行为变化）。
  2. 有活动 sink 时按序入队；get 超时返回 None 而非抛 queue.Empty。
  3. 跨线程 context 拷贝：子线程（Thread 构造）可见主线程设置的活动 sink。
  4. reset_active_sink 后回退到无 sink 行为。
"""

from __future__ import annotations

import contextvars
import threading

from core.streaming.sink import (
    StreamSink,
    emit_token,
    get_active_sink,
    reset_active_sink,
    set_active_sink,
)


def test_emit_token_noop_without_active_sink():
    """无活动 sink：emit_token 不抛错、不产生任何事件（纯累积路径）。"""
    emit_token("你好")  # 不应抛错
    assert get_active_sink() is None


def test_sink_put_and_get_in_order():
    sink = StreamSink()
    sink.put_token("你")
    sink.put_token("好")
    sink.put_event("result", {"answer": "ok"})
    sink.put_event("done", None)

    assert sink.get(timeout=1.0) == ("token", "你")
    assert sink.get(timeout=1.0) == ("token", "好")
    assert sink.get(timeout=1.0) == ("result", {"answer": "ok"})
    assert sink.get(timeout=1.0) == ("done", None)


def test_sink_get_returns_none_on_timeout():
    """空队列 + 短超时 → 返回 None（SSE 轮询语义），不抛 queue.Empty。"""
    sink = StreamSink()
    assert sink.get(timeout=0.01) is None
    sink.put_token("x")
    assert sink.get(timeout=0.01) == ("token", "x")
    assert sink.get(timeout=0.01) is None


def test_emit_token_writes_to_active_sink():
    token = set_active_sink(None)  # 先确保干净起点
    try:
        sink = StreamSink()
        set_active_sink(sink)
        emit_token("你")
        emit_token("好")
        assert sink.get(timeout=1.0) == ("token", "你")
        assert sink.get(timeout=1.0) == ("token", "好")
    finally:
        reset_active_sink(token)


def test_active_sink_visible_in_worker_thread():
    """SSE 工作线程机制：threading.Thread 不自动拷贝 contextvars（实测），
    生产代码用 ``copy_context().run`` 显式带入 —— 此处验证同一机制下
    emit_token 命中主线程设置的 sink。"""
    sink = StreamSink()
    token = set_active_sink(sink)
    ctx = contextvars.copy_context()
    seen: list[str] = []

    def worker():
        emit_token("跨线程")
        seen.append("ok")

    try:
        t = threading.Thread(target=lambda: ctx.run(worker))
        t.start()
        t.join(timeout=5)

        assert not t.is_alive()
        assert seen == ["ok"]
        assert sink.get(timeout=1.0) == ("token", "跨线程")
    finally:
        reset_active_sink(token)


def test_reset_active_sink_restores_noop():
    token = set_active_sink(None)
    set_active_sink(None)
    sink = StreamSink()
    set_active_sink(sink)
    emit_token("a")
    reset_active_sink(token)  # 回到最初（无 sink）状态

    emit_token("不应入队")
    assert sink.get(timeout=1.0) == ("token", "a")
    assert sink.get(timeout=0.01) is None
