# 功能：知识库交接产物——导出解析 markdown 与向量化前 document 数据；导入任一产物入库。
"""知识库交接产物（handoff artifacts）。

导出（本机执行，需 MinerU 可达）——``prepare_handoff(pdf_dir)`` 对每本 PDF：
  <stem>.assembled.md       三档路由解析产物（分块输入，人工可读）
  <stem>.documents.jsonl    分块后的 Document（page_content + metadata，含 chunk_uid）

导入（组员执行，需本地 Milvus + embedding 配置）：
  ingest_documents_jsonl(<file>)     直接消费导出的 document 数据
  ingest_markdown_dir(<dir>)         从 assembled markdown 重新分块

确定性契约：两条导入路径与导出产物逐字段一致（chunk 边界 / chapter /
chunk_index / chunk_uid / 向量），唯一差异是 Milvus 主键 ``id``（schema
auto_id=True，服务端自增）。分块口径与 ``rag_engine._process_single_pdf``
一致（cleaner → adapt_pdf_markdown / chunker）。

doc_name 约定：导出写 ``{pdf.stem}.assembled.md``，doc_name 取 ``pdf.stem``；
markdown 导入从文件名反向推导（去掉 ``.assembled`` 后缀）。两边文件名
不一致会导致 chunk_uid 不同（uid = md5(doc_name|chapter|chunk_index|text)）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.documents import Document

from core.contracts.chunk_ids import compute_chunk_uid
from core.config.settings import Settings
from .sinks.milvus_sink import MilvusSink, MilvusSinkMode
from .transforms import SemanticChunker, TextCleaner
from .normalizers.pdf.pdf_structure import adapt_pdf_markdown

logger = logging.getLogger(__name__)

_MARKDOWN_SUFFIX = ".assembled.md"
_RAW_SUFFIX = ".raw.md"
_RAW_SUBDIR = "raw"       # 解析层原始拼接中间态（留档核验，可重生成/删除）
_CLEAN_SUBDIR = "clean"   # 清洗后 assembled.md（Phase A 资产，人工可读 + Phase B/C 输入）
_CHUNKS_SUBDIR = "chunks" # 分块 documents.jsonl（Phase B 重设计后重建）


def _output_paths(out_dir: str | Path, stem: str) -> tuple[Path, Path, Path]:
    """三段产物按类型分目录：raw / clean / chunks（doc_name 取 stem 不变，
    chunk_uid 不受目录影响）。返回 (raw.md, clean assembled.md, documents.jsonl)。"""
    out = Path(out_dir)
    raw_dir = out / _RAW_SUBDIR
    clean_dir = out / _CLEAN_SUBDIR
    chunks_dir = out / _CHUNKS_SUBDIR
    for d in (raw_dir, clean_dir, chunks_dir):
        d.mkdir(parents=True, exist_ok=True)
    return (
        raw_dir / f"{stem}{_RAW_SUFFIX}",
        clean_dir / f"{stem}{_MARKDOWN_SUFFIX}",
        chunks_dir / f"{stem}.documents.jsonl",
    )


def clean_markdown(
    markdown: str,
    settings: Settings,
    *,
    cleaner: Optional[TextCleaner] = None,
    external: bool = False,
) -> str:
    """Phase A：文本清洗链路。输入原始 markdown（解析产物或外部文件），
    输出清洗后 markdown（**不切分**）。

    清洗 = TextCleaner（短行/LaTeX/页标记/元信息，A2）
         + 结构归一（A3）：目录点线过滤 → 外部 md 治理（external，A3-3，
           须先于 A3-2 以防交叉引用被拆成虚假条标题）→ 标题级归一（A3-1）→
           标题行内联拆分（A3-2）→ 前置页/目录标记（A3-5，书籍路径）
         + 结构校验闭环（A4，有界定点修复）。
    产出是独立可验收的干净文本资产（新版 assembled.md），与切分链路解耦。

    与旧链路等价的说明：`adapt_pdf_markdown` 目前仍自带 strip_toc_noise 与
    _split_heading_inline_articles（幂等），此处重复调用无害；Phase A3 收口时
    从 adapt_pdf_markdown 移除，避免双份。
    """
    from .normalizers.pdf.pdf_structure import strip_toc_noise
    from .transforms.structure import (
        mark_front_matter,
        mark_toc_page,
        normalize_external_md,
        normalize_heading_levels_full,
        repair_structure,
        split_inline_articles,
    )

    cleaner = cleaner or TextCleaner()
    cleaned = cleaner.clean(markdown)
    if settings.enable_pdf_structure:
        if settings.enable_pdf_toc_filter:
            cleaned = strip_toc_noise(cleaned)
        if external and settings.enable_external_md_normalize:
            cleaned = normalize_external_md(cleaned)  # A3-3（须先于 A3-2）
        cleaned = normalize_heading_levels_full(cleaned)  # A3-1
        cleaned = split_inline_articles(cleaned)          # A3-2
        if not external:
            # A3-5（书籍路径）：目录页标记 → 前置页标记/剥离（Phase B 消费标记）
            cleaned = mark_toc_page(cleaned)
            cleaned = mark_front_matter(cleaned, skip=settings.pdf_skip_front_matter)
        # A4 结构校验闭环（有界 3 轮；报告供 A5 验收脚本）
        cleaned, repair_report = repair_structure(cleaned)
        if any(
            n for rule, n in repair_report.items() if rule != "rounds"
        ):
            logger.info("A4 结构校验修复 %s", repair_report)
    return cleaned


def chunk_cleaned_markdown(
    clean_md: str,
    doc_name: str,
    settings: Settings,
    *,
    chunker: Optional[SemanticChunker] = None,
) -> List[Document]:
    """Phase B：切分链路（当前实现，Phase B 重设计时替换）。消费清洗后 markdown。

    与旧 adapt_pdf_markdown 口径一致：表格原子块 + SemanticChunker。切分策略
    与元数据挂载（page_start/end、表格原子块、目录不入 chunk）在 Phase B 重设计。
    """
    chunker = chunker or SemanticChunker(
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    if not settings.enable_pdf_structure:
        return chunker.chunk(clean_md, doc_name)
    return adapt_pdf_markdown(
        clean_md,
        doc_name=doc_name,
        chunker=chunker,
        min_table_rows=settings.pdf_min_table_rows,
        enable_toc_filter=settings.enable_pdf_toc_filter,
        enable_reflow_flag=settings.enable_pdf_reflow_flag,
    )


def chunk_markdown(
    markdown: str,
    doc_name: str,
    settings: Settings,
    *,
    cleaner: Optional[TextCleaner] = None,
    chunker: Optional[SemanticChunker] = None,
) -> List[Document]:
    """兼容壳：clean_markdown → chunk_cleaned_markdown（Phase A 拆分前的完整链路）。

    保持既有调用方（pipeline / parse_tool / prepare_handoff / 测试）行为不变。
    """
    cleaned = clean_markdown(markdown, settings, cleaner=cleaner)
    return chunk_cleaned_markdown(cleaned, doc_name, settings, chunker=chunker)


def _freeze_chunk_uid(documents: Sequence[Document]) -> List[Document]:
    """把 chunk_uid 固化进 metadata，避免两侧代码版本差异导致 uid 漂移。"""
    docs = list(documents)
    for doc in docs:
        doc.metadata["chunk_uid"] = str(
            doc.metadata.get("chunk_uid")
            or compute_chunk_uid(doc.page_content, doc.metadata)
        )
    return docs


# ── 导出 ───────────────────────────────────────────────────


def prepare_handoff(
    pdf_dir: str | Path,
    settings: Settings,
    *,
    out_dir: str | Path,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """解析 pdf_dir 下所有 PDF，产出 assembled.md + documents.jsonl。

    只做解析 + 分块，不创建 embeddings / 不写 Milvus。
    每本 PDF 独立 try，单本失败不阻断其余。

    文件级缓存：``out_dir/clean/{stem}.assembled.md`` 已存在且未传 ``force`` 时，
    跳过解析（Tier C 本就有内容缓存），直接读取 markdown 重新分块——幂等
    /增量重跑。路由规则或解析参数变更后，用 ``force=True`` 或删除该 md 触发
    重新解析。产物分目录：raw/{stem}.raw.md · clean/{stem}.assembled.md ·
    chunks/{stem}.documents.jsonl。
    """
    from .parser_factory import build_pdf_parser

    pdf_dir = Path(pdf_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not pdf_dir.exists():
        raise FileNotFoundError(f"PDF 目录不存在: {pdf_dir}")

    parser = build_pdf_parser(settings)
    summary: List[Dict[str, Any]] = []
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"目录中未找到 PDF 文件: {pdf_dir}")

    timeout_sec = max(1, settings.pdf_tiered_book_timeout_sec)
    for pdf_file in pdf_files:
        # 跳过下划线前缀的开发产物（如 _smoke_test_2p.pdf）
        if pdf_file.name.startswith("_"):
            logger.info("跳过开发产物: %s", pdf_file.name)
            continue
        entry: Dict[str, Any] = {"pdf": pdf_file.name}
        _, md_path, jsonl_path = _output_paths(out_dir, pdf_file.stem)
        try:
            if md_path.exists() and not force:
                # 缓存命中：主进程直接分块（快，无需子进程）——读 clean/assembled.md
                cached_md = md_path.read_text(encoding="utf-8")
                documents = _freeze_chunk_uid(
                    chunk_markdown(cached_md, pdf_file.stem, settings)
                )
                dump_documents_jsonl(documents, jsonl_path)
                entry.update({
                    "chunks": len(documents),
                    "markdown": str(md_path),
                    "jsonl": str(jsonl_path),
                    "cached": True,
                })
                logger.info(
                    "⏭ %s 命中缓存 → %d 块（跳过解析）",
                    pdf_file.name, len(documents),
                )
                summary.append(entry)
                continue
            # 全量解析：在子进程中运行，超时则 terminate（不留抢占 MinerU 的残留线程）
            result, err = _run_book_process(
                pdf_file, out_dir, force, settings, timeout_sec
            )
            if err is not None:
                raise err
            entry.update(result)
            logger.info(
                "✓ %s → %d 块 → %s / %s",
                pdf_file.name, result["chunks"],
                md_path.name, jsonl_path.name,
            )
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
            logger.error(
                "✗ %s 处理失败（已跳过，继续下一本）: %s",
                pdf_file.name, exc,
            )
        summary.append(entry)
    return summary


def _process_single_book(
    pdf_path: str | Path,
    out_dir: str | Path,
    force: bool,
    settings: Settings,
) -> Dict[str, Any]:
    """处理单本书（子进程或进程内调用）：解析 + 清洗 + 分块 + 落盘。返回 entry 字段。

    doc_name 取 ``pdf.stem``；``{stem}.assembled.md`` 写**清洗后** markdown
    （Phase A 独立可验收资产），原始解析产物另存 ``{stem}.raw.md`` 供调试。
    分块走 ``chunk_cleaned_markdown``（不再二次清洗，与旧 ``chunk_markdown``
    路径 chunk 口径一致）。
    """
    from .parser_factory import build_pdf_parser

    pdf_file = Path(pdf_path)
    raw_path, md_path, jsonl_path = _output_paths(out_dir, pdf_file.stem)

    if md_path.exists() and not force:
        # 缓存命中：已清洗的 assembled.md → 直接分块（清洗幂等，无需再清）
        cached_md = md_path.read_text(encoding="utf-8")
        documents = _freeze_chunk_uid(
            chunk_markdown(cached_md, pdf_file.stem, settings)
        )
        dump_documents_jsonl(documents, jsonl_path)
        return {
            "chunks": len(documents),
            "markdown": str(md_path),
            "jsonl": str(jsonl_path),
            "cached": True,
        }

    parser = build_pdf_parser(settings)
    raw_markdown = parser.parse(pdf_file)
    clean_md = clean_markdown(raw_markdown, settings)  # Phase A 清洗产物
    documents = _freeze_chunk_uid(
        chunk_cleaned_markdown(clean_md, pdf_file.stem, settings)
    )
    md_path.write_text(clean_md, encoding="utf-8")
    raw_path.write_text(raw_markdown, encoding="utf-8")
    dump_documents_jsonl(documents, jsonl_path)
    return {
        "chunks": len(documents),
        "markdown": str(md_path),
        "jsonl": str(jsonl_path),
    }


def _book_worker(
    pdf_path: str,
    out_dir: str,
    force: bool,
    settings: Settings,
    result_queue: Any,
) -> None:
    """子进程入口：处理单本书，结果（含异常）经队列回传。"""
    try:
        entry = _process_single_book(pdf_path, out_dir, force, settings)
        result_queue.put(entry)
    except Exception as exc:  # noqa: BLE001
        result_queue.put({"error": str(exc)})


def _run_book_process(
    pdf_file: str | Path,
    out_dir: str | Path,
    force: bool,
    settings: Settings,
    timeout_sec: int,
    *,
    worker: Any = None,
) -> tuple:
    """在独立子进程中处理单本书；超时则 terminate（彻底终止）。

    用子进程而非线程：超时后线程无法被杀，会残留后台解析继续抢占 MinerU，
    拖慢后续书。子进程 terminate 则立即释放全部资源。
    """
    import multiprocessing as mp
    import queue as _queue

    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()
    target = worker or _book_worker
    proc = ctx.Process(
        target=target,
        args=(str(pdf_file), str(out_dir), bool(force), settings, result_queue),
        daemon=True,
    )
    proc.start()
    try:
        result = result_queue.get(timeout=timeout_sec)
        proc.join(5)
        return result, None
    except _queue.Empty:
        proc.terminate()
        proc.join(10)
        return None, TimeoutError(
            f"超过 {timeout_sec}s 未完成，已跳过该本"
        )


def dump_documents_jsonl(
    documents: Sequence[Document],
    path: str | Path,
) -> int:
    """把 Document 序列化为 JSONL（page_content + metadata）。"""
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        for doc in documents:
            fh.write(json.dumps(
                {"page_content": doc.page_content, "metadata": doc.metadata},
                ensure_ascii=False,
            ) + "\n")
    return len(documents)


# ── 导入 ───────────────────────────────────────────────────


def load_documents_jsonl(path: str | Path) -> List[Document]:
    """读 JSONL → List[Document]（含固化 chunk_uid）。"""
    docs: List[Document] = []
    for line_no, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        docs.append(Document(
            page_content=record.get("page_content", ""),
            metadata=record.get("metadata") or {},
        ))
        if not docs[-1].page_content:
            raise ValueError(f"{path}: 第 {line_no + 1} 行 page_content 为空")
    return _freeze_chunk_uid(docs)


@dataclass
class HandoffIngestResult:
    """交接产物入库结果（与旧 IngestionResult 同构字段，供 __main__ 消费）。"""

    source: str
    chunk_count: int
    inserted_count: int


def ingest_documents(
    documents: Sequence[Document],
    settings: Settings,
    *,
    mode: MilvusSinkMode = "append",
    manager: Any = None,
) -> HandoffIngestResult:
    """把 Document 列表写入 Milvus（经 MilvusSink，含 chunk_uid 判重/幂等）。

    直连 MilvusSink（不再走旧 Source 流水线），chunk_uid 已在导入侧固化。
    """
    if manager is None:
        from core.embeddings import get_embeddings
        from core.vectorstore.milvus_client import MilvusStoreManager

        manager = MilvusStoreManager(settings, get_embeddings(settings))
    docs = list(documents)
    inserted = MilvusSink(manager, mode=mode).write(docs, records=[])
    return HandoffIngestResult(
        source="handoff", chunk_count=len(docs), inserted_count=inserted,
    )


def ingest_documents_jsonl(
    path: str | Path,
    settings: Settings,
    *,
    mode: MilvusSinkMode = "append",
    manager: Any = None,
) -> HandoffIngestResult:
    """从导出的 documents.jsonl 直接入库（不重新分块）。"""
    documents = load_documents_jsonl(path)
    logger.info("读取 %s → %d 个 Document", Path(path).name, len(documents))
    return ingest_documents(documents, settings, mode=mode, manager=manager)


def ingest_markdown_dir(
    markdown_dir: str | Path,
    settings: Settings,
    *,
    mode: MilvusSinkMode = "append",
    manager: Any = None,
) -> HandoffIngestResult:
    """从 ``<stem>.assembled.md`` 重新分块后入库（确定性等价于 jsonl 导入）。"""
    md_dir = Path(markdown_dir)
    files = sorted(md_dir.glob(f"*{_MARKDOWN_SUFFIX}"))
    if not files:
        raise FileNotFoundError(
            f"目录中未找到 *{_MARKDOWN_SUFFIX} 文件: {md_dir}"
        )
    all_docs: List[Document] = []
    for md_file in files:
        doc_name = md_file.name[: -len(_MARKDOWN_SUFFIX)]  # 去掉 .assembled.md
        raw = md_file.read_text(encoding="utf-8")
        all_docs.extend(
            _freeze_chunk_uid(chunk_markdown(raw, doc_name, settings))
        )
        logger.info("✓ %s → %s 块", md_file.name, len(all_docs))
    logger.info("markdown 目录共 %d 个 Document", len(all_docs))
    return ingest_documents(all_docs, settings, mode=mode, manager=manager)
