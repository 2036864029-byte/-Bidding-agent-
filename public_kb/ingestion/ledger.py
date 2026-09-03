"""IngestLedger — 文档级解析前去重登记（方案 Phase 5 / E-3）。

设计（实现细化，2026-09-03）：
  - **主判据 = 内容级精确去重**：登记每个源文档的字节 MD5（source_hash），
    任何新文件只要 content hash 与已成功处理的一致即跳过——跨文件名精确捕获
    "同一本书换文件名重扫"，避免重复烧 MinerU（口径与 pdf_mineru_router 一致）。
  - **doc_id 规则**：同路径重跑（同 hash=exact_dup、异 hash=changed）、
    失败重试（status != processed → retry_failed）。
  - **SimHash 仅登记留作诊断**：实测简单 SimHash 在小文档尺度对轻微改动的海明
    距离波动大（一字之差 ~11bit、随机 ~29bit），不足以作可靠跳过门；故 simhash
    写入 ledger 供排查/人工复核，不作为自动去重判据（相对原清单"MD5+SimHash
    登记"的收敛）。

持久化：`ingest_ledger.jsonl`（append + 启动全量加载）。单进程使用，非线程安全。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LedgerDecision:
    """should_process 的判定结果。"""

    process: bool
    reason: str  # "new" | "changed" | "content_dup" | "exact_dup" | "retry_failed"
    matched_doc_id: Optional[str] = None


class IngestLedger:
    """文档级去重登记表（内容哈希为主判据）。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        # doc_id → entry（最新一条）
        self._by_id: Dict[str, dict] = {}
        # source_hash → doc_id（内容登记；取该内容首次成功处理者）
        self._by_hash: Dict[str, str] = {}
        if self._path.exists():
            self._load()

    # ── 查询 ───────────────────────────────────────────────

    def should_process(self, doc_id: str, source_hash: str, simhash: int = 0) -> LedgerDecision:
        """判定该文档是否需处理。

        Args:
            doc_id: 文档标识（通常为源文件绝对路径）。
            source_hash: 源文件字节 MD5（compute_source_hash）。
            simhash: 文本 SimHash（仅登记用，不参与判定）。

        Returns:
            LedgerDecision（process=True 表示需处理，reason 说明原因）。
        """
        entry = self._by_id.get(doc_id)
        if entry is not None and entry.get("source_hash") == source_hash:
            # 同 doc_id 同内容：精确重复（成功过）或失败重试
            if entry.get("status") == "processed":
                return LedgerDecision(False, "exact_dup", matched_doc_id=doc_id)
            return LedgerDecision(True, "retry_failed")

        # 内容级去重：该字节内容已成功处理过 → 跳过（跨文件名，覆盖内容变更后撞已有内容）
        matched = self._by_hash.get(source_hash)
        if matched is not None:
            return LedgerDecision(False, "content_dup", matched_doc_id=matched)

        if entry is not None:
            # 同 doc_id 内容已变 → 需重新处理
            return LedgerDecision(True, "changed")
        return LedgerDecision(True, "new")

    # ── 登记 ───────────────────────────────────────────────

    def mark_done(
        self,
        doc_id: str,
        source_hash: str,
        *,
        simhash: int = 0,
        source_format: str = "",
        status: str = "processed",
    ) -> None:
        """写入一条登记（成功或失败均落盘，失败允许下次重试）。"""
        entry = {
            "doc_id": doc_id,
            "source_hash": source_hash,
            "simhash": int(simhash),
            "source_format": source_format,
            "status": status,
        }
        self._by_id[doc_id] = entry
        if status == "processed" and source_hash not in self._by_hash:
            self._by_hash[source_hash] = doc_id
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── 内部 ───────────────────────────────────────────────

    def _load(self) -> None:
        """加载既有登记（同 doc_id 取最新；同 hash 取首次成功者）。"""
        by_id: Dict[str, dict] = {}
        by_hash: Dict[str, str] = {}
        for line_no, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("ingest_ledger 第 %d 行损坏，跳过", line_no)
                continue
            doc_id = entry.get("doc_id")
            if not doc_id:
                continue
            by_id[doc_id] = entry
            if entry.get("status") == "processed" and entry.get("source_hash"):
                by_hash.setdefault(entry["source_hash"], doc_id)
        self._by_id = by_id
        self._by_hash = by_hash

    def __len__(self) -> int:
        return len(self._by_id)
