"""
Graph — StateGraph 构建与编译。

核心骨架层，负责：
  - 构建 StateGraph("agent") 主流程
  - 实现 _with_fallback 全局异常兜底
  - 注册所有业务节点和条件边
  - 注入 Checkpointer 并编译
  - 提供 AgentGraph 统一入口
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from typing import Any, Callable, Dict, Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from core.config.settings import Settings as PublicKBSettings
from core.llm.registry import create_llm_for_slot
from core.observability.metrics import record_branch_latency

from .state import AgentState
from .router import build_router_node
from .registry import CapabilityRegistry, get_default_registry
from .nodes.base import get_node_spec
from .checkpointer import create_checkpointer

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════
# 全局异常兜底包装器
# ═════════════════════════════════════════════════════════

_BRANCH_LABELS: Dict[str, str] = {
    "node_knowledge_qa":  "专业知识问答",
    "node_price_inquiry": "智能询价",
    "node_general_chat":  "通用对话",
    "node_doc_qa":        "文档问答",
}


def _accepts_config(node_fn: Callable) -> bool:
    """节点是否接受可选的 config 参数。

    LangGraph 按节点签名注入 config；`_with_fallback` 的 wrapper 固定为
    `(state, config=None)`（保证 LangGraph 总会传入），是否继续下传由
    `_accepts_config` 决定——单参节点（多数业务节点）照旧 `node_fn(state)`。
    """
    try:
        params = inspect.signature(node_fn).parameters.values()
    except (TypeError, ValueError):
        return False
    has_var = any(
        p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for p in params
    )
    positional = sum(
        p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for p in params
    )
    return has_var or positional >= 2


def _with_fallback(node_fn: Callable) -> Callable:
    """包装业务节点：任何未捕获异常 → 自动降级返回友好提示。

    包装后的节点仍然符合统一接口: (AgentState) → dict（可选 config 透传，
    供子图类节点转发 thread_id）。

    Args:
        node_fn: 原始业务节点函数

    Returns:
        包装后的节点函数
    """

    @functools.wraps(node_fn)
    def wrapped(state: AgentState, config: Optional[Dict] = None) -> dict:
        _t0 = time.perf_counter()
        try:
            if _accepts_config(node_fn):
                result = node_fn(state, config)
            else:
                result = node_fn(state)
        except Exception as e:
            node_name = getattr(node_fn, "__name__", "unknown")
            label = _BRANCH_LABELS.get(node_name, "当前")
            logger.error("节点 [%s] 执行失败: %s", node_name, e, exc_info=True)

            error_msg = (
                f"抱歉，「{label}」功能暂时不可用，请稍后重试或尝试其他问题。\n\n"
                f"您可以：\n"
                f"  - 咨询招投标法规知识\n"
                f"  - 查询历史中标价格\n"
                f"  - 进行通用对话"
            )

            record_branch_latency("fallback", (time.perf_counter() - _t0) * 1000)
            return {
                "business_result": {
                    "branch": "fallback",
                    "answer": error_msg,
                    "data": {
                        "error": str(e)[:200],
                        "failed_branch": node_name,
                    },
                },
                "messages": [AIMessage(content=error_msg)],
            }

        # 正常返回 → 记分支耗时
        branch = (result.get("business_result") or {}).get("branch", "?")
        record_branch_latency(branch, (time.perf_counter() - _t0) * 1000)
        return result

    return wrapped


# ═════════════════════════════════════════════════════════
# 路由条件函数
# ═════════════════════════════════════════════════════════

def _route_by_intent(state: AgentState) -> str:
    """条件边路由：直接返回 router_intent 枚举值。"""
    return state.get("router_intent", "fallback")


# ═════════════════════════════════════════════════════════
# 图构建
# ═════════════════════════════════════════════════════════

def build_graph(
    *,
    llm: Optional[BaseChatModel] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
    registry: Optional[CapabilityRegistry] = None,
) -> Any:
    """构建并编译 StateGraph。

    Args:
        llm: 对话模型实例。若为 None 则自动从配置创建。
        checkpointer: 检查点存储器。若为 None 则使用 MemorySaver。
        registry: 能力注册表（默认进程级）。节点与条件边按 registry 的 enabled
            能力动态注册；新增能力登记即接线，本文件零改动（解 R9）。

    Returns:
        编译后的 CompiledStateGraph，可直接 invoke。
    """
    if llm is None:
        # 默认走 models.yaml 的 router 槽位（temperature=0，与现状 create_llm 等价）
        llm = create_llm_for_slot("router", PublicKBSettings())
        logger.info("LLM 初始化: slot=router model=%s",
                     getattr(llm, "model_name", getattr(llm, "model", "?")))

    if checkpointer is None:
        checkpointer = create_checkpointer("memory")

    reg = registry or get_default_registry()
    specs = reg.enabled_specs()
    if not specs:
        raise ValueError("registry 无可路由能力（enabled_specs 为空）")

    # ── 创建图 ──
    graph = StateGraph(AgentState)

    # ── 注册节点 ──
    # Router（不需要 _with_fallback，自身有异常处理）
    router_node = build_router_node(llm, registry=reg)
    graph.add_node("router", router_node)

    # 业务节点 — 按 registry enabled 能力动态注册；fallback 本身已是兜底不包装
    path_map: Dict[str, str] = {}
    wired_nodes: set[str] = set()
    for spec in specs:
        node_name = spec.graph_node
        if node_name in wired_nodes:
            path_map[spec.id] = node_name
            continue
        ns = get_node_spec(node_name)
        if ns is None:
            raise ValueError(
                f"capability {spec.id!r} 引用的节点 {node_name!r} 未登记 NodeSpec，"
                "请在 agent.nodes.base 中 register 该节点 entry"
            )
        entry = ns.entry if node_name == "fallback" else _with_fallback(ns.entry)
        graph.add_node(node_name, entry)
        wired_nodes.add(node_name)
        path_map[spec.id] = node_name
        logger.info("动态注册节点: %s (capability=%s)", node_name, spec.id)

    # ── 边 ──
    # START → router
    graph.set_entry_point("router")

    # router → 条件边分发到业务节点（map 由 registry 生成）
    graph.add_conditional_edges("router", _route_by_intent, path_map)

    # 所有业务节点 → END（直接终止，无中间 format_output 层）
    for node_name in wired_nodes:
        graph.add_edge(node_name, END)

    # ── 编译 ──
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("StateGraph 编译完成，checkpointer=%s，capabilities=%s",
                type(checkpointer).__name__, list(spec.id for spec in specs))

    return compiled


# ═════════════════════════════════════════════════════════
# AgentGraph — 对外统一入口
# ═════════════════════════════════════════════════════════

class AgentGraph:
    """招投标智能助手 Agent 对外入口。

    Usage:
        agent = AgentGraph()
        result = agent.invoke("招标方式有哪些？")
        print(result["answer"])
    """

    def __init__(
        self,
        *,
        llm: Optional[BaseChatModel] = None,
        checkpointer_backend: str = "memory",
        registry: Optional[CapabilityRegistry] = None,
    ) -> None:
        """初始化 Agent。

        Args:
            llm: 对话模型。None 则自动创建 ChatOpenAI(deepseek-chat)。
            checkpointer_backend: 记忆后端，可选 "memory" | "sqlite" | "postgres" | "redis"
            registry: 能力注册表（默认进程级默认注册表）。
        """
        self._llm = llm
        self._registry = registry
        self._checkpointer = create_checkpointer(checkpointer_backend)
        self._graph = build_graph(
            llm=self._llm, checkpointer=self._checkpointer, registry=self._registry,
        )
        logger.info("AgentGraph 就绪")

    def invoke(self, question: str, thread_id: str = "default") -> Dict[str, Any]:
        """单次问答。

        Args:
            question: 用户问题
            thread_id: 会话线程 ID（用于多轮对话记忆隔离）

        Returns:
            {"answer": str, "intent": str, "business_result": dict}
        """
        logger.info("用户提问: %s", question[:100])

        result = self._graph.invoke(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": thread_id}},
        )

        # 提取回答文本
        messages = result.get("messages", [])
        answer = str(messages[-1].content) if messages else ""

        business_result = result.get("business_result", {})

        return {
            "answer": answer,
            "intent": result.get("router_intent", "unknown"),
            "business_result": business_result,
        }

    def stream(self, question: str, thread_id: str = "default"):
        """流式问答（生成器）。"""
        logger.info("流式提问: %s", question[:100])

        for event in self._graph.stream(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": thread_id}},
        ):
            yield event

    def get_state(self, thread_id: str = "default") -> Optional[AgentState]:
        """获取指定会话的当前状态。"""
        config = {"configurable": {"thread_id": thread_id}}
        state = self._graph.get_state(config)
        return state.values if state else None
