"""
Router — 意图路由节点（P4 动态化：候选集/系统提示/route_* tool 由 CapabilityRegistry 驱动）。

核心设计：
  - 优先尝试 with_structured_output；若 API 不支持则回退到 Tool Calling
  - 两种方案均强制枚举输出（枚举 = registry 中 enabled 能力的 id），不解新自由文本
  - 路由原文（route_desc / route_tool_doc / examples）作为数据随 CapabilitySpec 登记，
    本模块只做「生成」，不再硬编码任何能力专属文案 —— 新增能力零改本文件（解 R9）
  - 携带最近 3 轮对话历史（6 条消息），避免指代分类失准
  - 所有异常兜底 → fallback
"""

from __future__ import annotations

import logging
from typing import List, Literal, Optional, Type

from pydantic import BaseModel, Field, create_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool

from .state import AgentState
from .registry import CapabilityRegistry, CapabilitySpec, get_default_registry

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════
# 由 registry 动态生成的三件套（prompt / tools / decision schema）
# ═════════════════════════════════════════════════════════

def build_system_prompt(caps: List[CapabilitySpec]) -> str:
    """由 enabled 能力的路由原文生成系统提示词（与冻结真值逐字节一致）。

    - 每条 "- {id}: {route_desc}"
    - 路由示例 = 各能力自带 examples 的 "用户: \"{q}\" → {id}"
    """
    lines = ["你是招投标智能助手的意图路由器。请将用户输入分类到以下意图之一：", ""]
    for c in caps:
        lines.append(f"- {c.id}: {c.route_desc}")
    lines += ["", "=== 路由示例 ==="]
    for c in caps:
        for q in c.examples:
            lines.append(f'用户: "{q}" → {c.id}')
    lines.append("")  # 原常量以换行结尾，保持字节等价
    return "\n".join(lines)


class _ReasonArgs(BaseModel):
    reason: str


def _route_stub(reason: str) -> str:  # noqa: ARG001  # 仅作分类标签，永不执行
    return "noop"


def build_route_tools(caps: List[CapabilitySpec]) -> List[BaseTool]:
    """每个 enabled 能力生成一个 route_{id} tool（name/description 与冻结真值一致）。"""
    tools: List[BaseTool] = []
    for c in caps:
        tools.append(StructuredTool.from_function(
            name=f"route_{c.id}",
            description=c.route_tool_doc or c.description,
            func=_route_stub,
            args_schema=_ReasonArgs,
        ))
    return tools


def tool_intent_map(caps: List[CapabilitySpec]) -> dict:
    """tool 名 → 意图 id。"""
    return {f"route_{c.id}": c.id for c in caps}


def build_decision_model(intents: List[str]) -> Type[BaseModel]:
    """为当前候选集动态构建 RouterDecision（intent 限制为 enabled 能力 id）。"""
    intent_type = Literal[tuple(intents)]
    return create_model(
        "RouterDecision",
        intent=(intent_type, Field(description="用户意图分类")),
        reason=(str, Field(description="分类理由，4~8 字")),
    )


# ═════════════════════════════════════════════════════════
# 路由用户模板（静态，无能力专属文案）
# ═════════════════════════════════════════════════════════

ROUTER_USER_TEMPLATE = """【对话历史】
{history}

【当前用户输入】
{user_input}

请调用对应的路由工具。结合历史判断真实意图，不确定时用 route_fallback。"""


# ═════════════════════════════════════════════════════════
# Router 节点
# ═════════════════════════════════════════════════════════

def _format_history(messages: list, max_turns: int = 3) -> str:
    """格式化最近 N 轮对话为文本。"""
    recent = messages[-(max_turns * 2):]
    if not recent:
        return "（无历史对话）"
    lines: list[str] = []
    for msg in recent:
        role = "用户" if isinstance(msg, HumanMessage) else "助手"
        lines.append(f"{role}: {str(msg.content)[:150]}")
    return "\n".join(lines)


