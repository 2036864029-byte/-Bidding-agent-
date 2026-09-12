# 功能：MinerU 远程解析客户端单元测试（httpx 打桩，不连真实服务）。
"""Tests for MinerUApiParser (HTTP client for remote MinerU service).

覆盖（对齐部署补充方案 §4.1 协议）：
  - 未配置服务地址时 fail-fast；
  - POST /parse 成功返回 Markdown 并落本地缓存；
  - 缓存命中时不重复 POST；
  - Authorization: Bearer 头与 page_range 透传；
  - 服务端非 200 抛 RuntimeError。
"""

from __future__ import annotations

import json

import pytest

import public_kb.services.mineru_api_parser as parser_mod
from core.config.settings import Settings
from public_kb.services.mineru_api_parser import MinerUApiParser


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class FakeHttpx:
    """替身 httpx 模块：记录调用，可编程返回。"""

    HTTPError = Exception

    def __init__(self):
        self.calls = []
        self.get_timeouts = []
        self.health_json = {"status": "ok", "parser_version": "2.0.0"}
        self.parse_json = {"markdown": "# 第一条 正文\n"}

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        self.get_timeouts.append(timeout)
        return FakeResponse(200, self.health_json)

    def post(self, url, headers=None, data=None, files=None, timeout=None):
        self.calls.append(("POST", url, headers, data, files))
        return FakeResponse(200, self.parse_json)


@pytest.fixture
def fake_httpx(monkeypatch):
    fake = FakeHttpx()
    monkeypatch.setattr(parser_mod, "httpx", fake)
    return fake


def _settings(tmp_path, base_url="http://127.0.0.1:8002", token="sekret"):
    s = Settings()
    s.mineru_api_base_url = base_url
    s.mineru_api_token = token
    s.mineru_output_dir = str(tmp_path)
    return s


def test_parse_requires_base_url(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    parser = MinerUApiParser(_settings(tmp_path, base_url=""))
    with pytest.raises(RuntimeError, match="未配置"):
        parser.parse(pdf)


def test_parse_success_and_cache(fake_httpx, tmp_path):
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    parser = MinerUApiParser(_settings(tmp_path))

    md1 = parser.parse(pdf, page_range="12-18")
    assert md1 == "# 第一条 正文\n"
    assert any(call[0] == "POST" for call in fake_httpx.calls)

    # 第二次命中本地缓存，不再 POST
    fake_httpx.calls.clear()
    md2 = parser.parse(pdf, page_range="12-18")
    assert md2 == md1
    assert all(call[0] != "POST" for call in fake_httpx.calls)


def test_authorization_header_and_page_range(fake_httpx, tmp_path):
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    parser = MinerUApiParser(_settings(tmp_path))
    parser.parse(pdf, page_range="5-9")

    post_call = next(c for c in fake_httpx.calls if c[0] == "POST")
    _, url, headers, data, files = post_call
    assert headers["Authorization"] == "Bearer sekret"
    assert data["page_range"] == "5-9"
    assert files["file"][0] == "sub.pdf"


def test_server_error_raises(fake_httpx, tmp_path):
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    fake_httpx.post = lambda url, headers=None, data=None, files=None, timeout=None: FakeResponse(500, {}, "boom")
    parser = MinerUApiParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="500"):
        parser.parse(pdf)


def test_empty_markdown_raises(fake_httpx, tmp_path):
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    fake_httpx.parse_json = {"markdown": ""}
    parser = MinerUApiParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="空 Markdown"):
        parser.parse(pdf)


# ── P5：服务不可达时的 parser_version 回退（缓存键才不会全 miss）────────


class FailingHttpx(FakeHttpx):
    """GET / POST 均抛错（模拟 MinerU 服务不可达）。"""

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        self.get_timeouts.append(timeout)
        raise self.HTTPError("connection refused")

    def post(self, url, headers=None, data=None, files=None, timeout=None):
        self.calls.append(("POST", url, headers, data, files))
        raise self.HTTPError("connection refused")


def _cache_dir(tmp_path):
    return tmp_path / "_mineru_api_cache"


def test_probe_uses_short_timeout(fake_httpx, tmp_path):
    """探测用短超时，不占用解析长超时（否则离线重跑会长时间挂起）。"""
    parser = MinerUApiParser(_settings(tmp_path))
    parser._timeout = 1800
    parser.resolve_parser_version()
    assert fake_httpx.get_timeouts == [parser_mod._PROBE_TIMEOUT_SEC]


def test_resolve_version_persists_on_success(fake_httpx, tmp_path):
    """探测成功 → 持久化版本，供后续离线回退。"""
    parser = MinerUApiParser(_settings(tmp_path))
    assert parser.resolve_parser_version() == "2.0.0"
    assert (_cache_dir(tmp_path) / "_parser_version.txt").read_text(
        encoding="utf-8"
    ) == "2.0.0"


def test_resolve_version_falls_back_to_persisted_file(tmp_path, monkeypatch):
    """服务不可达 → 回退到持久化的 parser_version（键不变 → 缓存可命）。"""
    monkeypatch.setattr(parser_mod, "httpx", FailingHttpx())
    _cache_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    (_cache_dir(tmp_path) / "_parser_version.txt").write_text(
        "magic-pdf, version 1.3.12", encoding="utf-8"
    )
    parser = MinerUApiParser(_settings(tmp_path))
    assert parser.resolve_parser_version() == "magic-pdf, version 1.3.12"


def test_resolve_version_recovers_from_cache_sidecar(tmp_path, monkeypatch):
    """无持久化文件时，从缓存 sidecar 的 parser_version 恢复并落盘。"""
    monkeypatch.setattr(parser_mod, "httpx", FailingHttpx())
    _cache_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    (_cache_dir(tmp_path) / "abc.json").write_text(
        json.dumps({"parser_version": "magic-pdf, version 9.9.9"}),
        encoding="utf-8",
    )
    parser = MinerUApiParser(_settings(tmp_path))
    assert parser.resolve_parser_version() == "magic-pdf, version 9.9.9"
    assert (_cache_dir(tmp_path) / "_parser_version.txt").read_text(
        encoding="utf-8"
    ) == "magic-pdf, version 9.9.9"


def test_offline_rerun_hits_local_cache(tmp_path, monkeypatch):
    """P5 验收：服务下线后重跑同一子 PDF，应命中本地缓存、零 POST、解析成功。"""
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")

    # 1) 在线：解析一次 → 落缓存 + 持久化版本
    monkeypatch.setattr(parser_mod, "httpx", FakeHttpx())
    online_md = MinerUApiParser(_settings(tmp_path)).parse(pdf, page_range="1-3")
    assert online_md == "# 第一条 正文\n"

    # 2) 服务下线（新实例，无进程内 memo）
    offline = FailingHttpx()
    monkeypatch.setattr(parser_mod, "httpx", offline)
    offline_md = MinerUApiParser(_settings(tmp_path)).parse(pdf, page_range="1-3")

    assert offline_md == online_md
    assert all(call[0] != "POST" for call in offline.calls)


def test_fallback_empty_when_no_history(tmp_path, monkeypatch):
    """无任何历史（无持久化文件、无 sidecar）→ 回退空串，不抛错。"""
    monkeypatch.setattr(parser_mod, "httpx", FailingHttpx())
    parser = MinerUApiParser(_settings(tmp_path))
    assert parser.resolve_parser_version() == ""
