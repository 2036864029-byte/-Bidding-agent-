"""
doc_qa — 文档问答节点（9.3 Track D / D-2，替换原占位）。

「无检索」模式（D-0-3）：用户临时上传文件经 FileParseTool（ingestion/parse_tool）
解析为清洗后 Markdown，由 API 层以 SystemMessage（DOC_CONTEXT_MARKER 前缀）注入
会话（D-0-2：上下文经 messages 传递，AgentState 不加字段）；本节点提取文档上下文
→ kb_generation 槽位生成回答。不写公共 Milvus、不做向量检索（session 隔离临时
集合为后续扩展点，D-4）。保持 (AgentState) -> dict 统一接口。
"""

from __future__ import annotations

import logging
import threading
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

_DOC_QA_SYSTEM_SUFFIX = (
    "\n\n你收到用户上传的文档内容。请严格仅依据该文档内容回答用户问题；"
    "若文档内容不足以回答，请明确告知。不要编造文档中不存在的信息。"
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


def node_doc_qa(state: AgentState, config=None) -> dict:
    """文档问答节点（无检索模式）：读注入上下文 → kb_generation 生成。"""
    messages = state.get("messages", [])
    question = _last_human_question(messages)
    attached, context = extract_doc_context(messages)

    if not context:
        return _no_doc_reply()

    settings = _get_settings()
    # 防超长：无检索模式单次问答的上下文上限（按 chunk 口径截断）
    context_limit = settings.chunk_max_chars * 8
    if len(context) > context_limit:
        context = context[:context_limit]

    try:
        chain = _get_prompt() | _get_llm() | StrOutputParser()
        # W3-2：.invoke → .stream，逐 token 写入流式 sink（无 sink 时纯累积）
        parts: List[str] = []
        for chunk in chain.stream({"context": context, "question": question}):
            emit_token(chunk)
            parts.append(chunk)
        answer = "".join(parts).strip()
    except Exception as e:
        logger.error("doc_qa 生成失败: %s", e, exc_info=True)
        answer = "抱歉，文档处理失败，请重新上传后再试。"
        return {
            "business_result": {
                "branch": "doc_qa",
                "answer": answer,
                "data": {"status": "failed", "sources": [], "attached_docs": attached},
            },
            "messages": [AIMessage(content=answer)],
        }

    logger.info("doc_qa: 已依据 %d 个上传文档作答（%d 字符上下文）", len(attached), len(context))
    return {
        "business_result": {
            "branch": "doc_qa",
            "answer": answer,
            "data": {
                "status": "success",
                "sources": [],
                "attached_docs": attached,
            },
        },
        "messages": [AIMessage(content=answer)],
    }
