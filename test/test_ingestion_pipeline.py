"""Offline tests for the ingestion pipeline and CSV source contracts.

9.3 C-2：原 legacy IngestionPipeline/CsvSource 主题随 `_legacy` 清退移除，
替换为三段式 FileIngestionPipeline 对政策/QA CSV 的等价覆盖；sink 契约测试保留。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence

from langchain_core.documents import Document

from core.config.settings import Settings
from public_kb.ingestion.pipeline import FileIngestionPipeline
from public_kb.ingestion.sinks.markdown_sink import MarkdownSink
from public_kb.ingestion.sinks.milvus_sink import MilvusSink


def _settings(**overrides: Any) -> Settings:
    values = {"milvus_uri": "http://offline.invalid:19530"}
    values.update(overrides)
    return Settings(**values)


class CountingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def write(
        self,
        documents: Sequence[Document],
        *,
        records: Sequence[Dict[str, Any]],
    ) -> int:
        self.calls.append({"documents": list(documents), "records": list(records)})
        return len(documents)


def _policy_csv(path: Path) -> None:
    path.write_text(
        "title,content,publish_date,source_url\n"
        "政策名称,\"# 政策名称\n第一条 这是用于验证政策内容。\","
        "2024-01-01,https://example.com/policy\n",
        encoding="utf-8",
    )


def test_pipeline_processes_csv_into_markdown_chunks(tmp_path):
    """三段式对政策/QA CSV 产出 markdown 行块（入向量路，C-2 迁移后等价覆盖）。"""
    csv_path = tmp_path / "policy_data.csv"
    _policy_csv(csv_path)
    sink = CountingSink()
    results = FileIngestionPipeline(_settings(), sink=sink).run([csv_path])
    res = results[0]

    assert res.status == "completed"
    assert res.chunk_count >= 1
    # 正文内容入块（TextCleaner 剥离 markdown 标题属预期清洗行为）
    text = "".join(d.page_content for d in sink.calls[0]["documents"])
    assert "第一条 这是用于验证政策内容。" in text
    # table IR 记录行数（price 侧 SQL 消费）
    assert res.detail["table_row_count"] == 1


def test_pipeline_csv_preview_mode_no_sink_write(tmp_path):
    """--no-import 预览：sink=None 干跑，产出 chunk 计数但不入库。"""
    csv_path = tmp_path / "policy_data.csv"
    _policy_csv(csv_path)
    results = FileIngestionPipeline(_settings(), sink=None).run([csv_path])
    res = results[0]

    assert res.status == "completed"
    assert res.chunk_count >= 1
    assert res.inserted_count == 0


class FakeMilvusManager:
    def __init__(self) -> None:
        self.initialized: List[Sequence[Document]] = []
        self.added: List[Sequence[Document]] = []

    def initialize_collection(
        self,
        documents: Sequence[Document],
        *,
        recreate: bool = False,
    ) -> int:
        self.initialized.append(documents)
        return len(documents)

    def add_documents(self, documents: Sequence[Document]) -> int:
        self.added.append(documents)
        return len(documents)


def test_milvus_sink_supports_initialize_and_append_modes():
    manager = FakeMilvusManager()
    initialize_sink = MilvusSink(manager, mode="initialize")
    append_sink = MilvusSink(manager, mode="append")

    assert initialize_sink.write([_document()], records=[]) == 1
    assert append_sink.write([_document()], records=[]) == 1
    assert len(manager.initialized) == 1
    assert len(manager.added) == 1


def test_markdown_sink_writes_document_preview(tmp_path):
    document = _document()
    document.metadata["source_file"] = "sample.csv"
    sink = MarkdownSink(tmp_path, source_file="sample.csv")

    count = sink.write([document], records=[{"_line_num": 1, "title": "测试"}])

    assert count == 1
    assert (tmp_path / "sample_chunks.md").exists()


def _document() -> Document:
    return Document(
        page_content="测试内容",
        metadata={
            "doc_name": "测试文档",
            "chapter": "第一章",
            "chunk_index": 0,
        },
    )
