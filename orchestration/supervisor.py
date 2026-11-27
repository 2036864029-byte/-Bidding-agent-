"""orchestration.supervisor — Supervisor 三级路由 + handoff 决策（方案 Phase 6 / Step 2 G-2）。

三级路由级联（先命中先返回，全 miss → fallback）：
  L0 `_doc_context_kind`
      **显式**（客户端传 file_ids）→ 文档问答，压过 L1/L2/L3
      **自动附着**（服务端按会话带文件）→ 让过 L1 后生效（A3）
  L1 `classify_rules`      规则快路径（问候 / 项目编号 / 文档关键词，确定性，零成本）
  L2 `classify_semantic`   语义相似度（BGE-M3 嵌入 × registry examples 余弦，
                           **默认开启**——能力示例向量进程内预计算一次缓存，
                           单请求只剩 1 次嵌入（问题本身），成本与一次路由调用同量级；
                           embedder 不可用时自动降级 L3，不阻断路由；
                           `SUPERVISOR_SEMANTIC_ROUTE=false` 可关闭）
  L3 `route_llm`           复用既有 `build_router_node`（registry 驱动 LLM 分类，
                           temperature=0，router.py:155）

handoff 决策（`decide_handoff`）在 supervisor 层，agent 子图零认知：
  price_inquiry 意图 + 法规解释关键词（法律效力/依据/为什么/解释/...）→ 转 knowledge_qa。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from agent.registry import (
    CapabilityRegistry,
    get_default_registry,
    is_capability_enabled,
)

from .handoff import HandoffMeta, build_sub_question
from .registry_wiring import agent_ids
from .routing_rules import RoutingRules, load_rules_cached

logger = logging.getLogger(__name__)

# ── L2 语义路由开关（默认开：能力向量预计算，单请求 1 次嵌入）──
# 关闭条件：`SUPERVISOR_SEMANTIC_ROUTE=false`（严格省成本 / 无网环境）。
# 注意：这是**导入期**常量，测试改它请 monkeypatch 模块属性。
SEMANTIC_ROUTE_ENABLED = os.getenv("SUPERVISOR_SEMANTIC_ROUTE", "true").lower() in {
    "1", "true", "yes",
}
SEMANTIC_ROUTE_THRESHOLD = 0.5

#: 能力示例向量缓存：key=(embedder 身份, 能力集签名) → (embedder 引用, [向量...])。
#: 存 embedder 引用是为了防 `id()` 复用（对象被回收后新对象拿到同一 id 会误命中）。
_EXAMPLE_VECTORS: Dict[Tuple, Tuple[Any, List[Tuple[str, List[float]]]]] = {}
_EXAMPLE_VECTORS_LOCK = threading.Lock()


def reset_semantic_cache() -> None:
    """清空示例向量缓存（测试 / 热更新 registry examples 后调用）。"""
    with _EXAMPLE_VECTORS_LOCK:
        _EXAMPLE_VECTORS.clear()


def _registry_signature(registry: CapabilityRegistry) -> Tuple:
    """能力集签名：id + 示例条数 + 首例文本（examples 改了就自动失效）。"""
    return tuple(
        (spec.id, len(spec.examples), spec.examples[0] if spec.examples else "")
        for spec in registry.enabled_specs()
    )


def _capability_vectors(
    registry: CapabilityRegistry, embedder: Any
) -> List[Tuple[str, List[float]]]:
    """取（或首次构建）能力示例向量表：**每个示例只嵌入一次**。

    why：原实现每次请求都为全部能力示例重新嵌入（6 能力 × 2~3 示例 ≈ 14 次 API
    调用），成本高到只能默认关闭语义路由。示例是静态数据，向量本可复用 ——
    预计算后单请求只剩 1 次嵌入，语义层才有资格默认开启。
    """
    key = (type(embedder).__module__, type(embedder).__qualname__, id(embedder),
           _registry_signature(registry))
    with _EXAMPLE_VECTORS_LOCK:
        cached = _EXAMPLE_VECTORS.get(key)
    if cached is not None and cached[0] is embedder:
        return cached[1]

    t0 = time.perf_counter()
    vectors: List[Tuple[str, List[float]]] = []
    for spec in registry.enabled_specs():
        for example in spec.examples:
            vectors.append((spec.id, embedder.embed_query(example)))
    with _EXAMPLE_VECTORS_LOCK:
        _EXAMPLE_VECTORS[key] = (embedder, vectors)
    logger.info(
        "语义路由示例向量预计算: %d 条 / %d 能力 / %.2fs（此后单请求仅 1 次嵌入）",
        len(vectors), len(registry.enabled_specs()), time.perf_counter() - t0,
    )
    return vectors


# ── L0 文档上下文（B2/A3：本轮带了上传文件 → 走 doc_qa）──────────

#: L0 路由目标（能力 id）。doc_qa 未启用时 L0 自动失效，退回三级级联。
DOC_CONTEXT_ROUTE = "doc_qa"


def _doc_context_kind(
    messages: List[BaseMessage], registry: Optional[CapabilityRegistry]
) -> Optional[str]:
    """本轮文档上下文的强度（doc_qa 不可调度时一律 None）。

    判定口径复用 `agent.nodes.doc_qa.doc_context_kind`（与 doc_qa 节点同源），
    避免路由说"有文档"、节点却读不出上下文的自相矛盾。
    """
    from agent.nodes.doc_qa import doc_context_kind

    if DOC_CONTEXT_ROUTE not in set(agent_ids(registry)):
        return None
    return doc_context_kind(messages)


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

    成本：示例向量走进程内缓存，**本函数每请求只做 1 次嵌入**（问题）。
    容错：嵌入失败 / 无示例 → 返回 None（由调用方降级到 L3），不阻断路由。
    """
    if not SEMANTIC_ROUTE_ENABLED and embedder is None:
        return None
    embedder = embedder or _get_default_embedder()
    reg = registry or get_default_registry()

    try:
        q_vec = embedder.embed_query(question)
        vectors = _capability_vectors(reg, embedder)
    except Exception as exc:  # noqa: BLE001  嵌入不可用 → 让过 L2，交给 L3
        logger.warning("语义路由不可用，降级 L3: %s", exc)
        return None

    best_id, best_score = None, 0.0
    for cap_id, vec in vectors:
        score = _cosine(q_vec, vec)
        if score > best_score:
            best_id, best_score = cap_id, score

    if best_id is not None and best_score >= SEMANTIC_ROUTE_THRESHOLD:
        logger.info("supervisor L2 语义路由 → %s (score=%.3f)", best_id, best_score)
        return best_id
    return None


