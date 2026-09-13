"""
doc_qa — 文档问答节点（9.3 Track D / D-2；C1 起支持检索模式）。

两种上下文模式（同一节点，由注入文本的**块标记**区分）：

  - **检索模式**（C1，长文档）：``api/doc_context`` 在提问时对该文档做**文档内
    检索**，只把 top-K 相关块注入——不再截断，中部内容靠检索命中，字符数恒定。
    注入格式见 ``api/doc_context`` 模块文档；本节点的 ``_BLOCK_RE`` 解析出引用
    来源与相关度。
  - **全文模式**（回退，旧行为）：小文档 / 未建索引 / 检索失败 → 全文注入，
    超长时按 ``chunk_max_chars * 8`` 截断。

上下文经 SystemMessage（DOC_CONTEXT_MARKER 前缀）注入会话（D-0-2：上下文经
messages 传递，AgentState 不加字段）；本节点提取 → kb_generation 槽位生成回答。
不写公共 Milvus（会话私有索引见 ``core/doc_index``）。保持 (AgentState) -> dict 接口。
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from core.config.settings import Settings
from core.llm.registry import create_llm_for_slot
from core.streaming.sink import emit_token

from ..state import AgentState

logger = logging.getLogger(__name__)

# API 层注入文档上下文的 SystemMessage 前缀（D-0-2：上下文经 messages 传递）。
# 两种前缀对应两种强度——路由层据此决定优先级（A2/A3）：
#   - DOC_CONTEXT_MARKER   客户端**显式**传了 file_ids：最强意图信号，L0 直接走 doc_qa
#   - SESSION_DOC_MARKER   服务端按会话**自动附着**本会话文件：弱信号，L1 规则优先
DOC_CONTEXT_MARKER = "【已上传文档】"
SESSION_DOC_MARKER = "【本会话文档】"
_CONTEXT_MARKERS = (DOC_CONTEXT_MARKER, SESSION_DOC_MARKER)

#: 上下文模式（data.retrieval.mode）
MODE_RETRIEVAL = "retrieval"
MODE_FULLTEXT = "fulltext"

# ── 注入格式解析（与 api/doc_context 的 BLOCK_LINE / RETRIEVAL_HEADER 成对）──
_FILE_RE = re.compile(r"^\[文件：(.+)\]$")
_HEADER_RE = re.compile(r"^\[检索模式｜命中 (\d+)/(\d+) 块｜最高相关度 ([\d.]+)\]$")
_BLOCK_RE = re.compile(r"^\[块 (\d+)｜(\d+)/(\d+)｜(.+?)｜([\d.]+)\]$")


@dataclass
class _Block:
    chunk_index: int
    chapter: str
    score: float
    content: str


@dataclass
class _InjectedDoc:
    name: str
    mode: str = MODE_FULLTEXT
    max_score: float = 0.0
    blocks: List[_Block] = field(default_factory=list)


def parse_injected_docs(body: str) -> List[_InjectedDoc]:
    """解析注入正文 → 每个文件一段（含模式与命中块）。

    单遍扫描：``[文件：x]`` 开启一段，其后 ``[检索模式｜...]`` 标模式、
    ``[块 ...]`` 开启一个块（正文延续到下一个标记行）。无标记的正文（全文模式，
    旧格式）解析为 mode=fulltext 的空块段——与旧行为完全兼容。
    """
    docs: List[_InjectedDoc] = []
    lines = (body or "").splitlines()
    current: Optional[_InjectedDoc] = None
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _FILE_RE.match(line)
        if m:
            current = _InjectedDoc(name=m.group(1))
            docs.append(current)
            i += 1
            continue
        m = _HEADER_RE.match(line)
        if m is not None and current is not None:
            current.mode = MODE_RETRIEVAL
            current.max_score = float(m.group(3))
            i += 1
            continue
        m = _BLOCK_RE.match(line)
        if m is not None and current is not None:
            j = i + 1
            buf: List[str] = []
            while j < len(lines) and not _is_marker(lines[j].strip()):
                buf.append(lines[j])
                j += 1
            current.blocks.append(
                _Block(
                    chunk_index=int(m.group(1)),
                    chapter=m.group(4),
                    score=float(m.group(5)),
                    content="\n".join(buf).strip(),
                )
            )
            i = j
            continue
        i += 1
    return docs


def _is_marker(line: str) -> bool:
    return bool(
        _FILE_RE.match(line) or _HEADER_RE.match(line) or _BLOCK_RE.match(line)
    )


def _block_sources(doc: _InjectedDoc) -> List[dict]:
    """命中块 → legacy ``sources`` 视图（对齐 knowledge_qa 的字段契约）。"""
    out = []
    for block in doc.blocks:
        text = block.content
        out.append({
            "doc": doc.name,
            "chapter": block.chapter or "未知章节",
            "chunk_index": block.chunk_index,
            "content_snippet": text[:200] + "..." if len(text) > 200 else text,
            "score": round(block.score, 4),
        })
    return out


_DOC_QA_SYSTEM_SUFFIX = (
    "\n\n你收到用户上传的文档内容。请严格仅依据该文档内容回答用户问题；"
    "若文档内容不足以回答，请明确告知。不要编造文档中不存在的信息。"
    "\n\n文档内容可能以检索模式给出：每段以 "
    "[块 块序号｜命中序/命中总数｜章节｜相关度] 开头。"
    "请优先依据这些段落作答，并在回答中标注所依据的章节名（如「第三章 评标办法」）。"
)

_USER_TEMPLATE = """【文档内容】
{context}

