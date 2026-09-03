"""doc_agent — 工具驱动 agent 节点（方案 Phase 6 / Step 3 H-2）。

能力包双形态复用验证：doc_agent 把 `kb_search_as_tool()`（public_kb 法规知识）与
`price_query_as_tool()`（price_engine 结构化查询）直接挂到槽位 LLM 上自主调用，
证明能力包除「子图/节点」形态外还可按「tool 形态」复用——新 agent = 自有 prompt +
选工具组合，零改能力包内部。

本节点为声明预留（registry enabled=False，同 echo_demo 先例）：工具化复用已离线
验证；路由给真实用户待数据清理 + router golden 重生成后启用。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ..tool_agent import build_tool_agent_node

logger = logging.getLogger(__name__)

# ── 自有 system prompt（引导工具选择）────────────────────────

DOC_AGENT_SYSTEM_PROMPT = (
    "你是招投标智能助手的「文档与数据」agent。你可以调用以下工具回答用户问题：\n"
    "  1. kb_search_tool —— 检索公共知识库（法律法规、招标投标知识），返回引用块。"
    "适合法规/程序/政策解释类问题。\n"
    "  2. price_query —— 结构化业务查询（企业工商/不良记录/中标情报），返回记录列表。"
    "适合查企业信息、处罚、中标项目等问题。\n"
    "根据问题选择最合适的工具调用；如果一次调用不够，可多次调用不同工具；"
    "得到工具结果后，基于结果组织最终回答。如果工具结果为空，如实说明。"
)


def _doc_agent_tools() -> List[BaseTool]:
    from public_kb.tools import kb_search_as_tool
    from price_engine.tools import price_query_as_tool

    return [kb_search_as_tool(), price_query_as_tool()]


def _slot_llm_factory() -> BaseChatModel:
    from core.config.settings import Settings
    from core.llm.registry import create_llm_for_slot

    return create_llm_for_slot("doc_agent", Settings())


def build_doc_agent_node(
    *,
    llm: Optional[BaseChatModel] = None,
    llm_factory: Optional[Callable[[], BaseChatModel]] = None,
    tools: Optional[List[BaseTool]] = None,
) -> Callable[[dict, Optional[dict]], dict]:
    """构建 doc_agent 节点（llm/tools 可注入，离线测试用）。

    Args:
        llm / llm_factory: 直接实例或惰性工厂（默认 doc_agent 槽位）。
        tools: 注入的工具列表（默认 kb_search_as_tool + price_query_as_tool）。
    """
    factory = llm_factory if llm_factory is not None else _slot_llm_factory
    return build_tool_agent_node(
        tools=tools if tools is not None else _doc_agent_tools(),
        system_prompt=DOC_AGENT_SYSTEM_PROMPT,
        llm=llm,
        llm_factory=factory,
        agent_name="doc_agent",
    )


# ── 进程级惰性单例（registry/base 登记的 entry）─────────────

_lock = threading.Lock()
_node: Optional[Callable] = None


def _get_doc_agent_node():
    global _node
    if _node is None:
        with _lock:
            if _node is None:
                _node = build_doc_agent_node()
    return _node


def node_doc_agent(state: dict, config: Optional[dict] = None) -> dict:
    """doc_agent 节点入口（与既有节点统一接口，支持 config 透传）。"""
    return _get_doc_agent_node()(state, config)


def reset_doc_agent() -> None:
    """清空单例（测试/重载用）。"""
    global _node
    _node = None
