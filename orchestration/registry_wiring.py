"""从 CapabilityRegistry 解析 supervisor 的 agent 节点表（W2-1）。

背景（审计 C1）：agent 集与可调度集合此前硬编码在
`main_graph.agent_specs`、`handoff.AGENT_NODES` 两处（加上 supervisor 的 L1 规则
共 3 个文件 5 个地方），而单图侧是 registry 驱动 —— 两条主图扩展模型不一致，
新增能力要手改多处。

现在 supervisor 与单图共用 `agent.nodes.base` 的 NodeSpec 登记表：
新增能力 = registry 登记 CapabilitySpec + base 登记节点 entry，本文件自动纳入。
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from agent.nodes.base import get_node_spec
from agent.registry import CapabilityRegistry, get_default_registry

logger = logging.getLogger(__name__)


def agent_ids(registry: Optional[CapabilityRegistry] = None) -> List[str]:
    """supervisor 可调度的 agent id 列表（registry enabled 能力，按登记顺序）。"""
    reg = registry or get_default_registry()
    return [spec.id for spec in reg.enabled_specs()]


def resolve_agent_nodes(
    registry: Optional[CapabilityRegistry] = None,
) -> Dict[str, Callable]:
    """能力 id → 节点入口（`(state, config=None) -> dict`）。

    Raises:
        ValueError: 能力引用的节点未在 `agent.nodes.base` 登记。
    """
    reg = registry or get_default_registry()
    nodes: Dict[str, Callable] = {}
    for spec in reg.enabled_specs():
        node_spec = get_node_spec(spec.graph_node)
        if node_spec is None:
            raise ValueError(
                f"capability {spec.id!r} 引用的节点 {spec.graph_node!r} 未登记 NodeSpec，"
                "请在 agent.nodes.base 中 register 该节点 entry"
            )
        nodes[spec.id] = node_spec.entry
    logger.info("supervisor agent 集（registry 驱动）: %s", list(nodes))
    return nodes


__all__ = ["agent_ids", "resolve_agent_nodes"]
