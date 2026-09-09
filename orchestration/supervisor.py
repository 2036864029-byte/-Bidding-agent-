"""orchestration.supervisor — Supervisor 三级路由 + handoff 决策（方案 Phase 6 / Step 2 G-2）。

三级路由级联（先命中先返回，全 miss → fallback）：
  L1 `classify_rules`      规则快路径（问候 / 项目编号 / 文档关键词，确定性，零成本）
  L2 `classify_semantic`   语义相似度（BGE-M3 嵌入 × registry examples 余弦，
                           **默认关闭**——embedder 为 API（SiliconFlow），每请求 13+ 次
                           embed 调用成本高；`SUPERVISOR_SEMANTIC_ROUTE=true` 开启，
                           离线测试注入 fake embedder）
  L3 `route_llm`           复用既有 `build_router_node`（registry 驱动 LLM 分类，
                           temperature=0，router.py:155）

handoff 决策（`decide_handoff`）在 supervisor 层，agent 子图零认知：
  price_inquiry 意图 + 法规解释关键词（法律效力/依据/为什么/解释/...）→ 转 knowledge_qa。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, List, Optional

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from agent.registry import CapabilityRegistry, get_default_registry

from .handoff import HandoffMeta, build_sub_question
from .registry_wiring import agent_ids
from .routing_rules import RoutingRules, load_rules_cached

logger = logging.getLogger(__name__)

# ── L2 语义路由开关（默认关：embedder 为 API，成本高；离线测试注入 fake）──
SEMANTIC_ROUTE_ENABLED = os.getenv("SUPERVISOR_SEMANTIC_ROUTE", "false").lower() in {
    "1", "true", "yes",
}
SEMANTIC_ROUTE_THRESHOLD = 0.5


# ── L1 规则快路径（W2-1：规则数据化 → orchestration/routing_rules.yaml）────

def classify_rules(
    question: str,
    rules: Optional[RoutingRules] = None,
) -> Optional[str]:
    """L1 规则快路径：按规则文件顺序匹配，首个命中即返回 capability id。

    只覆盖高置信快路径；模糊场景留给 L2/L3，避免误路由。
    """
    q = question.strip()
    for rule in (rules or load_rules_cached()).l1:
        if rule.max_len is not None and len(q) > rule.max_len:
            continue
        if rule.pattern.search(q):
            logger.info("supervisor L1 规则命中 rule=%s → %s", rule.id, rule.target)
            return rule.target
    return None


# ── L2 语义相似度 ─────────────────────────────────────────────

def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _get_default_embedder():
    """惰性构建默认 embedder（BGE-M3 / SiliconFlow API）。仅 L2 开启时调用。
    9.3 Track A：委托 core.embeddings 全局单例。"""
    from core.config.settings import Settings
    from core.embeddings import get_embeddings

    return get_embeddings(Settings())


def classify_semantic(
    question: str,
    registry: Optional[CapabilityRegistry] = None,
    embedder: Optional[Any] = None,
) -> Optional[str]:
    """L2 语义相似度：question 嵌入 × 各能力 examples 的最大余弦，超阈值返回 capability。

    Args:
        question: 用户问题。
        registry: 能力注册表（默认进程级）。
        embedder: 注入的 embedder（须有 embed_query(text) -> list[float]）；None 时
            惰性构建默认 BGE-M3。离线测试注入 fake 避免网络。
    """
    if not SEMANTIC_ROUTE_ENABLED and embedder is None:
        return None
    embedder = embedder or _get_default_embedder()
    reg = registry or get_default_registry()

    q_vec = embedder.embed_query(question)
    best_id, best_score = None, 0.0
    for cap in reg.enabled_specs():
        if not cap.examples:
            continue
        score = max(_cosine(q_vec, embedder.embed_query(e)) for e in cap.examples)
        if score > best_score:
            best_id, best_score = cap.id, score

    if best_id is not None and best_score >= SEMANTIC_ROUTE_THRESHOLD:
        logger.info("supervisor L2 语义路由 → %s (score=%.3f)", best_id, best_score)
        return best_id
    return None


# ── L3 LLM 分类（复用既有 registry router）───────────────────

def _build_llm_router(llm, registry: Optional[CapabilityRegistry] = None) -> Callable[[dict], dict]:
    from agent.router import build_router_node

    return build_router_node(llm, registry=registry)


# ── 三级级联 ──────────────────────────────────────────────────

def route_question(
    messages: List[BaseMessage],
    *,
    llm: Optional[Any] = None,
    registry: Optional[CapabilityRegistry] = None,
    embedder: Optional[Any] = None,
    llm_router: Optional[Callable[[dict], dict]] = None,
) -> str:
    """三级级联路由：L1 规则 → L2 语义 → L3 LLM（registry router）→ fallback。

    Args:
        messages: 对话消息（末条为用户问题；L3 走完整 history 与既有 router 一致）。
        llm: L3 用 LLM（llm_router 缺省时经 build_router_node 构建）。
        registry: 能力注册表（默认进程级）。
        embedder: L2 用（None 且开关关 → 跳过 L2）。
        llm_router: 注入的 L3 分类器（(state)->{"router_intent": str}）；离线测试注入。
    """
    question = str(messages[-1].content) if messages else ""

    rule_hit = classify_rules(question)
    if rule_hit:
        logger.info("supervisor L1 规则路由 → %s", rule_hit)
        return rule_hit

    semantic_hit = classify_semantic(question, registry, embedder)
    if semantic_hit:
        return semantic_hit

    if llm_router is None:
        if llm is None:
            logger.warning("supervisor L3 缺 llm 与 llm_router，兜底 fallback")
            return "fallback"
        llm_router = _build_llm_router(llm, registry)
    out = llm_router({"messages": messages})
    intent = out.get("router_intent", "fallback")
    logger.info("supervisor L3 LLM 路由 → %s", intent)
    return intent if intent in set(agent_ids(registry)) else "fallback"


# ── handoff 决策（supervisor 层，agent 零认知；W2-1 规则数据化）────


def decide_handoff(
    intent: str,
    question: str,
    business_result: Optional[dict] = None,
    rules: Optional[RoutingRules] = None,
) -> Optional[HandoffMeta]:
    """跨 agent 交接决策：按 routing_rules.yaml 的 handoff 规则匹配。

    规则形如 `from=price_inquiry, to=knowledge_qa, keywords=[法律效力, 依据, ...]`：
    来源 agent 匹配且问题含任一关键词 → 交接。

    Args:
        intent: 本 agent 的路由意图。
        question: 原始用户问题。
        business_result: 上一 agent 的 business_result（供后续场景扩展）。
        rules: 注入规则（测试用；缺省读 routing_rules.yaml 缓存）。
    """
    for rule in (rules or load_rules_cached()).handoff:
        if rule.from_agent != intent:
            continue
        if any(keyword in question for keyword in rule.keywords):
            logger.info(
                "supervisor handoff 决策：%s → %s（%s）",
                rule.from_agent, rule.to, rule.reason,
            )
            return HandoffMeta(
                from_agent=rule.from_agent,
                to=rule.to,
                reason=rule.reason,
                payload={
                    "sub_question": build_sub_question(question),
                    "context_answer": (business_result or {}).get("answer", ""),
                },
            )
    return None


# ── supervisor 节点 ───────────────────────────────────────────

def build_supervisor_node(
    llm: Optional[Any] = None,
    registry: Optional[CapabilityRegistry] = None,
    embedder: Optional[Any] = None,
    llm_router: Optional[Callable[[dict], dict]] = None,
) -> Callable[[dict, Optional[dict]], dict]:
    """构建 supervisor 节点（依赖闭包注入，对齐 build_router_node 模式）。

    Args:
        llm / registry / embedder / llm_router: 三级路由依赖（None 走默认，见
            route_question）；离线测试注入 fake。

    注意（W0-4）：L3 router **在构建节点时创建一次**。`build_router_node` 构造期
    会做一次 structured_output 预检测（真实 LLM 调用）；旧实现把它放在
    `route_question` 的惰性分支里，导致每个请求都重建一次 router → 每问多一次
    LLM 往返。
    """
    if llm_router is None and llm is not None:
        llm_router = _build_llm_router(llm, registry)

    def supervisor_node(state: dict, config: Optional[RunnableConfig] = None) -> dict:
        messages = state.get("messages", [])
        route = route_question(
            messages, llm=llm, registry=registry,
            embedder=embedder, llm_router=llm_router,
        )
        return {"supervisor_route": route, "router_intent": route}

    return supervisor_node


def build_default_llm_router(llm, registry=None):
    """便捷入口：构建 L3 分类器（供 supervisor_node / 测试注入）。"""
    return _build_llm_router(llm, registry)
