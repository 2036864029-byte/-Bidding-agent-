# 功能：agent/nodes/base — NodeSpec 节点协议（方案 Phase 4 / P4-1 + P4 动态化接线）。
"""NodeSpec — 能力节点的自描述四元组 + 节点登记存储（供 registry 驱动 graph 接线）。

本模块提供两件事：
1. ``NodeSpec``（name/entry/state_keys/fallback）：既有 5 节点的协议声明。
2. 节点登记存储：``register`` / ``get_node_spec`` / ``all_specs``。graph 按
   registry 里的 enabled 能力（``CapabilitySpec.node``）取对应 entry 挂节点；
   新增能力 = registry 登记一个 spec + 本存储登记一个 entry，router/graph 零改动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence

from ..state import AgentState  # noqa: F401  # 类型引用


@dataclass(frozen=True)
class NodeSpec:
    """节点协议：一个可被注册/路由的能力节点。

    Attributes:
        name: 节点/能力唯一 id（对应 graph 节点名与路由意图值）。
        entry: 节点入口 (AgentState) -> dict（与既有节点接口一致）。
        state_keys: 节点读写的 AgentState 键（预留，声明用）。
        fallback: 兜底节点（可选；默认指向 graph 的 fallback）。
    """

    name: str
    entry: Callable[[AgentState], dict]
    state_keys: Sequence[str] = ()
    fallback: str = "fallback"


# ── 节点登记存储（惰性 seed 既有 5 节点）──────────────────────────────

_REGISTRY: Dict[str, NodeSpec] = {}
_SEEDED = False


def _node_entries() -> dict[str, Callable[[AgentState], dict]]:
    """惰性返回既有节点入口（避免 agent.nodes 顶层循环 import）。"""
    from agent.nodes import (  # noqa: PLC0415
        node_doc_agent,
        node_doc_qa,
        node_fallback,
        node_general_chat,
        node_knowledge_qa,
        node_price_inquiry,
    )

    return {
        "knowledge_qa": node_knowledge_qa,
        "price_inquiry": node_price_inquiry,
        "general_chat": node_general_chat,
        "doc_qa": node_doc_qa,
        "doc_agent": node_doc_agent,
        "fallback": node_fallback,
    }


def _ensure_seeded() -> None:
    global _SEEDED
    if not _SEEDED:
        for name, entry in _node_entries().items():
            register(name, entry)
        _SEEDED = True


def register(name: str, entry: Callable[[AgentState], dict], **kw) -> NodeSpec:
    """登记一个节点 entry（graph 按 node 名取用）。既有的可在 _ensure_seeded 后覆盖。"""
    spec = NodeSpec(name=name, entry=entry, **kw)
    _REGISTRY[name] = spec
    return spec


def get_node_spec(name: str) -> Optional[NodeSpec]:
    """按名取 NodeSpec（惰性 seed 既有 5 节点）。"""
    _ensure_seeded()
    return _REGISTRY.get(name)


def all_specs() -> dict[str, NodeSpec]:
    """全部已登记 NodeSpec（惰性 seed 既有 5 节点）。"""
    _ensure_seeded()
    return dict(_REGISTRY)


def reset_node_registry() -> None:
    """清空节点存储（测试用）。"""
    global _SEEDED
    _REGISTRY.clear()
    _SEEDED = False


def builtin_specs() -> dict[str, NodeSpec]:
    """兼容入口：返回全部已登记 NodeSpec（默认即既有 5 节点）。"""
    return all_specs()
