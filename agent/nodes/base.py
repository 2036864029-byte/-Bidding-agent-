# 功能：agent/nodes/base — NodeSpec 节点协议（方案 Phase 4 / P4-1）。
"""NodeSpec — 能力节点的自描述四元组，为 registry/动态图/P6 子图化铺路。

本阶段只做声明与登记，**不改** router/graph 现行为（保持单图硬编码）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from ..state import AgentState  # noqa: F401  # 类型引用


@dataclass(frozen=True)
class NodeSpec:
    """节点协议：一个可被注册/路由的能力节点。

    Attributes:
        name: 节点/能力唯一 id（对应现有 graph 节点名与 RouterIntent 枚举值）。
        entry: 节点入口 (AgentState) -> dict（与既有节点接口一致）。
        state_keys: 节点读写的 AgentState 键（预留，声明用）。
        fallback: 兜底节点（可选；默认指向 graph 的 fallback）。
    """

    name: str
    entry: Callable[[AgentState], dict]
    state_keys: Sequence[str] = ()
    fallback: str = "fallback"


def _node_entries() -> dict[str, Callable[[AgentState], dict]]:
    """惰性返回 5 个既有节点入口（避免 agent.nodes 顶层循环 import）。"""
    from agent.nodes import (  # noqa: PLC0415
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
        "fallback": node_fallback,
    }


def builtin_specs() -> dict[str, NodeSpec]:
    """返回既有 5 节点的 NodeSpec（惰性解析入口）。"""
    entries = _node_entries()
    return {
        name: NodeSpec(name=name, entry=entries[name])
        for name in ("knowledge_qa", "price_inquiry", "general_chat", "doc_qa", "fallback")
    }
