"""MinerU 官方 API 客户端单测（httpx 打桩，全程不联网）。

覆盖官方 v4 四步流程：申请上传链接 → PUT 上传 → 轮询 batch → 下载 zip 取
full.md。重点断言失败语义（超时 / 任务失败 / 接口报错都不静默返回空）。
"""

from __future__ import annotations

import io
import zipfile

import pytest

import public_kb.services.mineru_cloud_parser as cloud_mod
from core.config.settings import Settings
from public_kb.services.mineru_cloud_parser import (
    MinerUCloudParser,
    extract_markdown_from_zip,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", content=b""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.content = content

    def json(self):
        return self._json


def _zip_with(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return buf.getvalue()


def _done_payload(state="done", zip_url="https://cdn/result.zip", err=""):
    item = {"file_name": "a.pdf", "state": state, "err_msg": err}
    if state == "done":
        item["full_zip_url"] = zip_url
    return {"code": 0, "data": {"batch_id": "b1", "extract_result": [item]}}


class FakeHttpx:
    """替身 httpx 模块：记录调用，poll 序列可编程。"""

    HTTPError = Exception

    def __init__(self, zip_bytes: bytes):
        self.calls: list[tuple] = []
        self.batch_json = {
            "code": 0,
            "data": {"batch_id": "b1", "file_urls": ["https://oss/up1"]},
        }
        self.batch_status = 200
        self.upload_status = 200
        self.zip_bytes = zip_bytes
        self.zip_status = 200
        self.poll_payloads = [_done_payload()]
        self._poll_idx = 0

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json))
        return FakeResponse(self.batch_status, self.batch_json)

    def put(self, url, content=None, timeout=None):
        self.calls.append(("PUT", url, content))
        return FakeResponse(self.upload_status)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        if "extract-results/batch/" in url:
            idx = min(self._poll_idx, len(self.poll_payloads) - 1)
            self._poll_idx += 1
            return FakeResponse(200, self.poll_payloads[idx])
        return FakeResponse(self.zip_status, content=self.zip_bytes)

    def count(self, method: str) -> int:
        return sum(1 for c in self.calls if c[0] == method)


@pytest.fixture
def fake_httpx(monkeypatch):
    fake = FakeHttpx(_zip_with({"full.md": "# 第一条 正文\n"}))
    monkeypatch.setattr(cloud_mod, "httpx", fake)
    return fake


def _settings(tmp_path, *, token="sekret", poll_timeout=5) -> Settings:
    s = Settings()
    s.mineru_api_token = token
    s.mineru_output_dir = str(tmp_path)
    s.mineru_official_poll_interval = 0
    s.mineru_official_poll_timeout = poll_timeout
    s.mineru_official_language = "ch"
    return s


def _pdf(tmp_path, name="a.pdf", body=b"%PDF-1.4 fake"):
    p = tmp_path / name
    p.write_bytes(body)
    return p


# ── 前置校验 ───────────────────────────────────────────────


def test_requires_token(tmp_path):
    """没有 Token 就别发请求（官方 API 401 之前先本地拦下）。"""
    parser = MinerUCloudParser(_settings(tmp_path, token=""))
    with pytest.raises(RuntimeError, match="未配置"):
        parser.parse(_pdf(tmp_path))


def test_missing_file(tmp_path):
    parser = MinerUCloudParser(_settings(tmp_path))
    with pytest.raises(FileNotFoundError):
        parser.parse(tmp_path / "nope.pdf")


# ── 主流程 ─────────────────────────────────────────────────


def test_full_flow_returns_markdown(fake_httpx, tmp_path):
    parser = MinerUCloudParser(_settings(tmp_path))
    md = parser.parse(_pdf(tmp_path))
    assert md == "# 第一条 正文\n"
    assert fake_httpx.count("POST") == 1          # 申请上传链接
    assert fake_httpx.count("PUT") == 1           # 上传文件
    assert fake_httpx.count("GET") == 2           # 轮询 + 下载 zip


def test_upload_sent_bytes_and_no_content_type(fake_httpx, tmp_path):
    """PUT 上传的是原始字节；Content-Type 由调用方不设（签名为准）。"""
    parser = MinerUCloudParser(_settings(tmp_path))
    parser.parse(_pdf(tmp_path, body=b"%PDF-1.4 payload"))
    put_call = next(c for c in fake_httpx.calls if c[0] == "PUT")
    assert put_call[2] == b"%PDF-1.4 payload"
    assert put_call[1] == "https://oss/up1"


def test_bearer_header_on_api_calls(fake_httpx, tmp_path):
    parser = MinerUCloudParser(_settings(tmp_path))
    parser.parse(_pdf(tmp_path))
    post_call = next(c for c in fake_httpx.calls if c[0] == "POST")
    assert post_call[2]["Authorization"] == "Bearer sekret"