# ── L3 LLM 分类（复用既有 registry router）───────────────────

def _build_llm_router(llm, registry: Optional[CapabilityRegistry] = None) -> Callable[[dict], dict]:
    from agent.router import build_router_node

    return build_router_node(llm, registry=registry)


# ── 三级级联 ──────────────────────────────────────────────────

AGENT_UPGRADE_ROUTE = "deep_agent"


def should_upgrade_to_agent(
    question: str,
    *,
    rules: Optional[RoutingRules] = None,
    registry: Optional[CapabilityRegistry] = None,
) -> bool:
    """闸门判定：**会话已有文件时**，是否把问题升级给 deep_agent（第 3 项）。

    纯确定性，零 LLM：关键词命中（可选再要求"问题指向本文档"）。
    规则与词表在 `routing_rules.yaml` 的 `agent_gate` 段 —— 采到真实数据后
    **只改 YAML**，不动代码。

    **调用方必须先确认"有文件"**：`带文件` 是唯一未撞墙的确定性信号（Phase G-5），
    本函数只管"有文件时再判什么"。实测（49 条带文件金标集，见
    `scripts/diag_gate_criteria.py`）：偏宽松配置误入 3.8% / 漏答 0~13%；
    开启 `require_doc_ref` 可把误入压到 0%、漏答升到 17~30%。
    """
    gate = (rules or load_rules_cached()).agent_gate
    if not gate.enabled or not gate.keywords:
        return False
    # deep_agent 未接线时不得放行 —— 否则会把图路由到不存在的节点
    reg = registry or get_default_registry()
    spec = reg.get(AGENT_UPGRADE_ROUTE)
    if spec is None or not is_capability_enabled(spec):
        return False
    if not any(kw in question for kw in gate.keywords):
        return False
    if gate.require_doc_ref:
        if gate.doc_ref_pattern is None or not gate.doc_ref_pattern.search(question):
            return False
    return True


