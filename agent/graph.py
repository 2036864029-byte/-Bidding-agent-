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

import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from core.config.settings import Settings as PublicKBSettings
from core.errors.fallback import accepts_config, with_fallback
from core.llm.registry import create_llm_for_slot

from .checkpointer import create_checkpointer
from .nodes.base import get_node_spec
from .registry import CapabilityRegistry, get_default_registry
from .router import build_router_node
from .state import AgentState

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════
# 全局异常兜底包装器（W2-1：实现已下沉 core.errors.fallback）
# ═════════════════════════════════════════════════════════
# 兼容别名保留：既有测试与调用方仍可 `from agent.graph import _with_fallback`。
# 语义见 core/errors/fallback.py —— 任何未捕获异常 → 友好降级 + 分支耗时指标。
_with_fallback = with_fallback
_accepts_config = accepts_config


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
# 模式分发（P6 / G-4）：single 单图 ↔ supervisor 多 Agent
# ═════════════════════════════════════════════════════════

def build_agent_graph(
    *,
    mode: str = "single",
    llm: Optional[BaseChatModel] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
    registry: Optional[CapabilityRegistry] = None,
) -> Any:
    """按模式构建主图。

    - "single"（默认）：单图（router → 业务节点）。
    - "supervisor"：多 Agent 主图（orchestration/，三级路由 + handoff）。

    二者共享 llm/checkpointer/registry 装配面；AGENT_MODE 环境变量可在外层切换。
    """
    if mode == "supervisor":
        from orchestration.main_graph import build_supervisor_graph  # 延迟：避免循环

        return build_supervisor_graph(
            llm=llm, checkpointer=checkpointer, registry=registry,
        )
    return build_graph(llm=llm, checkpointer=checkpointer, registry=registry)


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
        mode: Optional[str] = None,
    ) -> None:
        """初始化 Agent。

        Args:
            llm: 对话模型。None 则自动创建 ChatOpenAI(deepseek-chat)。
            checkpointer_backend: 记忆后端，可选 "memory" | "sqlite" | "postgres" | "redis"
            registry: 能力注册表（默认进程级默认注册表）。
            mode: 主图模式 "single" | "supervisor"。None 时读环境变量 AGENT_MODE
                （9.3 Track C 起默认 supervisor = 多 Agent 主图 orchestration/；
                single 为回滚路径）。
        """
        if mode is None:
            mode = os.getenv("AGENT_MODE", "supervisor").lower()
        if mode not in ("single", "supervisor"):
            logger.warning("未知 AGENT_MODE=%r，回退 supervisor", mode)
            mode = "supervisor"
        self._mode = mode
        self._llm = llm
        self._registry = registry
        self._checkpointer = create_checkpointer(checkpointer_backend)
        self._graph = build_agent_graph(
            mode=mode, llm=self._llm, checkpointer=self._checkpointer,
            registry=self._registry,
        )
        logger.info("AgentGraph 就绪（mode=%s）", mode)

    def invoke(
        self,
        question: str,
        thread_id: str = "default",
        *,
        context_docs: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> Dict[str, Any]:
        """单次问答。

        Args:
            question: 用户问题
            thread_id: 会话线程 ID（用于多轮对话记忆隔离）
            context_docs: 可选 (文件名, 清洗后 markdown) 列表，作为文档上下文
                经 SystemMessage 注入（D-0-2，doc_qa 消费；AgentState 不加字段）。

        Returns:
            {"answer": str, "intent": str, "business_result": dict}
        """
        logger.info("用户提问: %s", question[:100])

        messages = self._build_input_messages(question, context_docs)
        result = self._graph.invoke(
            {"messages": messages},
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

    def stream(
        self,
        question: str,
        thread_id: str = "default",
        *,
        context_docs: Optional[Sequence[Tuple[str, str]]] = None,
    ):
        """流式问答（生成器；context_docs 同 invoke）。"""
        logger.info("流式提问: %s", question[:100])

        messages = self._build_input_messages(question, context_docs)
        for event in self._graph.stream(
            {"messages": messages},
            config={"configurable": {"thread_id": thread_id}},
        ):
            yield event

    @staticmethod
    def _build_input_messages(
        question: str,
        context_docs: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> List[Any]:
        """构造图输入消息：文档上下文以 SystemMessage 前缀注入（D-0-2）。"""
        messages: List[Any] = []
        if context_docs:
            from agent.nodes.doc_qa import DOC_CONTEXT_MARKER

            parts = [f"[文件：{name}]\n{content}" for name, content in context_docs]
            messages.append(
                SystemMessage(content=f"{DOC_CONTEXT_MARKER}\n" + "\n\n".join(parts))
            )
        messages.append(HumanMessage(content=question))
        return messages

    def get_state(self, thread_id: str = "default") -> Optional[AgentState]:
        """获取指定会话的当前状态。"""
        config = {"configurable": {"thread_id": thread_id}}
        state = self._graph.get_state(config)
        return state.values if state else None

    def delete_thread(self, thread_id: str) -> bool:
        """删除某会话的检查点（W2-4 会话过期清理用）。

        Returns:
            是否真正删除（后端不支持时 False，不抛错）。
        """
        from .checkpointer import delete_thread as _delete_thread

        return _delete_thread(self._checkpointer, thread_id)