def test_page_range_not_forwarded_to_official(fake_httpx, tmp_path):
    """page_range 只进缓存 key，**不下发** —— 上行 PDF 已是切好的子 PDF。

    若把源 PDF 的页范围再声明给官方 page_ranges，会被按子 PDF 页码二次裁剪，
    内容错位（与自建服务语义对齐：那里 page_range 也只用于缓存键）。
    """
    parser = MinerUCloudParser(_settings(tmp_path))
    parser.parse(_pdf(tmp_path), page_range="3-8")
    body = next(c for c in fake_httpx.calls if c[0] == "POST")[3]
    assert body["files"] == [{"name": "a.pdf"}]
    assert "page_ranges" not in body["files"][0]
    assert body["model_version"] == "pipeline"


def test_cache_hit_skips_network(fake_httpx, tmp_path):
    parser = MinerUCloudParser(_settings(tmp_path))
    pdf = _pdf(tmp_path)
    assert parser.parse(pdf) == "# 第一条 正文\n"
    calls_after_first = len(fake_httpx.calls)
    assert parser.parse(pdf) == "# 第一条 正文\n"
    assert len(fake_httpx.calls) == calls_after_first, "命中缓存不应再发请求"
    assert (tmp_path / "_mineru_cloud_cache").is_dir()


def test_cache_key_varies_by_page_range(fake_httpx, tmp_path):
    parser = MinerUCloudParser(_settings(tmp_path))
    pdf = _pdf(tmp_path)
    parser.parse(pdf)
    before = len(fake_httpx.calls)
    parser.parse(pdf, page_range="1-2")
    assert len(fake_httpx.calls) > before, "不同 page_range 不能共用缓存"


# ── 失败语义 ───────────────────────────────────────────────


def test_task_failed_raises(fake_httpx, tmp_path):
    fake_httpx.poll_payloads = [_done_payload(state="failed", err="页数超限")]
    parser = MinerUCloudParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="页数超限"):
        parser.parse(_pdf(tmp_path))


def test_poll_timeout_raises(fake_httpx, tmp_path):
    fake_httpx.poll_payloads = [_done_payload(state="running")]
    parser = MinerUCloudParser(_settings(tmp_path, poll_timeout=0))
    with pytest.raises(RuntimeError, match="轮询超时"):
        parser.parse(_pdf(tmp_path))


def test_api_error_code_raises(fake_httpx, tmp_path):
    fake_httpx.batch_json = {"code": -60001, "msg": "token 无效"}
    parser = MinerUCloudParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="token 无效"):
        parser.parse(_pdf(tmp_path))


def test_http_error_status_raises(fake_httpx, tmp_path):
    fake_httpx.batch_status = 401
    parser = MinerUCloudParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="401"):
        parser.parse(_pdf(tmp_path))


def test_upload_failure_raises(fake_httpx, tmp_path):
    fake_httpx.upload_status = 500
    parser = MinerUCloudParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="上传"):
        parser.parse(_pdf(tmp_path))


def test_missing_zip_url_raises(fake_httpx, tmp_path):
    fake_httpx.poll_payloads = [
        {"code": 0, "data": {"batch_id": "b1", "extract_result": [{"state": "done"}]}}
    ]
    parser = MinerUCloudParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="full_zip_url"):
        parser.parse(_pdf(tmp_path))


# ── zip 产物解析 ───────────────────────────────────────────


def test_extract_prefers_full_md():
    data = _zip_with({"other.md": "fallback", "full.md": "# 正文", "layout.json": "{}"})
    assert extract_markdown_from_zip(data) == "# 正文"


def test_extract_falls_back_to_any_markdown():
    """官方产物目录名可能调整，退让到第一个 .md 好过整本失败。"""
    data = _zip_with({"auto/part-1.md": "# 分片"})
    assert extract_markdown_from_zip(data) == "# 分片"


def test_extract_without_markdown_raises():
    data = _zip_with({"layout.json": "{}"})
    with pytest.raises(RuntimeError, match="未找到 Markdown"):
        extract_markdown_from_zip(data)


def test_extract_bad_zip_raises():
    with pytest.raises(RuntimeError, match="不是合法 zip"):
        extract_markdown_from_zip(b"not a zip at all")


# ── 版本串与配置自检 ───────────────────────────────────────


def test_parser_version_tracks_model_version(tmp_path):
    s = _settings(tmp_path)
    assert MinerUCloudParser(s).resolve_parser_version() == "official-pipeline"
    assert (
        MinerUCloudParser(s, model_version="vlm").resolve_parser_version()
        == "official-vlm"
    )


def test_health_does_not_hit_network(fake_httpx, tmp_path):
    """官方无 /health 端点，自检只读配置（避免伪造探测把调用打出去）。"""
    health = MinerUCloudParser(_settings(tmp_path)).health()
    assert health["status"] == "ok"
    assert health["parser"] == "mineru-official"
    assert fake_httpx.calls == []


def test_health_unconfigured_without_token(tmp_path):
    health = MinerUCloudParser(_settings(tmp_path, token="")).health()
    assert health["status"] == "unconfigured"
