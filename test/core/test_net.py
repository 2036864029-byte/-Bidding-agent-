"""core.net 出口约定测试 —— 环回地址不得被环境代理劫持。

真实踩坑（2026-09-14）：embedding 走本机隧道 127.0.0.1:18003，而
httpx / requests 默认 trust_env=True，把 HTTP_PROXY 也套到环回上 →
代理够不到客户端的 localhost → ``APIConnectionError``，但 curl 手测却是通的。

⚠️ 平台差异：**Windows 环境变量大小写不敏感**，``NO_PROXY`` 与 ``no_proxy``
是同一个变量；POSIX 上是两个。所以清理环境要按下面的 `_clear_proxy_env` 来，
否则会出现"删小写把大写也删了"的诡异结果。
"""

from __future__ import annotations

import os

import pytest

from core.net import ensure_no_proxy_for_loopback, is_loopback_url


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 NO_PROXY 清干净（两个拼写都删；Windows 上等价于删同一个）。"""
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(key, raising=False)


class TestIsLoopbackUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:18003/v1",
            "http://localhost:8000",
            "http://127.0.0.5:1234",  # 127.0.0.0/8 整个网段
            "http://[::1]:8000",
        ],
    )
    def test_环回地址(self, url: str) -> None:
        assert is_loopback_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.siliconflow.cn/v1",
            "https://api.deepseek.com/v1",
            "http://8.133.217.57:8003/v1",  # 公网 IP 不是环回
            "not-a-url",
        ],
    )
    def test_非环回地址(self, url: str) -> None:
        assert is_loopback_url(url) is False


class TestEnsureNoProxyForLoopback:
    def test_空环境时补齐环回条目(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_proxy_env(monkeypatch)

        assert ensure_no_proxy_for_loopback() is True

        value = os.environ["NO_PROXY"]
        for host in ("127.0.0.1", "localhost", "::1"):
            assert host in value
        # 小写拼写同样写入（POSIX 上部分库只认小写；Windows 是同一变量）
        assert os.environ.get("no_proxy")

    def test_保留既有条目且幂等(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_proxy_env(monkeypatch)
        monkeypatch.setenv("NO_PROXY", "example.com")

        assert ensure_no_proxy_for_loopback() is True
        value = os.environ["NO_PROXY"]
        assert value.startswith("example.com")  # 不覆盖用户已有设置
        assert "127.0.0.1" in value

        # 第二次调用不该再改动
        assert ensure_no_proxy_for_loopback() is False

    def test_已覆盖时不动环境(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_proxy_env(monkeypatch)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")

        assert ensure_no_proxy_for_loopback() is False
        assert os.environ["NO_PROXY"] == "127.0.0.1,localhost,::1"