def _doc_route(
    question: str,
    *,
    rules: Optional[RoutingRules] = None,
    registry: Optional[CapabilityRegistry] = None,
) -> str:
    """带文件时的落点：闸门命中且 deep_agent 可用 → 升级；否则单节点文档问答。"""
    if should_upgrade_to_agent(question, rules=rules, registry=registry):
        logger.info("supervisor 闸门命中 → 升级 %s（带文件 + 跨源信号）", AGENT_UPGRADE_ROUTE)
        return AGENT_UPGRADE_ROUTE
    return DOC_CONTEXT_ROUTE


def route_question(
    messages: List[BaseMessage],
    *,
    llm: Optional[Any] = None,
    registry: Optional[CapabilityRegistry] = None,
    embedder: Optional[Any] = None,
    llm_router: Optional[Callable[[dict], dict]] = None,
) -> str:
    """路由（兼容旧接口）：只返回意图字符串。完整信息见 `route_question_full`。"""
    return route_question_full(
        messages, llm=llm, registry=registry,
        embedder=embedder, llm_router=llm_router,
    ).intent


@dataclass
class RouteResult:
    """三级路由的完整决策（Phase 2：路由 + 追问消解合并）。"""

    intent: str = "fallback"
    #: 追问消解后的自包含问题（L3 从 llm_router 输出的 messages 替换中还原；
    #: L0/L1/L2 快路径恒 None——它们只处理自包含的确定性场景）。
    resolved_question: Optional[str] = None
    needs_clarification: bool = False
    candidates: List[str] = field(default_factory=list)