def _route_via_tool_calling(
    llm: BaseChatModel, history_str: str, user_input: str,
    *, tools: List[BaseTool], tool_map: dict, system_prompt: str,
) -> str:
    """通过 Tool Calling 实现路由。"""
    llm_with_tools = llm.bind_tools(tools, tool_choice="required")
    response = llm_with_tools.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=ROUTER_USER_TEMPLATE.format(history=history_str, user_input=user_input)),
    ])
    tool_calls = getattr(response, "tool_calls", None) or []
    if tool_calls:
        tool_name = tool_calls[0].get("name", "")
        intent = tool_map.get(tool_name, "fallback")
        logger.info("路由(tool): intent=%s", intent)
        return intent
    logger.warning("Tool Calling 无结果，降级 fallback")
    return "fallback"


def _route_via_structured_output(
    llm: BaseChatModel, history_str: str, user_input: str,
    *, decision_model: Type[BaseModel], system_prompt: str,
) -> str:
    """通过 with_structured_output 实现路由。"""
    structured_llm = llm.with_structured_output(decision_model)
    decision = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=ROUTER_USER_TEMPLATE.format(history=history_str, user_input=user_input)),
    ])
    intent = getattr(decision, "intent", None) or "fallback"
    logger.info("路由(structured): intent=%s", intent)
    return intent


def enabled_specs(registry: Optional[CapabilityRegistry] = None) -> List[CapabilitySpec]:
    """路由候选能力（registry.enabled 且按登记顺序）。"""
    return (registry or get_default_registry()).enabled_specs()


def build_router_node(llm: BaseChatModel, *, registry: Optional[CapabilityRegistry] = None):
    """构建路由节点（候选集来自 registry，默认进程级注册表）。

    策略：预检测 structured_output 支持性，不支持则用 Tool Calling。

    Args:
        llm: 用于路由判断的 LLM（temperature=0）
        registry: 能力注册表（默认 get_default_registry()）。动态化后新增能力
            只需在 registry 登记 enabled spec，本节点自动纳入候选。

    Returns:
        router_node 函数: (AgentState) → dict
    """
    reg = registry or get_default_registry()
    caps = reg.enabled_specs()
    system_prompt = build_system_prompt(caps)
    tools = build_route_tools(caps)
    tool_map = tool_intent_map(caps)
    decision_model = build_decision_model([c.id for c in caps])

    # 预检测 structured_output 支持性
    tool_fallback = False
    try:
        test_llm = llm.with_structured_output(decision_model)
        test_llm.invoke("测试")
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("response_format", "unavailable", "not supported")):
            logger.info("Router: 使用 Tool Calling 回退")
            tool_fallback = True

    # 用 dict 包装可变状态（避免 nonlocal 问题）
    state = {"tool_fallback": tool_fallback}

    def router_node(agent_state: AgentState) -> dict:
        messages = agent_state.get("messages", [])
        if not messages:
            return {"router_intent": "fallback"}

        history_str = _format_history(messages, max_turns=3)
        user_input = str(messages[-1].content)

        try:
            if state["tool_fallback"]:
                intent = _route_via_tool_calling(
                    llm, history_str, user_input,
                    tools=tools, tool_map=tool_map, system_prompt=system_prompt,
                )
            else:
                try:
                    intent = _route_via_structured_output(
                        llm, history_str, user_input,
                        decision_model=decision_model, system_prompt=system_prompt,
                    )
                except Exception as e:
                    err = str(e).lower()
                    if any(k in err for k in ("response_format", "unavailable", "not supported")):
                        state["tool_fallback"] = True
                        logger.info("Router: 运行时切换到 Tool Calling")
                        intent = _route_via_tool_calling(
                            llm, history_str, user_input,
                            tools=tools, tool_map=tool_map, system_prompt=system_prompt,
                        )
                    else:
                        raise
            return {"router_intent": intent}
        except Exception as e:
            logger.error("路由失败 → fallback: %s", e)
            return {"router_intent": "fallback"}

    return router_node
