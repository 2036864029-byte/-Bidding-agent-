"""三段式文件入库编排（方案 Phase 5 / E-8）。

对每个源文件：
  Stage A（normalize）：FormatRouter 分发 → Normalizer 规范化 → 一组
    NormalizedDocument（markdown 向量路 / table 结构路双 IR）；
  Stage B（clean/chunk）：markdown IR → TextCleaner + SemanticChunker
    （或 enable_pdf_structure 时走 adapt_pdf_markdown）→ chunk_uid 固化
    （M2 幂等键，口径与 handoff/rag_engine 一致）；
  Stage C（embed/sink）：sink.write(docs)（MilvusSink → add_documents，
    chunk_uid 判重幂等，见 E-9）。

table IR 不入向量路，仅记录（供 price 侧 SQL 消费，E-13 出消费示例）。
ledger 开启时做文档级解析前去重（E-3）；quality_score 关卡在 E-11 接入。

"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.documents import Document

from core.contracts.chunk_ids import compute_chunk_uid

from .checkpoint import STAGE_A, STAGE_C, StageCheckpoint
from .ir import NormalizedDocument, simhash
from .ledger import IngestLedger
from .normalizers import build_normalizer
from .quality import QualityGate
from .router import FormatRouter
from .transforms import detect_doc_type_or_none

logger = logging.getLogger(__name__)


@dataclass
class FileIngestionResult:
    """单个文件的三段式入库结果。"""

    path: str
    source_format: str
    status: str  # completed | skipped | failed
    reason: str = ""
    ir_count: int = 0
    table_irs: int = 0
    chunk_count: int = 0
    inserted_count: int = 0
    elapsed_ms: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)


class FileIngestionPipeline:
    """三段式文件入库：normalize → clean/chunk → embed/sink。"""

    def __init__(
        self,
        settings: Any,
        *,
        sink: Any = None,
        router: Optional[FormatRouter] = None,
        ledger: Optional[IngestLedger] = None,
        cleaner: Any = None,
        chunker: Any = None,
        checkpoint: Optional[StageCheckpoint] = None,
        resume: bool = False,
        rerun_from: Optional[str] = None,
        quality_gate: Optional[QualityGate] = None,
    ) -> None:
        self._settings = settings
        self._sink = sink  # 可为 None（预览/不导入模式）
        self._router = router or FormatRouter()
        self._ledger = ledger
        self._cleaner = cleaner
        self._chunker = chunker
        self._checkpoint = checkpoint
        self._resume = resume
        self._rerun_from = rerun_from  # "B" = 复用 A 产物重做 B+C
        self._quality_gate = quality_gate  # None = 关卡关闭

    # ── 入口 ───────────────────────────────────────────────

    def run(self, paths: Sequence[str | Path]) -> List[FileIngestionResult]:
        results = []
        for path in paths:
            results.append(self._process_one(Path(path)))
        return results

    def _process_one(self, path: Path) -> FileIngestionResult:
        t0 = time.perf_counter()
        res = FileIngestionResult(path=str(path), source_format="?", status="failed")
        try:
            res.source_format = fmt = self._router.classify(path)
            routed = self._router.route(path)
            source_hash = routed.metadata.get("source_hash", "")

            # 文档级解析前去重（E-3）
            if self._ledger is not None:
                decision = self._ledger.should_process(routed.doc_id, source_hash)
                if not decision.process:
                    res.status = "skipped"
                    res.reason = decision.reason
                    return res

            # checkpoint：--resume 跳过 C 已完成的文件
            if self._checkpoint is not None and self._resume and self._checkpoint.is_done(path, STAGE_C):
                res.status = "skipped"
                res.reason = "checkpoint_done"
                return res

            # Stage A：规范化 → IR（--rerun-from=B 复用 A 中间产物）
            if (
                self._checkpoint is not None
                and self._rerun_from == "B"
                and self._checkpoint.has_artifact(path)
            ):
                content = self._checkpoint.load_artifact(path)
                irs = [
                    NormalizedDocument(
                        doc_id=f"{routed.doc_id}#markdown",
                        source_format=fmt,
                        ir_format="markdown",
                        content=content,
                        source_path=str(path),
                        metadata={
                            "source_hash": source_hash,
                            "quality_score": 1.0,
                            "parser_version": "checkpoint-A",
                        },
                    )
                ]
                res.detail["stage_a_reused"] = True
            else:
                normalizer = build_normalizer(fmt, settings=self._settings)
                irs = normalizer.normalize(routed)
                if self._checkpoint is not None:
                    md_irs = [ir for ir in irs if ir.ir_format == "markdown"]
                    if md_irs:
                        self._checkpoint.save_artifact(path, md_irs[0].content)
                    self._checkpoint.mark(path, STAGE_A)

            table_irs = [ir for ir in irs if ir.ir_format == "table"]
            md_irs = [ir for ir in irs if ir.ir_format == "markdown"]

            # E-11 质量关卡：低质量 markdown IR 旁路 review_queue，不入库
            if self._quality_gate is not None:
                ingest_md, review_md = self._quality_gate.partition(md_irs)
                for ir in review_md:
                    self._quality_gate.dump_review(ir, path)
                if review_md:
                    res.detail["review_count"] = len(review_md)
                    res.detail["review_reason"] = "quality_below_threshold"
            else:
                ingest_md, review_md = md_irs, []

            # Stage B：清洗 + 分块 + chunk_uid 固化
            all_docs: List[Document] = []
            for ir in ingest_md:
                all_docs.extend(self._chunk_ir(ir, path))

            res.ir_count = len(irs)
            res.table_irs = len(table_irs)
            res.chunk_count = len(all_docs)
            if table_irs:
                res.detail["table_columns"] = [
                    t.metadata.get("columns") for t in table_irs
                ]
                res.detail["table_row_count"] = sum(
                    int(t.metadata.get("row_count", 0)) for t in table_irs
                )

            # 全部 markdown IR 被质量关卡旁路 → 不入库（标记 review 状态）
            if md_irs and not ingest_md:
                res.status = "review"
                res.reason = "quality_below_threshold"
                if self._checkpoint is not None:
                    self._checkpoint.mark(path, STAGE_C)
                if self._ledger is not None:
                    self._ledger.mark_done(
                        routed.doc_id, source_hash,
                        source_format=fmt, status="review",
                    )
                return res

            # Stage C：写入 sink（无 sink = 预览模式）
            if all_docs and self._sink is not None:
                res.inserted_count = self._sink.write(all_docs, records=[])

            res.status = "completed"

            if self._checkpoint is not None:
                self._checkpoint.mark(path, STAGE_C)

            if self._ledger is not None:
                sh = simhash(irs[0].content) if irs else 0
                self._ledger.mark_done(
                    routed.doc_id, source_hash, simhash=sh,
                    source_format=fmt, status="processed",
                )
        except Exception as exc:  # noqa: BLE001
            res.status = "failed"
            res.reason = str(exc)[:300]
            logger.error("文件处理失败 %s: %s", path, exc)
        finally:
            res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return res

    def _chunk_ir(self, ir: NormalizedDocument, path: Path) -> List[Document]:
        """markdown IR → 清洗 + 分块 + 元数据/幂等键固化。"""
        from core.contracts import validate_ingestion_documents

        from .handoff import chunk_markdown  # 懒加载避免 pipeline↔handoff 循环

        doc_name = path.stem  # 与 handoff 的 doc_name 口径一致（跨机器稳定）
        doc_type: Optional[str] = None
        if path.suffix.lower() == ".md":
            try:
                detected = detect_doc_type_or_none(path)
                doc_type = detected.value if detected is not None else None
            except Exception:
                doc_type = None
        docs = chunk_markdown(
            ir.content, doc_name, self._settings,
            cleaner=self._cleaner, chunker=self._chunker,
            doc_type=doc_type,
        )
        for doc in docs:
            doc.metadata["chunk_uid"] = compute_chunk_uid(
                doc.page_content, doc.metadata
            )
            doc.metadata["source_format"] = ir.source_format
            doc.metadata["quality_score"] = ir.metadata.get("quality_score", 1.0)
            doc.metadata["parser_version"] = ir.metadata.get("parser_version", "")
        return validate_ingestion_documents(docs)
