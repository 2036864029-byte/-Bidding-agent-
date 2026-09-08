# 功能：类型化 Markdown 分块器 — 法规 / 法规全书 / 问答 / 实务书（两层切分）。
"""类型化分块器（Phase B/C 优化，2026-09-08 修订版）。

两层切分：
  第 1 层（结构切分，HeadingSplitter）：按**所有** Markdown 标题层级切分，不做
    类型区分——每个标题都是切分点，块内容 = 自己的标题 + 两标题之间的正文行；
    祖先标题只进标题栈（拼 hierarchy_path），自己的标题**剥掉 ``#`` 标记只留标题
    文本**（避免 markdown 语法残渣进入向量与 LLM 上下文，标题文本本身保留可溯源）；
    两标题之间无正文 → 不产生块（内容零丢失）。
  第 2 层（超限细分，OversizeSplitter）：块正文超过类型阈值才触发，按
    「类型化次级标题 → 段落 → 句子」逐级切分，每段 1 个 chunk（child）。

doc_type 只影响四件事：前置内联提取、超限阈值、次级标题切分规则、专属元数据
（article_number / question）；**不影响第 1 层"按所有标题切分"这一主流程**。

父块一期不落行：超限块的 child 用 `parent_chunk_id={doc_id}|sec_{块序号}`
指向合成父标识（杜绝超长 parent 行），二期启用父子块时再决定父块是否入库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from langchain_core.documents import Document

from core.config.settings import Settings
from core.contracts.chunk_ids import compute_chunk_uid

from ..normalizers.pdf.pdf_structure import split_table_blocks, strip_toc_noise
from .structure import _content_level, split_inline_articles


class DocType(str, Enum):
    LAW = "law"
    LAW_COMPILATION = "law_compilation"
    QA = "qa"
    BOOK_PRACTICAL = "book_practical"


# ── 公共正则 ───────────────────────────────────────────────
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千\d]+条")
_QUESTION_RE = re.compile(r"^\d+\.\s*(.+[?？])$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；])")
# 次级标题（款项/小节）：heading 形态与 A3-3 降级后的 `- （一）` 正文列表形态都认
_ITEM_RE = re.compile(
    r"^(?:#{1,6}\s+|-\s*)(?:[（(][一二三四五六七八九十百千\d]+[）)]|"
    r"[一二三四五六七八九十百千]+、)"
)
_BOOK_SUB_RE = re.compile(
    r"^(?:#{1,6}\s+|-\s*)(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[）)]|"
    r"相关案例|案例\d|例\d|\d+[.、]\s)"
)


# ── 文档类型识别（配置表驱动）──────────────────────────────
def _path_contains(*needles: str) -> Callable[[str, str], bool]:
    return lambda posix_path, _name: any(nd in posix_path for nd in needles)


def _filename_has(*needles: str) -> Callable[[str, str], bool]:
    return lambda _posix_path, name: any(nd in name for nd in needles)


# 判定顺序：目录规则 → 文件名关键词 → 目录兜底；未命中 → book_practical 风格
# （按所有标题切分，不依赖关键词，切分正确性不受影响）。
TYPE_ROUTING: List[Tuple[Callable[[str, str], bool], DocType]] = [
    (_path_contains("cleaned_v1/markdown"), DocType.LAW),
    (_filename_has("1200问", "问答", "答疑", "问题"), DocType.QA),
    (_filename_has("实务", "手册", "指南", "教程", "解读"), DocType.BOOK_PRACTICAL),
    (_filename_has("法规全书", "汇编"), DocType.LAW_COMPILATION),
    (_path_contains("handoff"), DocType.BOOK_PRACTICAL),
]


def detect_doc_type(path: str | Path) -> DocType:
    """按文件路径/文件名识别 doc_type（TYPE_ROUTING 配置表驱动）。"""
    p = Path(path)
    posix_path = str(p).replace("\\", "/").lower()
    name = p.name.lower()
    for cond, doc_type in TYPE_ROUTING:
        if cond(posix_path, name):
            return doc_type
    return DocType.BOOK_PRACTICAL


def detect_doc_type_or_none(path: str | Path) -> Optional[DocType]:
    """仅对已知清洗来源目录返回 doc_type；其他目录返回 None。

    用于流水线：未知来源的 .md 保持旧 SemanticChunker 行为，不被意外类型化。
    """
    posix_path = str(Path(path)).replace("\\", "/").lower()
    if "cleaned_v1/markdown" in posix_path or "handoff" in posix_path:
        return detect_doc_type(path)
    return None


# ── 类型配置 ───────────────────────────────────────────────
TYPE_CONFIG = {
    DocType.LAW: {
        "threshold_key": "chunk_max_chars_law",
        "sub_split_re": _ITEM_RE,
    },
    DocType.LAW_COMPILATION: {
        "threshold_key": "chunk_max_chars_law_compilation",
        "sub_split_re": _ITEM_RE,
    },
    DocType.QA: {
        "threshold_key": "chunk_max_chars_qa",
        "sub_split_re": None,
    },
    DocType.BOOK_PRACTICAL: {
        "threshold_key": "chunk_max_chars_book_practical",
        "sub_split_re": _BOOK_SUB_RE,
    },
}


# ── 辅助函数 ───────────────────────────────────────────────
def _update_heading_stack(stack: List[str], level: int, title: str) -> None:
    target_idx = level - 1
    while len(stack) > target_idx:
        stack.pop()
    while len(stack) < target_idx:
        stack.append("")
    stack.append(title)


def _hierarchy_path(stack: Sequence[str]) -> str:
    return " / ".join(h for h in stack if h)


def _extract_article_number(text: str) -> str:
    m = _ARTICLE_RE.search(text)
    return m.group(0) if m else ""


def _extract_question(title: str) -> str:
    m = _QUESTION_RE.match(title)
    if m:
        return m.group(1).strip()
    # 无编号问题：完整问题原样保留（含问号）
    if title.endswith("?") or title.endswith("？"):
        return title.strip()
    return title.strip()


def _has_body(lines: Sequence[str]) -> bool:
    """是否含非空白正文行（仅有标题的空块不入 chunk）。"""
    return any(line.strip() for line in lines)


def _split_by_sentence(text: str, max_chars: int) -> List[str]:
    """按句子边界（。！？；）切分，保留句内换行/段落结构；单句超长时硬切。

    只在句末标点后切、不吞后续空白，句子重拼时段落空行得以保留——
    避免旧版在 ``\\n`` 处切分导致标题行与正文挤成一行的格式损耗。
    """
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            if current.strip():
                chunks.append(current.strip())
            if len(sentence) > max_chars:
                chunks.extend(
                    piece.strip() or piece
                    for piece in (
                        sentence[i : i + max_chars]
                        for i in range(0, len(sentence), max_chars)
                    )
                )
                current = ""
            else:
                current = sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _split_by_subheadings(
    text: str, subheading_re: re.Pattern, max_chars: int
) -> List[str]:
    """按次级标题切分；次级标题段仍超长则按句子切。"""
    segments: List[str] = []
    current_lines: List[str] = []
    for line in text.split("\n"):
        if subheading_re.match(line.strip()):
            if current_lines:
                segments.append("\n".join(current_lines).strip())
                current_lines = []
            current_lines.append(line)
        else:
            current_lines.append(line)
    if current_lines:
        segments.append("\n".join(current_lines).strip())

    result: List[str] = []
    for seg in segments:
        if not seg:
            continue
        if len(seg) <= max_chars:
            result.append(seg)
        else:
            result.extend(_split_by_sentence(seg, max_chars))
    return result


def _paragraph_then_sentence(text: str, max_chars: int) -> List[str]:
    """优先按段落切分；段落仍超长则按句子切。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    result: List[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            result.append(para)
        else:
            result.extend(_split_by_sentence(para, max_chars))
    return result


class OversizeSplitter:
    """第 2 层：超限块按类型细分，严格不跨边界。"""

    @staticmethod
    def split(
        text: str,
        doc_type: DocType,
        max_chars: int,
        *,
        question: str = "",
    ) -> List[str]:
        if doc_type in (DocType.LAW, DocType.LAW_COMPILATION):
            parts = _split_by_subheadings(text, _ITEM_RE, max_chars)
            if len(parts) > 1:
                return parts
            return _split_by_sentence(text, max_chars)

        if doc_type == DocType.QA:
            # text = 答案正文（调用方已传入 body_text，不含问题标题行）；
            # 子块前缀完整问题，切分预算扣掉前缀长度保证 child 仍 ≤ 阈值。
            prefix = f"问：{question}\n\n" if question else ""
            budget = max(1, max_chars - len(prefix)) if prefix else max_chars
            answer_parts = _paragraph_then_sentence(text, budget)
            if not answer_parts:
                answer_parts = [text]
            if prefix:
                return [prefix + part for part in answer_parts]
            return answer_parts

        # book_practical
        parts = _split_by_subheadings(text, _BOOK_SUB_RE, max_chars)
        if len(parts) > 1:
            return parts
        return _paragraph_then_sentence(text, max_chars)


# ── 第 1 层：结构切分 ──────────────────────────────────────
@dataclass
class _StructBlock:
    """标题层级切分产出的结构块：自己的标题 + 两标题间正文 + 标题栈快照。"""

    heading_level: int
    heading_title: str
    body_lines: List[str] = field(default_factory=list)
    stack: List[str] = field(default_factory=list)


class HeadingSplitter:
    """按所有标题层级切分；标题行 → 标题栈（元数据），正文随自己的标题行成块。

    栈用 **A3-1 语义级**（`_content_level`：章→2、条→4…）而非裸 markdown ``#``
    级数——cleaned_v1 是平级标题（法名/章都 ``#``），裸级会让章名覆盖法名。
    模式标题取固定语义级；非模式标题（法名/书名/小节名）取最近锚级+1，无锚取
    ``#`` 级。只影响栈位置，不重写文本。
    """

    def split(
        self, text: str, *, initial_stack: Optional[List[str]] = None,
        initial_anchor: Optional[int] = None,
    ) -> Tuple[List[_StructBlock], List[str], Optional[int]]:
        stack = list(initial_stack or [])
        last_anchor = initial_anchor
        blocks: List[_StructBlock] = []
        current: Optional[_StructBlock] = None

        for line in text.split("\n"):
            m = _HEADING_RE.match(line)
            if m:
                md_level = len(m.group(1))
                title = m.group(2).strip()
                level, is_anchor = _content_level(title)
                if is_anchor:
                    last_anchor = level
                elif md_level == 1:
                    # 文档根（法名/书名，md 级 1 的非模式标题）：重置为顶级，
                    # 不被 last_anchor 拉深——法规全书里每部 `# 法名` 都是新根。
                    level = 1
                else:
                    # 非模式子标题：最近锚级+1（A3-1 口径），无锚则用 md 级
                    level = (
                        min(last_anchor + 1, 6)
                        if last_anchor is not None
                        else md_level
                    )
                if current is not None and _has_body(current.body_lines):
                    blocks.append(current)
                _update_heading_stack(stack, level, title)
                current = _StructBlock(
                    heading_level=level,
                    heading_title=title,
                    body_lines=[],
                    stack=list(stack),
                )
            else:
                if current is None:
                    # 段首正文（首个标题前/表格分隔后）→ 无自己标题的预置块
                    current = _StructBlock(
                        heading_level=0,
                        heading_title="",
                        body_lines=[],
                        stack=list(stack),
                    )
                current.body_lines.append(line)

        if current is not None and _has_body(current.body_lines):
            blocks.append(current)
        return blocks, stack, last_anchor


# ── 核心分块器 ─────────────────────────────────────────────
class TypedChunker:
    """两层切分 + 类型配置驱动的 Markdown 分块器。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or Settings()

    def chunk(
        self,
        markdown_text: str,
        doc_name: str,
        doc_type: DocType | str,
        *,
        doc_id: Optional[str] = None,
        source_format: str = "text",
        effective_date: str = "",
        status: str = "",
        enable_toc_filter: bool = True,
    ) -> List[Document]:
        """切分 Markdown 文本为带完整元数据的 Document 列表。"""
        if isinstance(doc_type, str):
            doc_type = DocType(doc_type)
        if not markdown_text.strip():
            return []

        # 前置（必需）：标题内联正文提取（幂等）→ 目录剔除
        text = split_inline_articles(markdown_text)
        if enable_toc_filter:
            text = strip_toc_noise(text)

        cfg = TYPE_CONFIG[doc_type]
        max_chars = getattr(self._settings, cfg["threshold_key"])
        base_metadata = {
            "doc_id": doc_id or doc_name,
            "doc_name": doc_name,
            "doc_type": doc_type.value,
            "source_format": source_format,
            "effective_date": effective_date or "",
            "status": status or "",
            "embedding_model": self._settings.embedding_model,
            "schema_version": self._settings.collection_schema_version,
        }

        documents: List[Document] = []
        chunk_index = 0
        stack: List[str] = []
        last_anchor: Optional[int] = None

        for content, is_table in split_table_blocks(
            text, self._settings.pdf_min_table_rows
        ):
            if is_table:
                documents.append(self._make_atom(
                    content.strip(), base_metadata, chunk_index,
                    stack=stack, content_type="table",
                ))
                chunk_index += 1
                continue

            blocks, stack, last_anchor = HeadingSplitter().split(
                content, initial_stack=stack, initial_anchor=last_anchor
            )
            for block in blocks:
                docs = self._process_block(
                    block, base_metadata, doc_type, max_chars, chunk_index
                )
                documents.extend(docs)
                chunk_index += len(docs)

        return documents

    def _process_block(
        self,
        block: _StructBlock,
        base_metadata: dict,
        doc_type: DocType,
        max_chars: int,
        start_index: int,
    ) -> List[Document]:
        """单个结构块 → atom 或（超限）child 序列（一期不落 parent 行）。

        内容归一化：自己的标题用 `heading_title`（已剥 ``#`` 标记、只留标题文本），
        避免 markdown 语法残渣进入向量与 LLM 上下文；段落空行（``\\n\\n``）保留。
        """
        hierarchy = _hierarchy_path(block.stack) or base_metadata["doc_name"]
        article_number = ""
        question = ""
        if doc_type in (DocType.LAW, DocType.LAW_COMPILATION):
            article_number = _extract_article_number(block.heading_title)
        elif doc_type == DocType.QA:
            question = _extract_question(block.heading_title)

        heading_text = block.heading_title or ""
        body_text = "\n".join(block.body_lines).strip()
        block_text = "\n\n".join(p for p in (heading_text, body_text) if p)
        if not block_text:
            return []

        if len(block_text) <= max_chars:
            return [self._build_doc(
                block_text, base_metadata, hierarchy,
                article_number, question, start_index,
                chunk_type="atom", parent_chunk_id="", sequence_in_parent=0,
            )]

        # 超限 → 第 2 层细分；parent_chunk_id 指向合成父标识（不落整块 parent 行）。
        # QA 只切答案正文（body_text，不含问题标题行），子块前缀完整问题；
        # law/book 对整个块（标题+正文）按次级标题/句子细分。
        parent_key = f"{base_metadata['doc_id']}|sec_{start_index}"
        split_source = body_text if doc_type == DocType.QA else block_text
        children = OversizeSplitter.split(
            split_source, doc_type, max_chars, question=question
        )
        docs: List[Document] = []
        for seq, child_text in enumerate(children, start=1):
            if not child_text.strip():
                continue
            docs.append(self._build_doc(
                child_text.strip(), base_metadata, hierarchy,
                article_number, question, start_index + seq - 1,
                chunk_type="child", parent_chunk_id=parent_key, sequence_in_parent=seq,
            ))
        return docs

    def _build_doc(
        self,
        page_content: str,
        base_metadata: dict,
        hierarchy: str,
        article_number: str,
        question: str,
        chunk_index: int,
        *,
        chunk_type: str,
        parent_chunk_id: str,
        sequence_in_parent: int,
        content_type: str = "text",
    ) -> Document:
        meta = {
            **base_metadata,
            "chunk_index": chunk_index,
            "chapter": hierarchy,
            "hierarchy_path": hierarchy,
            "chunk_type": chunk_type,
            "parent_chunk_id": parent_chunk_id,
            "sequence_in_parent": sequence_in_parent,
            "article_number": article_number,
            "question": question,
            "content_type": content_type,
        }
        meta["chunk_uid"] = compute_chunk_uid(page_content, meta)
        # 独立块自成一组：Milvus 分组检索要求 parent_chunk_id 唯一，空值会让所有独立块塌缩为一组
        if not meta["parent_chunk_id"]:
            meta["parent_chunk_id"] = meta["chunk_uid"]
        return Document(page_content=page_content, metadata=meta)

    def _make_atom(
        self,
        text: str,
        base_metadata: dict,
        chunk_index: int,
        *,
        stack: List[str],
        content_type: str = "table",
    ) -> Document:
        hierarchy = _hierarchy_path(stack) or base_metadata["doc_name"]
        return self._build_doc(
            text, base_metadata, hierarchy, "", "", chunk_index,
            chunk_type="atom", parent_chunk_id="", sequence_in_parent=0,
            content_type=content_type,
        )


# ── 便捷函数 ───────────────────────────────────────────────
def chunk_markdown_typed(
    markdown_text: str,
    doc_name: str,
    doc_type: DocType | str | None = None,
    *,
    path: str | Path | None = None,
    settings: Optional[Settings] = None,
    **kwargs: object,
) -> List[Document]:
    """自动识别 doc_type（当未提供时）并切分。"""
    if doc_type is None:
        if path is None:
            doc_type = DocType.BOOK_PRACTICAL
        else:
            doc_type = detect_doc_type(path)
    return TypedChunker(settings).chunk(
        markdown_text, doc_name, doc_type, **kwargs
    )