用户问题：{question}"""

_NO_DOC_ANSWER = (
    "📄 当前会话没有已上传的文档。\n\n"
    "请先上传文件（如 PDF / 文本 / 标书），上传后我才能帮您分析、解读或对比文档内容。\n\n"
    "您也可以使用其他功能：\n"
    "  1. 专业知识问答 — 查询招投标法规、招标方式、评标规则等\n"
    "  2. 智能询价 — 查询历史中标价格、产品报价、公司中标记录\n"
    "  3. 通用对话 — 功能引导和操作咨询"
)

# 惰性单例（带锁，线程安全；对齐 knowledge_qa/chain 模式）
_lock = threading.Lock()
_settings: Optional[Settings] = None
_llm: Optional[BaseChatModel] = None
_prompt: Optional[ChatPromptTemplate] = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _get_llm() -> BaseChatModel:
    global _llm
    if _llm is None:
        with _lock:
            if _llm is None:
                _llm = create_llm_for_slot("kb_generation", _get_settings())
    return _llm


def _get_prompt() -> ChatPromptTemplate:
    global _prompt
    if _prompt is None:
        with _lock:
            if _prompt is None:
                s = _get_settings()
                _prompt = ChatPromptTemplate.from_messages([
                    ("system", s.system_prompt + _DOC_QA_SYSTEM_SUFFIX),
                    ("user", _USER_TEMPLATE),
                ])
    return _prompt


def reset_runtime() -> None:
    """清空惰性单例（测试/重载用）。"""
    global _settings, _llm, _prompt
    _settings = _llm = _prompt = None


def _last_human_question(messages: List[BaseMessage]) -> str:
    for m in reversed(messages or []):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


def _injected_marker(messages) -> Optional[str]:
    """本轮注入的上下文前缀（无则 None）。

    只认最后一条 HumanMessage 之前紧邻的 marker SystemMessage——避免 MemorySaver
    历史中的旧上下文跨轮残留（9.3 验收修复：file_ids 每轮显式传入才生效）。
    """
    if not messages or len(messages) < 2:
        return None
    if not isinstance(messages[-1], HumanMessage):
        return None
    prev = messages[-2]
    if not isinstance(prev, SystemMessage):
        return None
    content = str(prev.content)
    for marker in _CONTEXT_MARKERS:
        if content.startswith(marker):
            return marker
    return None


def extract_doc_context(messages) -> Tuple[List[str], str]:
    """提取**本轮注入**的文档上下文：返回 (文件名列表, 拼接后的清洗后 Markdown)。

    上下文经 SystemMessage + marker 前缀注入（D-0-2）。显式与自动附着两种前缀
    都认——节点本身不关心强度，只关心"这一轮有没有文档可用"。
    """
    names: List[str] = []
    marker = _injected_marker(messages)
    if marker is None:
        return names, ""
    body = str(messages[-2].content)[len(marker):].strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("[文件：") and stripped.endswith("]"):
            names.append(stripped[len("[文件："):-1])
    return names, body


def doc_context_kind(messages) -> Optional[str]:
    """本轮注入的文档上下文强度（路由前置判定用）。

    - ``"explicit"``：客户端显式传了 ``file_ids`` → 最强意图信号，L0 直接走 doc_qa
    - ``"session"`` ：服务端按会话自动附着 → 弱信号，让 L1 规则先判（否则上传过
      文件的会话里，"查一下 XX 项目" 会被强行拉进 doc_qa——B2 症状的反向）
    - ``None``      ：本轮没带文档
    """
    marker = _injected_marker(messages)
    if marker is None:
        return None
    return "session" if marker == SESSION_DOC_MARKER else "explicit"


def _no_doc_reply() -> dict:
    return {
        "business_result": {
            "branch": "doc_qa",
            "answer": _NO_DOC_ANSWER,
            "data": {"status": "no_doc_attached", "sources": [], "attached_docs": []},
        },
        "messages": [AIMessage(content=_NO_DOC_ANSWER)],
    }


def _context_caveats(
    retrieval_docs: List[_InjectedDoc],
    *,
    truncated: bool,
    total_chars: int,
    limit: int,
    settings: Settings,
) -> List[str]:
    """C2 兜底：明确告知"本次依据什么作答"，而不是默默答错/默默截断。"""
    notes: List[str] = []
    if retrieval_docs:
        from core.doc_index import DEFAULT_MIN_SCORE

        floor = float(
            getattr(settings, "doc_index_min_score", DEFAULT_MIN_SCORE)
            or DEFAULT_MIN_SCORE
        )
        for doc in retrieval_docs:
            if doc.blocks and doc.max_score < floor:
                notes.append(
                    f"⚠️ 在《{doc.name}》中未检索到与问题高度相关的段落"
                    f"（最高相关度 {doc.max_score:.2f}），以下回答仅供参考。"
                )
    if truncated:
        notes.append(
            f"⚠️ 文档内容较长（共 {total_chars} 字符），本次仅使用了前 {limit} 字符，"
            "后文可能未纳入作答。"
        )
    return notes


def node_doc_qa(state: AgentState, config=None) -> dict:
    """文档问答节点：读注入上下文（检索模式 / 全文模式）→ kb_generation 生成。"""
    messages = state.get("messages", [])
    question = _last_human_question(messages)
    attached, context = extract_doc_context(messages)

    if not context:
        return _no_doc_reply()

    settings = _get_settings()
    parsed = parse_injected_docs(context)
    retrieval_docs = [doc for doc in parsed if doc.blocks]
    mode = MODE_RETRIEVAL if retrieval_docs else MODE_FULLTEXT
    sources = [src for doc in retrieval_docs for src in _block_sources(doc)]

    # 防超长：全文模式的主要防线（检索模式的 top-K 块正常远低于上限）
    total_chars = len(context)
    limit = settings.chunk_max_chars * 8
    truncated = total_chars > limit
    if truncated:
        context = context[:limit]

    notes = _context_caveats(
        retrieval_docs, truncated=truncated, total_chars=total_chars,
        limit=limit, settings=settings,
    )
    data = {
        "status": "success",
        "mode": mode,
        "sources": sources,
        "attached_docs": attached,
        "retrieval": {
            "mode": mode,
            "blocks": len(sources),
            "truncated": truncated,
            "notes": notes,
        },
    }

    try:
        chain = _get_prompt() | _get_llm() | StrOutputParser()
        # W3-2：.invoke → .stream，逐 token 写入流式 sink（无 sink 时纯累积）
        parts: List[str] = []
        for chunk in chain.stream({"context": context, "question": question}):
            emit_token(chunk)
            parts.append(chunk)
        answer = "".join(parts).strip()
        if notes:
            # 兜底说明同样走流式 sink——客户端累积的 token 与终态 answer 保持一致
            suffix = "\n\n" + "\n".join(notes)
            emit_token(suffix)
            answer += suffix
    except Exception as e:
        logger.error("doc_qa 生成失败: %s", e, exc_info=True)
        answer = "抱歉，文档处理失败，请重新上传后再试。"
        data["status"] = "failed"
        return {
            "business_result": {"branch": "doc_qa", "answer": answer, "data": data},
            "messages": [AIMessage(content=answer)],
        }

    logger.info(
        "doc_qa: mode=%s 文档=%d 命中块=%d（%d 字符上下文，截断=%s）",
        mode, len(attached), len(sources), len(context), truncated,
    )
    return {
        "business_result": {"branch": "doc_qa", "answer": answer, "data": data},
        "messages": [AIMessage(content=answer)],
    }
