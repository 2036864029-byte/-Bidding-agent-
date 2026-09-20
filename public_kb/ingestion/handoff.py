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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.documents import Document

from core.contracts.chunk_ids import compute_chunk_uid
from core.config.settings import Settings
from .sinks.milvus_sink import MilvusSink, MilvusSinkMode
from .transforms import (
    SemanticChunker,
    TextCleaner,
    TypedChunker,
    detect_doc_type,
)
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


# 卷首书名/法名标题（A3-5 误吞恢复）：结构锚前以 法/条例/… 收尾或含书名号 的标题
_DOC_TITLE_SUFFIX_RE = re.compile(
    r"(法|条例|规定|办法|细则|全书|手册|问答|实务|指南|教程|解读|汇编)$"
)
_LEAD_ANCHOR_RE = re.compile(r"^第[一二三四五六七八九十百千\d]+[编部分章节条款项]")
# 编号问项/列表行不是书名（1200问 手册体开篇章的 `N.xxx？` 问项不得当书名置顶）
_LEAD_NO_ITEM_RE = re.compile(
    r"^[0-9一二三四五六七八九十百]+[、.)．]|^（[0-9一二三四五六七八九十百]+）"
)
# 沿革行（D1=B，2026-09-07）：年份 + 通过/公布/施行/修正 语义——首法前置剥离后
# 与法规名一并恢复，保证首法/中段法形态一致（中段法沿革行自然留存，不受剥离）。
_YANGE_TEXT_RE = re.compile(r"\d{4}\s*年.*(通过|公布|施行|修正)")


