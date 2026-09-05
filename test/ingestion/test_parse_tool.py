"""FileParseTool 测试（9.3 Track D / D-1、D-4）— 不触 Milvus、不写向量库。"""

from __future__ import annotations

import pytest

from public_kb.ingestion.parse_tool import parse_file, parse_file_as_tool


def _settings():
    from core.config.settings import Settings
    return Settings(milvus_uri="http://offline.invalid:19530")


def test_parse_file_text_returns_cleaned_markdown(tmp_path):
    p = tmp_path / "contract.txt"
    p.write_text("第一章 这是上传文档的正文内容，用于验证 FileParseTool 的解析清洗链路。", encoding="utf-8")

    result = parse_file(p, settings=_settings())

    assert result.source_format == "text"
    assert "第一章" in result.markdown
    assert result.preview
    assert result.chunk_count >= 1


def test_parse_file_bytes_input_requires_source_name(tmp_path):
    data = "第一章 这是字节流输入的文档正文。".encode("utf-8")
    result = parse_file(data, source_name="upload.txt", settings=_settings())
    assert result.source_name == "upload.txt"
    assert result.markdown

    with pytest.raises(ValueError, match="source_name"):
        parse_file(data, settings=_settings())


def test_parse_file_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_file(tmp_path / "nope.pdf", settings=_settings())


def test_parse_file_no_parsable_text_raises(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="未解析出可读文本"):
        parse_file(p, settings=_settings())


def test_parse_file_does_not_touch_milvus(monkeypatch, tmp_path):
    """9.3 D-4 防污染：FileParseTool 不创建 MilvusSink / MilvusStoreManager。"""
    import public_kb.ingestion.sinks.milvus_sink as milvus_sink
    import core.vectorstore.milvus_client as milvus_client

    calls: list[str] = []
    monkeypatch.setattr(
        milvus_sink, "MilvusSink",
        lambda *a, **k: calls.append("sink") or object(),
    )
    monkeypatch.setattr(
        milvus_client, "MilvusStoreManager",
        lambda *a, **k: calls.append("manager") or object(),
    )
    p = tmp_path / "a.txt"
    p.write_text("第一章 这是上传文档的正文内容，用于验证不触达向量库。", encoding="utf-8")

    result = parse_file(p, settings=_settings())

    assert result.markdown
    assert calls == []  # 全程零向量库对象创建


def test_parse_file_as_tool_returns_dict(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("第一章 这是上传文档的正文内容，用于验证 tool 形态。", encoding="utf-8")

    tool = parse_file_as_tool()
    out = tool.invoke({"file_path": str(p)})

    assert isinstance(out, dict)
    assert out["source_name"] == "a.txt"
    assert out["markdown"]
