# 功能：上传文档 → 索引/上下文渲染 + read_file 工具（C1 胶水层）。
"""agent.doc_context — 把「会话文件」变成「喂给 agent 的上下文 / 可调用的工具」。

**分层**：本模块位于 agent 层，可同时 import ``public_kb``（分块）与 ``core``
（索引引擎 ``core.doc_index``、文件记录 ``core.filestore``）。**core 不得反向
依赖 public_kb**，故分块链条留在这里（见 ``core/doc_index.py`` 模块文档）；
api 层经本模块渲染上下文（api → agent 是既有方向，见 ``api/deps.py``）。

**两条路径**：

  检索模式（长文档）::

      提问 → 复用上传时建好的索引 → top-K 块（字符数恒定）→ 渲染块标记

  全文模式（回退）::

      小文档 / 未建索引 / 索引不可用 → 沿用「全文注入」（旧行为）

**渲染契约**（上下文经 SystemMessage 注入，AgentState 不加字段，D-0-2）：
每个文件渲染为 ``[文件：名]`` 开头的一段；检索模式在其后附机器可读头 + 块标记，
供 ``agent/nodes/doc_qa.py`` 解析出引用来源与相关度（见该模块的 ``_BLOCK_RE``）
::

    [文件：招标文件.pdf]
    [检索模式｜命中 5/12 块｜最高相关度 0.820]
    [块 12｜1/5｜第三章 评标办法｜0.820]
    <块正文>
    [块 40｜2/5｜第四章 合同条款｜0.610]
    ...

全文模式不写头（节点据上下文长度自行判定截断），保持与旧格式完全一致。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.config.settings import Settings

logger = logging.getLogger(__name__)

MODE_RETRIEVAL = "retrieval"
MODE_FULLTEXT = "fulltext"

#: 块标记：``[块 {原文块序}｜{命中序}/{命中总数}｜{章节}｜{相关度}]``
BLOCK_LINE = "[块 {idx}｜{seq}/{total}｜{chapter}｜{score:.3f}]"
#: 检索模式头：``[检索模式｜命中 {used}/{total} 块｜最高相关度 {max:.3f}]``
RETRIEVAL_HEADER = "[检索模式｜命中 {used}/{total} 块｜最高相关度 {score:.3f}]"


def chunk_record(record, settings: Settings) -> List[Any]:
    """会话文件记录 → Document 列表（跳过清洗：``FileRecord.markdown`` 已清洗）。

    ``parse_file`` 的产物是 **cleaned** markdown，故必须用 ``chunk_cleaned_markdown``
    而不是 ``chunk_markdown``——后者会再清洗一遍，属重复处理。
    """
    from public_kb.ingestion.handoff import chunk_cleaned_markdown

    return chunk_cleaned_markdown(record.markdown, record.source_name, settings)


def build_index_for(record, settings: Optional[Settings] = None) -> bool:
    """上传时为文件建索引；返回是否建成。

    小文档（块数 ≤ ``doc_index_full_context_max_chunks``）**跳过建索引**——全文
    本来就没多大，检索反而可能漏，白付一次向量化成本。失败即回退全文模式，
    不阻断上传（索引挂了只是退回旧行为）。
    """
    from core import doc_index

    settings = settings or Settings()
    if not getattr(settings, "doc_index_enabled", True):
        return False

    total = int(getattr(record, "chunk_count", 0) or 0)
    min_chunks = int(getattr(settings, "doc_index_full_context_max_chunks", 12) or 12)
    if total <= min_chunks:
        logger.debug("doc_context: %s 仅 %d 块，走全文模式，不建索引",
                     record.source_name, total)
        return False

    try:
        docs = chunk_record(record, settings)
    except Exception as exc:  # noqa: BLE001  分块失败 → 回退全文模式
        logger.warning("doc_context 分块失败（回退全文模式）: %s err=%s",
                       record.source_name, exc)
        return False

    chunks = [
        doc_index.DocChunk(
            chunk_index=i,
            chapter=str((doc.metadata or {}).get("chapter") or ""),
            content=doc.page_content,
        )
        for i, doc in enumerate(docs)
        if doc.page_content and doc.page_content.strip()
    ]
    built = doc_index.build(
        file_id=record.file_id,
        session_id=record.session_id,
        source_name=record.source_name,
        chunks=chunks,
        settings=settings,
    )
    if built is None:
        logger.info("doc_context: %s 未建索引，提问时将回退全文模式", record.source_name)
    return built is not None


def _render_retrieval(result, total_chunks: int) -> str:
    """检索结果 → 头 + 块标记 + 正文。"""
    used = len(result.hits)
    max_score = result.max_dense if result.max_dense is not None else 0.0
    parts = [RETRIEVAL_HEADER.format(used=used, total=total_chunks, score=max_score)]
    for seq, hit in enumerate(result.hits, start=1):
        parts.append(
            BLOCK_LINE.format(idx=hit.chunk_index, seq=seq, total=used,
                              chapter=hit.chapter or "未知章节", score=hit.dense)
        )
        parts.append(hit.content)
    return "\n".join(parts)


def render_for_record(
    record,
    question: str,
    settings: Optional[Settings] = None,
) -> Tuple[str, str]:
    """渲染单个文件的上下文 → ``(mode, content)``。

    走检索需同时满足：索引开关开、文档块数超过「小文档阈值」、索引已建。
    任何一步不满足即回退全文模式——**回退是预期路径，不是异常**。

    **不现场建索引**：本函数在提问链路上（`/chat` 请求路径），建索引实测
    60 块 ≈ 3.7s，会让 p95 出现尖刺。现场建只允许在 `read_file` 工具里做
    （agent 路径本就慢，且那是索引缺失后唯一的自愈机会）。
    """
    read = _read_record(record, question, settings)
    if read.mode == MODE_RETRIEVAL:
        return MODE_RETRIEVAL, _render_retrieval(read.result, read.total_chunks)
    return MODE_FULLTEXT, record.markdown


@dataclass
class _RecordRead:
    """一次「读会话文件」的结果（渲染与工具共用同一判定链）。"""

    record: Any
    mode: str
    total_chunks: int
    result: Any = None              # 检索模式下的 core.doc_index.SearchResult
    built_on_demand: bool = False   # 是否因索引缺失而现场补建

    @property
    def low_confidence(self) -> bool:
        return bool(self.result is not None and self.result.low_confidence)

    @property
    def max_score(self) -> float:
        if self.result is None or self.result.max_dense is None:
            return 0.0
        return float(self.result.max_dense)


def _read_record(
    record,
    question: str,
    settings: Optional[Settings] = None,
    *,
    allow_build: bool = False,
    top_k: Optional[int] = None,
) -> _RecordRead:
    """判定「该走检索还是全文」并取回内容（`render_for_record` 与工具的唯一判定链）。

    ``allow_build=True`` 时索引缺失则现场补建一次（工具路径）；否则只读。
    """
    from core import doc_index

    settings = settings or Settings()
    total = int(getattr(record, "chunk_count", 0) or 0)
    read = _RecordRead(record=record, mode=MODE_FULLTEXT, total_chunks=total)

    if not getattr(settings, "doc_index_enabled", True):
        return read

    min_chunks = int(getattr(settings, "doc_index_full_context_max_chunks", 12) or 12)
    # 小文档全文本来就装得下，检索反而可能漏掉上下文，直接走全文
    if total <= min_chunks:
        return read

    try:
        result = doc_index.search_file(
            record.file_id, record.session_id, question,
            top_k=top_k, settings=settings,
        )
        if result is None and allow_build:
            # 索引缺失（上传时向量化失败 / 记录早于本功能）→ 现场补建后重试一次
            logger.info("doc_context: %s 无索引，现场补建", record.source_name)
            read.built_on_demand = build_index_for(record, settings)
            result = doc_index.search_file(
                record.file_id, record.session_id, question,
                top_k=top_k, settings=settings,
            )
    except Exception as exc:  # noqa: BLE001  检索/建索引异常 → 回退全文模式
        logger.warning("doc_context 读取失败（回退全文模式）: %s err=%s",
                       record.source_name, exc)
        return read

    if result is None or result.empty:
        return read

    read.mode = MODE_RETRIEVAL
    read.result = result
    logger.info(
        "doc_context 检索: %s 命中 %d/%d 块 max_dense=%.3f low_conf=%s%s %dms",
        record.source_name, len(result.hits), total, result.max_dense or 0.0,
        result.low_confidence, "（现场建索引）" if read.built_on_demand else "",
        result.elapsed_ms,
    )
    return read


def context_docs(
    records: Sequence[Any],
    question: str,
    *,
    kind: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> List[Tuple[str, str, str]]:
    """一组文件记录 → ``(文件名, 上下文, kind)`` 列表（graph 注入用）。

    单个文件渲染失败不影响其余文件（逐个兜底到全文）。
    """
    settings = settings or Settings()
    out: List[Tuple[str, str, str]] = []
    for record in records:
        try:
            _mode, content = render_for_record(record, question, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("doc_context 渲染异常，按全文注入: %s err=%s",
                           getattr(record, "source_name", "?"), exc)
            content = record.markdown
        entry: Tuple[str, str, str] = (record.source_name, content, kind or "explicit")
        out.append(entry)
    return out


# ── 工具形态：read_file（工具是唯一入口，见 Phase G-6）───────────────
#
# 两个触发者共用同一份判定链（`_read_record`）：
#   - 上传时（系统预热）：`build_index_for` → 落索引
#   - 提问时（agent 自主）：`read_file` → 命中秒回 / 未命中现场建索引
#
# **为什么是文件名而不是 file_id**：agent 看到的是注入上下文里的 `[文件：名]`，
# 拿不到 file_id；而文件名在当前会话内可确定性解析（重名时让 LLM 用全名重试，
# 不静默挑一个）。会话作用域经 contextvar 还原，租户隔离不放松（W0-6）。


def _current_session_scope() -> Optional[str]:
    """当前请求的**内部会话作用域**（与 FileRecord.session_id 同一口径）。

    经 contextvar 还原：API 层 `set_session_id(原始 id)` + 中间件 `set_tenant_id`，
    作用域 = ``tenant_scoped(原始 id)``（见 api/server.py 的 session_scope）。
    工具在节点内执行，与请求同上下文（SSE 经 copy_context 带入），故可还原。
    """
    from core.observability.context import get_session_id, tenant_scoped

    session_id = get_session_id()
    return tenant_scoped(session_id) if session_id else None


def _pick_record(records: Sequence[Any], file_name: str) -> Tuple[Optional[Any], str]:
    """按文件名挑**单一**记录 → ``(记录, 错误说明)``。

    宽松匹配依次：精确 → 忽略大小写 → 去扩展名 → 唯一子串。**多义即报错**
    （列出候选）——静默挑一个会让 agent 读到错误的文件却不自知。

    ⚠️ 只处理"**指定了**文件名"的情形。留空 = 在全部文件内检索，
    由 `read_session_file` 直接处理（不再回落"最近上传"）——见该函数文档。
    """
    name = (file_name or "").strip()
    if not name:
        return None, "未指定文件名"
    lowered = name.lower()
    for rec in records:
        if rec.source_name == name:
            return rec, ""
    for rec in records:
        if rec.source_name.lower() == lowered:
            return rec, ""
    stem = lowered.rsplit(".", 1)[0]
    for rec in records:
        if rec.source_name.lower().rsplit(".", 1)[0] == stem:
            return rec, ""
    hits = [r for r in records if lowered in r.source_name.lower()]
    if len(hits) == 1:
        return hits[0], ""
    if len(hits) > 1:
        return None, f"「{name}」匹配到多个文件，请用完整文件名"
    return None, f"未找到文件「{name}」"


def _available_names(records: Sequence[Any]) -> List[str]:
    return [getattr(r, "source_name", "?") for r in records]


def session_file_names(messages) -> List[str]:
    """本轮注入上下文的文件名（deep_agent 规划时需要知道有哪些文件可读）。

    口径复用 ``agent.nodes.doc_qa.extract_doc_context``（与路由/doc_qa 同源）。
    """
    try:
        from .nodes.doc_qa import extract_doc_context

        names, _body = extract_doc_context(messages)
        return names
    except Exception:  # noqa: BLE001  解析失败不影响主流程
        return []


def read_session_file(
    question: str,
    *,
    file_name: str = "",
    top_k: int = 0,
    records: Optional[Sequence[Any]] = None,
    settings: Optional[Settings] = None,
) -> dict:
    """读取本会话文件 → 供 agent 消费的结构化结果（**工具的实现体**）。

    返回**对齐 deep_agent 观察摘要契约**的 ``chunks`` 形状（与 kb_search 同字段），
    使现成的 ``_summarize_result`` / ``_count_records`` 直接可用，不必改执行器。

    Args:
        question: 检索问题（也是工具主参数，deep_agent 执行器固定传 ``question=``）。
        file_name: 目标文件名；**留空 → 在会话全部文件内检索**（见下）。
        top_k: 返回块数上限；0 → 用 ``doc_index_top_k`` 配置。
        records: 注入记录列表（测试用；默认按当前会话作用域查）。

    **留空 = 全部文件**（调研文档 §8.3）：原先留空取"最近上传的那一个"，导致
    多文件问题必然失败——规划器通常不传名字，连续两次调用拿回**同一份**文件
    （实测原话："只返回了《道路工程》的工期条款，未返回《智慧园区》的任何工期信息"）。
    工具默认行为应当对"会话里有几份文件"自适应，而不是依赖规划器记得传参。

    **算力公平分配而非全局排序**：不同文档的余弦分**不可比**，故多文件时按
    ``max(2, ceil(top_k / 文件数))`` 给每份文件配额，各自取自己的 top-k，
    避免某一份文件的高分把其他文件整份挤掉。
    """
    from core import doc_index, filestore

    settings = settings or Settings()
    if records is None:
        scope = _current_session_scope()
        if scope is None:
            # CLI/测试等无会话上下文的入口：明确报错，而不是读到别人的文件
            return {
                "chunks": [],
                "error": "当前请求没有会话上下文，无法定位会话文件",
                "available_files": [],
            }
        records = filestore.list_by_session(scope)

    if not records:
        return {"chunks": [], "error": "本会话没有已上传的文件", "available_files": []}

    if (file_name or "").strip():
        record, err = _pick_record(records, file_name)
        if record is None:
            return {"chunks": [], "error": err, "available_files": _available_names(records)}
        targets = [record]
    else:
        targets = list(records)

    k = int(top_k) if int(top_k or 0) > 0 else int(
        getattr(settings, "doc_index_top_k", 6) or 6
    )
    per_file_k = max(2, -(-k // len(targets)))   # ceil(k / n)，下限 2

    notes: List[str] = []
    chunks: List[Dict[str, Any]] = []
    searched: List[Dict[str, Any]] = []
    total_chunks = 0
    built_on_demand = False
    any_retrieval = False
    last_read: Any = None
    last_truncated = False

    for record in targets:
        read = _read_record(record, question, settings, allow_build=True, top_k=per_file_k)
        last_read = read
        total_chunks += read.total_chunks
        built_on_demand = built_on_demand or read.built_on_demand

        if read.mode == MODE_RETRIEVAL:
            any_retrieval = True
            for hit in read.result.hits:
                chunks.append({
                    "content": hit.content,
                    "doc_name": record.source_name,
                    "chapter": hit.chapter,
                    "chunk_index": hit.chunk_index,
                    "score": round(hit.dense, 4),
                })
            searched.append({
                "file": record.source_name, "mode": read.mode,
                "matched": len(read.result.hits), "max_score": round(read.max_score, 4),
            })
            if read.low_confidence:
                floor = float(
                    getattr(settings, "doc_index_min_score", doc_index.DEFAULT_MIN_SCORE)
                    or doc_index.DEFAULT_MIN_SCORE
                )
                notes.append(
                    f"《{record.source_name}》检索到的段落相关度较低"
                    f"（最高 {read.max_score:.2f} < {floor:.2f}），可能没有该问题的答案"
                )
        else:
            content, truncated = _cap_text(record.markdown, settings)
            last_truncated = truncated
            chunks.append({
                "content": content,
                "doc_name": record.source_name,
                "chapter": "",
                "chunk_index": 0,
                "score": 0.0,
            })
            searched.append({"file": record.source_name, "mode": read.mode, "matched": 1})
            if truncated:
                notes.append(
                    f"《{record.source_name}》较长（共 {len(record.markdown)} 字符），"
                    f"仅返回了前 {len(content)} 字符"
                )

    out: Dict[str, Any] = {
        "mode": MODE_RETRIEVAL if any_retrieval else MODE_FULLTEXT,
        "chunks": chunks,
        "matched": len(chunks),
        "total_chunks": total_chunks,
        "available_files": _available_names(records),
        "files_searched": searched,
    }
    if len(targets) == 1:
        # 单文件保持原契约（既有调用方与测试依赖这些顶层字段）
        out["file"] = targets[0].source_name
        out["max_score"] = round(last_read.max_score, 4) if last_read else 0.0
        out["low_confidence"] = bool(last_read.low_confidence) if last_read else False
        if not any_retrieval:
            out["truncated"] = last_truncated
    else:
        notes.append(f"未指定文件名，已在全部 {len(targets)} 份文件内检索")

    if built_on_demand:
        notes.append("有文件此前未建立索引，本次已现场建立（首问较慢）")
    if notes:
        out["note"] = "；".join(notes)
    return out


def _cap_text(text: str, settings: Settings) -> Tuple[str, bool]:
    """全文模式的上下文上限（与 doc_qa 节点同一口径：``chunk_max_chars * 8``）。"""
    limit = int(getattr(settings, "chunk_max_chars", 2000) or 2000) * 8
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def read_file_as_tool():
    """LangChain tool 形态的 read_file（供 agent 挂载；**只建不接线**）。

    与 ``kb_search_as_tool`` / ``price_query_as_tool`` 同一形态。主参数为
    ``question``（deep_agent 执行器固定注入 ``question=step.query``）。
    """
    from langchain_core.tools import tool

    @tool
    def read_file(question: str, file_name: str = "", top_k: int = 0) -> dict:
        """读取**用户本次会话上传的文件**内容（不是公共知识库）。

        长文档会自动检索出与该问题最相关的若干段落后返回；短文档直接返回全文。

        Args:
            question: 要在这个文件里查什么（用自然语言描述）。
            file_name: 目标文件名；**留空 = 在本会话全部文件内检索**。
                只有一个文件时留空即可；问题明确指向某一份（或需要逐份指名）时才传。
                可用文件名见返回的 available_files。
            top_k: 返回段落数上限；0 表示用系统默认。

        Returns:
            dict：{mode, total_chunks, chunks, matched, max_score, low_confidence,
            available_files, files_searched, note?}。chunks 每项含
            content / doc_name / chapter / chunk_index / score ——
            **多文件时靠 doc_name 区分块来自哪一份**。
        """
        return read_session_file(question, file_name=file_name, top_k=top_k)

    return read_file


__all__ = [
    "MODE_RETRIEVAL",
    "MODE_FULLTEXT",
    "BLOCK_LINE",
    "RETRIEVAL_HEADER",
    "chunk_record",
    "build_index_for",
    "render_for_record",
    "context_docs",
    "session_file_names",
    "read_session_file",
    "read_file_as_tool",
]
