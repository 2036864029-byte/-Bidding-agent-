# 功能：用户临时文件解析工具（9.3 Track D / D-1）。
"""FileParseTool — 解析用户临时上传文件为清洗后 Markdown 文本。

复用 ingestion 三段式的解析与清洗逻辑（FormatRouter + build_normalizer +
TextCleaner + chunk_markdown，与 rag_engine/handoff 同口径）；**不调用 MilvusSink**、
不写入任何向量库 —— 供 doc_qa 对临时文件做单次问答（无检索模式，D-0-3）。
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union

from core.config.settings import Settings

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 600


def _safe_unlink(path: Path) -> None:
    """删除临时文件；Windows 下解析器可能短暂持有句柄，短重试后放弃。"""
    for _ in range(5):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.1)
    logger.warning("临时文件未能立即删除（Windows 句柄占用）: %s", path)


@dataclass
class FileParseResult:
    """文件解析产物（临时文件单次问答的最小可消费单元）。"""

    source_name: str
    source_format: str
    markdown: str          # 清洗后 markdown 文本
    preview: str           # 预览（截断）
    chunk_count: int       # 按 chunk_markdown 口径的分块数（一致性参考）
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "source_format": self.source_format,
            "markdown": self.markdown,
            "preview": self.preview,
            "chunk_count": self.chunk_count,
            "diagnostics": self.diagnostics,
        }


def _preview(markdown: str, limit: int = _PREVIEW_CHARS) -> str:
    text = markdown.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _parse_path(path: Path, settings: Settings) -> tuple[str, str, list]:
    """对文件路径跑三段式 Stage A 规范化，返回 (format, markdown_joined, irs)。"""
    from .router import FormatRouter
    from .normalizers import build_normalizer

    router = FormatRouter()
    fmt = router.classify(path)
    routed = router.route(path)
    normalizer = build_normalizer(fmt, settings=settings)
    irs = normalizer.normalize(routed)
    md_irs = [ir for ir in irs if ir.ir_format == "markdown"]
    markdown = "\n\n".join(ir.content for ir in md_irs if ir.content.strip())
    return fmt, markdown, irs


def parse_file(
    source: Union[str, Path, bytes],
    *,
    source_name: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> FileParseResult:
    """解析文件路径/字节流 → 清洗后 Markdown（不写 Milvus）。

    Args:
        source: 文件路径或原始字节流。
        source_name: 文件名（bytes 输入必需，用于格式路由与 doc_name）。
        settings: 全局配置；None 时新建 Settings()。

    Raises:
        FileNotFoundError: 路径不存在。
        ValueError: 未解析出可读文本 / bytes 输入缺 source_name。
    """
    settings = settings or Settings()
    if isinstance(source, (str, Path)):
        name = source_name or Path(source).name
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        fmt, markdown, irs = _parse_path(path, settings)
    else:
        if not source_name:
            raise ValueError("bytes 输入必须提供 source_name")
        name = source_name
        suffix = Path(name).suffix or ".tmp"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(source)
            tmp_path = Path(fh.name)
        try:
            fmt, markdown, irs = _parse_path(tmp_path, settings)
        finally:
            _safe_unlink(tmp_path)

    if not markdown.strip():
        raise ValueError(f"文件「{name}」未解析出可读文本")

    from .transforms import TextCleaner
    from .handoff import chunk_markdown

    cleaned = TextCleaner().clean(markdown)
    docs = chunk_markdown(cleaned, name, settings)
    result = FileParseResult(
        source_name=name,
        source_format=fmt,
        markdown=cleaned,
        preview=_preview(cleaned),
        chunk_count=len(docs),
        diagnostics={"parsed_irs": len(irs)},
    )
    logger.info(
        "FileParseTool: %s (%s) → %d 块, %d 字符",
        name, fmt, result.chunk_count, len(cleaned),
    )
    return result


def parse_file_as_tool():
    """LangChain tool 形态的 parse_file（对齐 kb_search_as_tool，供 agent 挂载）。"""
    from langchain_core.tools import tool

    @tool
    def parse_file_tool(file_path: str, source_name: Optional[str] = None) -> dict:
        """解析用户上传的文件（PDF/文本/CSV）为清洗后 Markdown 文本，不写入向量库。

        Args:
            file_path: 文件路径。
            source_name: 文件名（缺省取路径 basename）。

        Returns:
            dict：{source_name, source_format, markdown, preview, chunk_count, diagnostics}。
        """
        result = parse_file(file_path, source_name=source_name)
        return result.to_dict()

    return parse_file_tool


__all__ = ["FileParseResult", "parse_file", "parse_file_as_tool"]
