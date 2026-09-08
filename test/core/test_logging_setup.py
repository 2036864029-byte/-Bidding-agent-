"""core.observability.logging 测试（Wave 0 / W0-7）。

覆盖：级别解析优先级、根 logger 配置幂等、级别对齐。
注意：pytest 自身会在根 logger 上挂 LogCaptureHandler，故断言前先清空并在
finally 中恢复，避免污染其它用例。
"""

from __future__ import annotations

import logging

from core.observability.logging import (
    DEFAULT_FORMAT,
    reset_logging,
    resolve_level,
    setup_logging,
)


class _RootLoggerGuard:
    """保存/恢复根 logger 的 handler 与级别。"""

    def __enter__(self):
        self._root = logging.getLogger()
        self._handlers = list(self._root.handlers)
        self._level = self._root.level
        for handler in list(self._root.handlers):
            self._root.removeHandler(handler)
        return self._root

    def __exit__(self, *exc):
        for handler in list(self._root.handlers):
            self._root.removeHandler(handler)
        for handler in self._handlers:
            self._root.addHandler(handler)
        self._root.setLevel(self._level)
        return False


# ── resolve_level ────────────────────────────────────────────

def test_resolve_level_explicit_wins(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    assert resolve_level(verbose=False, level="DEBUG") == "DEBUG"


def test_resolve_level_env_overrides_verbose(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warning")
    assert resolve_level(verbose=True) == "WARNING"


def test_resolve_level_falls_back_to_verbose(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert resolve_level(verbose=False) == "INFO"
    assert resolve_level(verbose=True) == "DEBUG"


def test_resolve_level_ignores_invalid(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "not-a-level")
    assert resolve_level(verbose=False) == "INFO"


# ── setup_logging ────────────────────────────────────────────

def test_setup_logging_installs_root_handler():
    with _RootLoggerGuard() as root:
        setup_logging()
        assert root.handlers, "服务态下必须有 handler，否则 INFO 埋点丢失"
        assert root.level == logging.INFO
        assert root.handlers[0].formatter._fmt == DEFAULT_FORMAT


def test_setup_logging_is_idempotent():
    with _RootLoggerGuard() as root:
        setup_logging()
        count = len(root.handlers)
        setup_logging(level="DEBUG")
        assert len(root.handlers) == count, "重复调用不应叠加 handler"
        assert root.level == logging.DEBUG, "已有 handler 时仍应能对齐级别"


def test_reset_logging_clears_handlers():
    with _RootLoggerGuard() as root:
        setup_logging()
        assert root.handlers
        reset_logging()
        assert not root.handlers
