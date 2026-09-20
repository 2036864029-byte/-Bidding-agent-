"""网络闸门自身的元测试 —— 保证闸门"真的在拦"，而不是悄悄失效。

为什么值得单独测：闸门是 autouse fixture 的一层包装，一旦被改坏（顺序变了、
marker 判断写反了、socket 补丁被别处覆盖），**表现是"什么都不发生"** ——
测试全绿，但非 hermetic 用例又开始偷偷联网。这种"静默失效"和闸门想解决的问题
是同一类，所以它自己必须有守卫。

命名约定：本文件的两个用例分别验证"该拦的拦住了"与"该放的放行了"。
"""

from __future__ import annotations

import socket

import pytest

from _support.hermetic import NetworkBlockedByTest, blocked_attempts


def test_network_is_blocked_by_default():
    """非 live 用例联网 → 必须抛 NetworkBlockedByTest（而不是连上去或超时）。"""
    with pytest.raises(NetworkBlockedByTest) as exc:
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)

    # 提示里要带用例名与两个修法 —— 否则开发者拿到异常也不知道怎么办
    message = str(exc.value)
    assert "test_hermetic_gate" in message, "提示未指明当前用例"
    assert "_support/fakes.py" in message, "提示未指向假件模块"
    assert "pytest.mark.integration" in message, "提示未给出标记修法"


def test_blocked_attempt_is_recorded():
    """被拦的尝试要落进记录 —— 否则"降级后碰巧通过"的用例永远查不出来。

    这条对应 conftest 的 `pytest_terminal_summary`：它读的就是这个记录。
    """
    before = len(blocked_attempts())
    with pytest.raises(NetworkBlockedByTest):
        socket.create_connection(("198.51.100.7", 443), timeout=0.1)

    after = blocked_attempts()
    assert len(after) > before, "联网尝试未被记录"
    nodeid, addr = after[-1]
    assert "test_hermetic_gate" in nodeid
    assert "198.51.100.7" in addr


@pytest.mark.integration
def test_network_allowed_when_marked_integration():
    """标了 integration 的用例放行 —— 闸门不安装，故不抛 NetworkBlockedByTest。

    不断言"连得上"（那取决于环境）；只断言**闸门没拦**：对不可达地址应得到
    连接类异常，而不是闸门异常。
    """
    with pytest.raises(OSError) as exc:
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    assert not isinstance(exc.value, NetworkBlockedByTest), "integration 用例被误拦"


@pytest.mark.e2e
def test_network_allowed_when_marked_e2e():
    """e2e 同理放行。"""
    with pytest.raises(OSError) as exc:
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    assert not isinstance(exc.value, NetworkBlockedByTest), "e2e 用例被误拦"