def route_question_full(
    messages: List[BaseMessage],
    *,
    llm: Optional[Any] = None,
    registry: Optional[CapabilityRegistry] = None,
    embedder: Optional[Any] = None,
    llm_router: Optional[Callable[[dict], dict]] = None,
) -> RouteResult:
    """路由：L0 文档上下文 → L1 规则 → L2 语义 → L3 LLM → fallback（返回完整决策）。

    L0 分两档（A3）：
      - **显式**（客户端传了 file_ids）→ 最高的 L0，压过 L1/L2/L3；
      - **自动附着**（服务端按会话带上文件）→ 让过 L1 强规则后再生效，
        否则「上传过文件的会话」里问项目编号/公司会被强行拉进 doc_qa。

    **两条 L0 都可升级 deep_agent**（第 3 项）：带文件 + 闸门命中 → `deep_agent`
    （跨源编排）；否则 `doc_qa`（单节点，够快）。判据见 `should_upgrade_to_agent`。

    Phase 2（2026-09-16）：L3 为**合并会话理解调用**——`build_router_node` 一次
    输出 intent + resolved_question（追问消解），resolved_question 以同 id 原位
    替换 `messages[-1]` 的形式返回；本函数从替换中还原 `resolved_question`，
    供 `supervisor_node` 应用，使下游 agent 检索到归一化问题。

    Args:
        messages: 对话消息（末条为用户问题；L3 走完整 history 与既有 router 一致）。
        llm: L3 用 LLM（llm_router 缺省时经 build_router_node 构建）。
        registry: 能力注册表（默认进程级）。
        embedder: L2 用（None 且开关关 → 跳过 L2）。
        llm_router: 注入的 L3 分类器（(state)->{"router_intent": str, "messages"?}）；
            离线测试注入 fake。
    """
    question = str(messages[-1].content) if messages else ""
    rules = load_rules_cached()

    # L0（强）：客户端显式带了文件，是最强意图信号——交给关键词/LLM 判断会把它推翻
    # （实测：带 file_ids 问法规问题错走 knowledge_qa，文件被完全忽略）。
    kind = _doc_context_kind(messages, registry)
    if kind == "explicit":
        target = _doc_route(question, rules=rules, registry=registry)
        logger.info("supervisor L0（显式文件）路由 → %s", target)
        return RouteResult(intent=target)

    rule_hit = classify_rules(question, rules)
    if rule_hit:
        logger.info("supervisor L1 规则路由 → %s", rule_hit)
        return RouteResult(intent=rule_hit)

    # L0（弱）：本会话有自动附着的文件，且没有 L1 强信号 → 按文档答（同样可升级）。
    if kind == "session":
        target = _doc_route(question, rules=rules, registry=registry)
        logger.info("supervisor L0（会话文件）路由 → %s", target)
        return RouteResult(intent=target)

    semantic_hit = classify_semantic(question, registry, embedder)
    if semantic_hit:
        return RouteResult(intent=semantic_hit)

    if llm_router is None:
        if llm is None:
            logger.warning("supervisor L3 缺 llm 与 llm_router，兜底 fallback")
            return RouteResult(intent="fallback")
        llm_router = _build_llm_router(llm, registry)
    out = llm_router({"messages": messages}) or {}
    intent = out.get("router_intent", "fallback")
    logger.info("supervisor L3 LLM 路由 → %s", intent)
    if intent == "clarify":
        # Phase 3：追问无法消解 → 澄清反问（候选由路由节点写入 business_result.data）
        br = out.get("business_result") or {}
        data = br.get("data") if isinstance(br.get("data"), dict) else {}
        return RouteResult(
            intent="clarify",
            needs_clarification=True,
            candidates=[str(c) for c in (data.get("candidates") or [])],
        )
    if intent not in set(agent_ids(registry)):
        intent = "fallback"

    # 追问消解：从 L3 返回的 messages 原位替换中还原（与 router_node 的
    # 同 id 替换契约一致；`resolved_question` 不单独回传，避免非 schema 键）。
    resolved = None
    replacement = out.get("messages") or []
    if messages and isinstance(messages[-1], HumanMessage):
        for m in replacement:
            if isinstance(m, HumanMessage) and m.id == messages[-1].id:
                resolved = str(m.content)
                break
    return RouteResult(
        intent=intent,
        resolved_question=resolved,
        needs_clarification=bool(out.get("needs_clarification")),
        candidates=list(out.get("candidates") or []),
    )


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
        result = route_question_full(
            messages, llm=llm, registry=registry,
            embedder=embedder, llm_router=llm_router,
        )
        # Phase 3：追问无法消解 → 澄清反问（候选写进 business_result，图路由到 clarify）
        if result.needs_clarification and result.candidates:
            return {
                "supervisor_route": "clarify",
                "router_intent": "clarify",
                "business_result": {
                    "branch": "clarify",
                    "data": {"candidates": result.candidates},
                },
            }
        out = {"supervisor_route": result.intent, "router_intent": result.intent}
        # Phase 2：L3 返回的追问消解 → 同 id 原位替换 messages[-1]，
        # 下游 agent（knowledge_qa/price_inquiry）检索到归一化问题。
        if (
            result.resolved_question is not None
            and messages
            and isinstance(messages[-1], HumanMessage)
            and result.resolved_question != str(messages[-1].content)
        ):
            out["messages"] = [
                HumanMessage(content=result.resolved_question, id=messages[-1].id)
            ]
        return out

    return supervisor_node


def build_default_llm_router(llm, registry=None):
    """便捷入口：构建 L3 分类器（供 supervisor_node / 测试注入）。"""
    return _build_llm_router(llm, registry)
