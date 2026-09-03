"""agent.tool_agent — 工具驱动 agent（方案 Phase 6 / Step 3 H-1）。

能力包双形态复用（D5）：既有能力既可按「子图/节点」形态接入（knowledge_qa /
price_inquiry 子图），也可按「tool 形态」挂到 agent 上自主调用（public_kb.
kb_search_as_tool / price_engine.price_query_as_tool）。本模块提供通用工具驱动
agent：把一组 LangChain tool 绑定到槽位 LLM，在 max_iterations 有界循环内让 LLM
自主决定调用并给出最终回答。business_result 含 tool_trace（可观测/测评）。

离线可测：llm / tools 均注入（fake LLM 模拟 tool_calls 序列）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 3


def _tool_map(tools: Sequence[BaseTool]) -> Dict[str, BaseTool]:
    return {t.name: t for t in tools}


def run_tool_agent(
    question: str,
    *,
    llm: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> Dict[str, Any]:
    """执行一次工具驱动 agent 循环：LLM 自主选工具调用，直至无 tool_calls 或轮次上限。

    Args:
        question: 用户问题。
        llm: 支持 bind_tools 的对话模型（DeepSeek / 测试 fake）。
        tools: LangChain tool 列表。
        system_prompt: agent 自有 system 提示词（引导工具选择）。
        max_iterations: 有界工具调用轮次（防无限循环）。

    Returns:
        {"answer": str, "tool_trace": [{name, args, result_head}], "rounds": int}
    """
    tool_map = _tool_map(tools)
    llm_tools = llm.bind_tools(tools)
    messages: List[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=question),
    ]
    tool_trace: List[Dict[str, Any]] = []
    rounds = 0
    answer = ""

    for _ in range(max_iterations):
        rounds += 1
        resp = llm_tools.invoke(messages)
        messages.append(resp)
        tool_calls = getattr(resp, "tool_calls", None) or []
        if not tool_calls:
            answer = str(resp.content or "").strip()
            break
        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args") or {}
            cid = tc.get("id") or name
            tool = tool_map.get(name)
            try:
                result = tool.invoke(args) if tool is not None else f"未知工具: {name}"
            except Exception as e:  # 工具异常不中断循环
                result = f"工具错误: {e}"
            if isinstance(result, (dict, list)):
                result_text = json.dumps(result, ensure_ascii=False, default=str)
            else:
                result_text = str(result)
            tool_trace.append({"name": name, "args": args, "result_head": result_text[:200]})
            messages.append(ToolMessage(content=result_text, tool_call_id=cid))

    if not answer:
        answer = "抱歉，本轮未能完成回答，请换个问法重试。"
    return {"answer": answer, "tool_trace": tool_trace, "rounds": rounds}


def build_tool_agent_node(
    *,
    tools: Sequence[BaseTool],
    system_prompt: str,
    llm: Optional[BaseChatModel] = None,
    llm_factory: Optional[Callable[[], BaseChatModel]] = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    agent_name: str = "tool_agent",
) -> Callable[[dict, Optional[RunnableConfig]], dict]:
    """构建工具驱动 agent 节点：(AgentState, config?) -> dict。

    Args:
        tools: LangChain tool 列表（kb_search_as_tool / price_query_as_tool 等）。
        system_prompt: agent 自有 system 提示词。
        llm / llm_factory: 二选一——直接实例（测试注入）或惰性工厂（槽位）。
        max_iterations: 有界工具调用轮次。
        agent_name: business_result.branch 标识。
    """
    if llm is None and llm_factory is None:
        raise ValueError("llm 与 llm_factory 至少提供一个")

    def node(state: dict, config: Optional[RunnableConfig] = None) -> dict:
        messages = state.get("messages", [])
        if not messages:
            return {
                "business_result": {
                    "branch": agent_name,
                    "answer": "抱歉，没有收到您的问题，请重新输入。",
                    "data": {},
                },
            }
        question = str(messages[-1].content)
        llm_ = llm if llm is not None else llm_factory()
        result = run_tool_agent(
            question, llm=llm_, tools=tools,
            system_prompt=system_prompt, max_iterations=max_iterations,
        )
        answer = result["answer"]
        logger.info("tool_agent[%s]: %d 轮 %d 次工具调用",
                    agent_name, result["rounds"], len(result["tool_trace"]))
        return {
            "business_result": {
                "branch": agent_name,
                "answer": answer,
                "data": {"tool_trace": result["tool_trace"], "rounds": result["rounds"]},
            },
            "messages": [AIMessage(content=answer)],
        }

    return node