def _leading_doc_title_heading(markdown: str) -> Tuple[Optional[str], List[str]]:
    """A3-5 前置剥离前，取首个结构锚前形似 书名/法名 的标题文本与其后沿革行块。

    仅用于 mark_front_matter(skip=True) 误把正文开头书名标题当封面吞掉时置顶恢复
    （如《招标投标法律法规全书》开篇 `### 中华人民共和国招标投标法`）。
    匹配条件：`#` 标题文本 ≤30 字、以 法/条例/… 收尾或含书名号，且非 第X编/章/节/条
    锚、非编号问项/列表行（N.xxx？）。遇首个结构锚停止扫描。
    返回 (标题文本, 沿革行列表)；无标题 → (None, [])。沿革行 = 标题之后、首个锚之前、
    含年份+通过/公布/施行/修正 的正文行（过滤"目 录"等干扰行）。
    """
    title: Optional[str] = None
    yange: List[str] = []
    for line in markdown.split("\n"):
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", s)
        if not m:
            if title and _YANGE_TEXT_RE.search(s):
                yange.append(s)
            continue  # 前置块内的非标题正文（如 CIP 文字）——继续向上找标题
        text = m.group(2).strip()
        if _LEAD_ANCHOR_RE.match(text):
            break  # 进入正文（首结构锚）——其前无书名标题，返回 None
        if _LEAD_NO_ITEM_RE.match(text):
            continue  # 编号问项/列表行（1200问 开篇 N.xxx？）不是书名
        if title:
            break  # 标题后再遇非锚标题（如章节标题）→ 沿革行块结束
        if len(text) <= 30 and (_DOC_TITLE_SUFFIX_RE.search(text) or "《" in text):
            title = text
    return title, yange


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
           须先于 A3-2 以防交叉引用被拆成虚假条标题）→ 标题级归一（A3-1，
           **仅 external**：外部 md 无版面几何，模式级重写是唯一归一手段；
           书路径解析层已版面锚定，跳过以保留其 # 级）→
           标题行内联拆分（A3-2）→ 前置页/目录标记（A3-5，书籍路径，
           含卷首书名标题误吞恢复）→ 结构校验闭环（A4，有界修复）
         + 排版重构（A3-6，书路径 relayout_pipeline，enable_pdf_relayout）：
           目录残标题剔除 / 引用误作标题降级（R1/R2；R4/R5 已随灰度开关删除）。
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
        strip_inline_toc,
    )

    cleaner = cleaner or TextCleaner()
    cleaned = cleaner.clean(markdown)
    if settings.enable_pdf_structure:
        if settings.enable_pdf_toc_filter:
            cleaned = strip_toc_noise(cleaned)
        if external and settings.enable_external_md_normalize:
            cleaned = normalize_external_md(cleaned)  # A3-3（须先于 A3-2）
        if external:
            # 书路径解析层已产出版面锚定级（pdf_paragraph），A3-1 模式级重写会覆盖它们
            # （Q6）；仅 external 保留 A3-1——外部 md 无版面几何，模式级是唯一归一手段。
            cleaned = normalize_heading_levels_full(cleaned)  # A3-1
        cleaned = split_inline_articles(cleaned)          # A3-2
        if not external:
            # A3-5（书籍路径）：目录页标记 → 前置页标记/剥离（Phase B 消费标记）
            cleaned = mark_toc_page(cleaned)
            leading_title, leading_yange = (
                _leading_doc_title_heading(cleaned)
                if settings.pdf_skip_front_matter else (None, [])
            )
            cleaned = mark_front_matter(
                cleaned, skip=settings.pdf_skip_front_matter
            )
            if (
                leading_title is not None
                and settings.pdf_skip_front_matter
            ):
                # 仅在"文首不再以该标题作标题行"时恢复（用标题行判定，避免被正文
                # 《…法》引用里的同名子串干扰）。
                _headlines = [
                    re.sub(r"^#+\s+", "", line).strip()
                    for line in cleaned.split("\n")[:24]
                    if line.strip().startswith("#")
                ][:8]
                if leading_title not in _headlines:
                    # 前置剥离误把书名/法名标题当封面吞掉 → 置顶恢复为 H1（书根），
                    # 沿革行一并恢复（D1=B，首法/中段法一致——中段法沿革行不受剥离）。
                    head = f"# {leading_title}\n\n"
                    if leading_yange:
                        head += "\n".join(leading_yange) + "\n\n"
                    cleaned = head + cleaned.lstrip("\n")
        # A4 结构校验闭环（有界 3 轮；报告供 A5 验收脚本）。
        # rewrite_levels：书路径 False（解析层版面锚定级为准）；external True（模式级唯一归一）。
        cleaned, repair_report = repair_structure(
            cleaned, rewrite_levels=external
        )
        if settings.enable_pdf_relayout and not external:
            # A3-6 排版重构：R1/R2 兜底（R4/R5 已随灰度开关删除，解析层段落化取代）。
            from .transforms.relayout import relayout_pipeline

            cleaned = relayout_pipeline(cleaned)
        if not external:
            # P3 mini 目录剥离（连续短章节标题块，2026-09-07 用户决策：直接剥离，
            # 不保留 inline-toc 标记）——放在所有内容变换之后，作用于最终内容。
            cleaned = strip_inline_toc(cleaned)
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
    doc_type: Optional[str] = None,
) -> List[Document]:
    """Phase B：切分链路。消费清洗后 markdown。

    - 未传 doc_type 时保持原 SemanticChunker/adapt_pdf_markdown 行为（向后兼容）。
    - 传入 doc_type 时走 TypedChunker，按类型化策略分块。
    """
    if doc_type is not None:
        return TypedChunker(settings).chunk(clean_md, doc_name, doc_type)

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
    doc_type: Optional[str] = None,
) -> List[Document]:
    """兼容壳：clean_markdown → chunk_cleaned_markdown（Phase A 拆分前的完整链路）。

    保持既有调用方行为不变；新增 doc_type 透传以支持类型化分块。
    """
    cleaned = clean_markdown(markdown, settings, cleaner=cleaner)
    return chunk_cleaned_markdown(
        cleaned, doc_name, settings, chunker=chunker, doc_type=doc_type
    )


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
    pdf_dir = Path(pdf_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not pdf_dir.exists():
        raise FileNotFoundError(f"PDF 目录不存在: {pdf_dir}")

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
                    chunk_cleaned_markdown(cached_md, pdf_file.stem, settings)
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
        # 缓存命中：已清洗的 assembled.md 即 Phase A 资产 → 直接分块（不再二次清洗，
        # 避免 relayout 折行资产二次处理产生的行分组漂移）
        cached_md = md_path.read_text(encoding="utf-8")
        documents = _freeze_chunk_uid(
            chunk_cleaned_markdown(cached_md, pdf_file.stem, settings)
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
    except _queue.Empty:
        proc.terminate()
        proc.join(10)
        return None, TimeoutError(
            f"超过 {timeout_sec}s 未完成，已跳过该本"
        )
    if isinstance(result, dict) and "error" in result:
        # worker 内异常：真实错误必须浮出（曾以 KeyError 'chunks' 掩蔽根因）
        return None, RuntimeError(result["error"])
    return result, None


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
        # *.assembled.md 已是 clean 资产（Phase A 输出），直接分块（不再二次清洗——
        # 二次清洗对 relayout 折行资产可能轻微重排导致 chunk 与资产漂移）。
        clean_md = md_file.read_text(encoding="utf-8")
        doc_type = detect_doc_type(md_file).value
        all_docs.extend(
            _freeze_chunk_uid(
                chunk_cleaned_markdown(clean_md, doc_name, settings, doc_type=doc_type)
            )
        )
        logger.info("✓ %s → %s 块", md_file.name, len(all_docs))
    logger.info("markdown 目录共 %d 个 Document", len(all_docs))
    return ingest_documents(all_docs, settings, mode=mode, manager=manager)


def reclean_handoff_markdown(
    out_dir: str | Path,
    settings: Settings,
    *,
    backup: bool = True,
) -> List[Dict[str, Any]]:
    """用当前 clean_markdown（含 A3-6 relayout）从 ``raw/*.raw.md`` 重处理 clean
    资产并重算 chunks jsonl，保持 handoff 确定性契约（raw 不动）。

    用户决策（2026-09-05）：覆盖 handoff_v2/clean + 时间戳备份 + 重算 chunks。
    返回每本 summary（含 chunk 数），供验收报告对照。
    """
    import datetime as _dt
    import shutil

    out = Path(out_dir)
    raw_dir = out / _RAW_SUBDIR
    if not raw_dir.exists():
        raise FileNotFoundError(f"缺少 raw 目录: {raw_dir}")
    if backup:
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        bdir = out / f"backup_{stamp}"
        for sub in (_CLEAN_SUBDIR, _CHUNKS_SUBDIR):
            src = out / sub
            if src.exists():
                shutil.copytree(src, bdir / sub, dirs_exist_ok=True)
        logger.info("已备份旧 clean/chunks → %s", bdir)
    summary: List[Dict[str, Any]] = []
    for rp in sorted(raw_dir.glob(f"*{_RAW_SUFFIX}")):
        stem = rp.name[: -len(_RAW_SUFFIX)]
        _, md_path, jsonl_path = _output_paths(out, stem)
        raw_md = rp.read_text(encoding="utf-8")
        clean = clean_markdown(raw_md, settings)  # 含 A3-6 relayout
        md_path.write_text(clean, encoding="utf-8")
        docs = _freeze_chunk_uid(chunk_cleaned_markdown(clean, stem, settings))
        dump_documents_jsonl(docs, jsonl_path)
        logger.info("✓ %s → clean md + %d chunks", stem, len(docs))
        summary.append({
            "stem": stem,
            "markdown": str(md_path),
            "chunks": len(docs),
        })
    return summary
