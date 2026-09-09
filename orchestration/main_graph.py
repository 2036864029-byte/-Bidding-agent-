"""orchestration.main_graph — Supervisor 主图（方案 Phase 6 / Step 2 G-3）。

拓扑：START → supervisor（三级路由）→ 条件边 → agent（knowledge/price 编译子图 +
general_chat/doc_qa/fallback 单函数，统一 `_stamped_agent` 包装）→ handoff_decider
→ 条件边（handoff.to 有效 → 目标 agent；pending_context → 组合终态；否则 END）。

设计要点：
  - `SupervisorState(AgentState)` 继承扩展（TypedDict 继承作 schema 已验证）：agent
    子图共享 channels 合并回父图；handoff / supervisor_route / last_agent /
    pending_context 为 supervisor 层通道，agent 子图不可见（命名空间隔离）。
  - `last_agent` 由包装器在每个 agent 完成后打标——handoff_decider 据此判断
    「谁刚完成」，避免 supervisor_route 陈旧导致 handoff 死循环。
  - handoff 上下文经 messages 传递：decider 注入 HumanMessage(sub_question)；
    pending_context 保存上一 agent 结果，终态组合为合并回答。
  - compiled subgraph 直接 add_node（已验证）：config(thread_id) 原生传递、
    qa_*/pg_* 内部键隔离；统一 _with_fallback 兜底（对 compiled subgraph 生效）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from agent.registry import CapabilityRegistry, get_default_registry
from agent.state import AgentState

from .registry_wiring import resolve_agent_nodes
from .supervisor import build_supervisor_node, decide_handoff

logger = logging.getLogger(__name__)


class SupervisorState(AgentState, total=False):
    """Supervisor 主图状态 = AgentState + 编排层通道（agent 子图不可见）。"""

    supervisor_route: str          # supervisor 三级路由决策（当前 agent）
    last_agent: str                # 刚完成作答的 agent（包装器打标）
    handoff: Optional[dict]        # 待处理交接（HandoffMeta.model_dump()；None=无）
    pending_context: Optional[dict]  # 交接发起方 business_result（终态组合用）


# ── 节点 ──────────────────────────────────────────────────────


def _last_human_question(messages) -> str:
    for m in reversed(messages or []):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


def _compose_handoff_answer(context_answer: str, final_answer: str) -> str:
    """组合合并回答：上一 agent 结果 + 政策法规解释。"""
    if not context_answer:
        return final_answer
    if not final_answer:
        return context_answer
    return f"{context_answer}\n\n【政策法规解释】\n{final_answer}"


def _handoff_decider_node(state: SupervisorState) -> dict:
    """agent 完成后的交接决策：handoff 目标有效 → 注入子问题路由；目标完成 → 组合终态。

    依赖 last_agent（谁刚完成）判定当前 agent，supervisor_route 仅兜底（首次）。
    """
    current_agent = state.get("last_agent") or state.get("supervisor_route", "")
    question = _last_human_question(state.get("messages", []))
    business_result = state.get("business_result") or {}

    new_handoff = decide_handoff(current_agent, question, business_result)
    if new_handoff is not None:
        sub_question = new_handoff.payload.get("sub_question") or question
        logger.info("supervisor handoff: %s → %s（子问题注入）", current_agent, new_handoff.to)
        return {
            "handoff": new_handoff.model_dump(),
            "pending_context": business_result,
            "messages": [HumanMessage(content=sub_question)],
        }

    # 无新交接：若本 agent 是 handoff 目标（pending_context 来自发起方）→ 组合终态
    if state.get("pending_context"):
        context_answer = (state["pending_context"] or {}).get("answer", "")
        final_answer = business_result.get("answer", "")
        combined = _compose_handoff_answer(context_answer, final_answer)
        logger.info("supervisor handoff 终态组合（context=%s → final=%s）",
                    (state["pending_context"] or {}).get("branch"),
                    business_result.get("branch"))
        return {
            "handoff": None,
            "pending_context": None,
            "business_result": {
                "branch": business_result.get("branch", "knowledge_qa"),
                "answer": combined,
                "data": {
                    **(business_result.get("data") or {}),
                    "handoff_context": state["pending_context"],
                },
            },
            "messages": [AIMessage(content=combined)],
        }

    return {"handoff": None}


# ── 构建 ──────────────────────────────────────────────────────


def _as_agent_callable(target: Any) -> Callable:
    """把 agent 目标归一为可调用节点：(state, config=None) -> dict。

    compiled subgraph（langgraph 1.2.11 不可 callable，仅有 .invoke）经适配投影到
    共享通道（business_result/messages），qa_*/pg_* 内部键不泄漏进 supervisor 终态。
    """
    if callable(target):
        return target
    if hasattr(target, "invoke"):
        def _adapter(state: dict, config: Optional[RunnableConfig] = None) -> dict:
            out = target.invoke(dict(state), config=config)
            return {
                "business_result": out.get("business_result", {}),
                "messages": out.get("messages", []),
            }

        return _adapter
    raise TypeError(f"agent 目标不可调用且无 invoke: {type(target)!r}")


def _stamped_agent(name: str, wrapped_fn: Callable) -> Callable:
    """包装 agent 节点：_with_fallback 已外包，完成后打标 last_agent（decider 用）。"""

    def agent_node(state: SupervisorState, config: Optional[RunnableConfig] = None) -> dict:
        result = wrapped_fn(state, config)
        result["last_agent"] = name
        return result

    return agent_node


def build_supervisor_graph(
    *,
    llm: Optional[Any] = None,
    checkpointer: Optional[Any] = None,
    registry: Optional[CapabilityRegistry] = None,
    knowledge_subgraph: Optional[Any] = None,
    price_subgraph: Optional[Any] = None,
    embedder: Optional[Any] = None,
    llm_router: Optional[Callable[[dict], dict]] = None,
):
    """构建并编译 Supervisor 主图（默认进程级注册表 / MemorySaver checkpointer）。

    Args:
        llm: L3 路由 LLM（None 时经 registry 槽位创建）。
        checkpointer: 默认 MemorySaver（与单图一致，thread_id 隔离）。
        registry: 能力注册表（默认进程级）—— **agent 集与可调度集合由它解析**
            （W2-1），新增能力不再改本文件。
        knowledge_subgraph / price_subgraph: 注入的编译子图（离线测试 mock 用），
            覆盖 registry 解析出的同名 agent。
        embedder / llm_router: 三级路由注入（L2 语义 / L3 LLM 分类器）。

    Returns:
        CompiledStateGraph，可直接 invoke（与单图同接口）。
    """
    from core.errors.fallback import with_fallback as _with_fallback

    reg = registry or get_default_registry()

    if llm is None:
        # 与单图一致：默认走 models.yaml 的 router 槽位（temperature=0）
        from core.config.settings import Settings as PublicKBSettings
        from core.llm.registry import create_llm_for_slot

        llm = create_llm_for_slot("router", PublicKBSettings())
        logger.info("Supervisor L3 LLM 初始化: slot=router model=%s",
                    getattr(llm, "model_name", getattr(llm, "model", "?")))

    graph = StateGraph(SupervisorState)

    # ── 查询改写节点（W3-1）：入口先归一化追问，再走 supervisor 路由 ──
    from agent.nodes.rewrite import build_rewrite_node

    graph.add_node("rewrite", build_rewrite_node())

    # ── supervisor 节点（W2-3：与 agent 节点同样纳入兜底；W3-4：内容安全前置）──
    # 安全闸门在最外层：命中（超长/有害请求）→ 短路返回安全回复，跳过三级路由，
    # 并把 supervisor_route 置为 SAFETY_BLOCKED_ROUTE 哨兵 → 条件边直达 END。
    from agent.guardrails.output import with_output_guardrails
    from agent.nodes.safety import SAFETY_BLOCKED_ROUTE, with_safety_gate

    graph.add_node(
        "supervisor",
        with_safety_gate(
            _with_fallback(build_supervisor_node(llm, reg, embedder, llm_router)),
            route_key="supervisor_route",
        ),
    )

    # ── agent 节点（W2-1：registry 驱动；编译子图仍可注入覆盖，供离线测试）──
    # W3-4：`with_output_guardrails` 外包 `_with_fallback` —— 兜底后再做输出形状校验。
    agent_nodes = resolve_agent_nodes(reg)
    if knowledge_subgraph is not None:
        agent_nodes["knowledge_qa"] = knowledge_subgraph
    if price_subgraph is not None:
        agent_nodes["price_inquiry"] = price_subgraph
    allowed = set(agent_nodes)

    def _route_from_supervisor(state: SupervisorState) -> str:
        # W3-4：安全闸门短路 → 哨兵路由到 end（不落入业务节点覆盖安全结果）
        if state.get("supervisor_route") == SAFETY_BLOCKED_ROUTE:
            return "end"
        route = state.get("supervisor_route", "fallback")
        return route if route in allowed else "fallback"

    def _route_after_decider(state: SupervisorState) -> str:
        handoff = state.get("handoff")
        if handoff and handoff.get("to") in allowed:
            return handoff["to"]
        return "end"

    for name, fn in agent_nodes.items():
        graph.add_node(
            name,
            _stamped_agent(
                name, with_output_guardrails(_with_fallback(_as_agent_callable(fn)))
            ),
        )

    # ── handoff 决策节点（W2-3：同样纳入兜底）──
    graph.add_node("handoff_decider", _with_fallback(_handoff_decider_node))

    # ── 边 ──
    graph.set_entry_point("rewrite")
    graph.add_edge("rewrite", "supervisor")
    graph.add_conditional_edges(
        "supervisor", _route_from_supervisor,
        {name: name for name in agent_nodes} | {"end": END},
    )
    for name in agent_nodes:
        graph.add_edge(name, "handoff_decider")
    graph.add_conditional_edges(
        "handoff_decider", _route_after_decider,
        {name: name for name in agent_nodes} | {"end": END},
    )

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("SupervisorStateGraph 编译完成，agents=%s", list(agent_nodes))
    return compiled
