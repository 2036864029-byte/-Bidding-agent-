"""CapabilityRegistry — 能力数据驱动注册表（方案 Phase 4 / P4-3）。

目标（解 R8/R9）：新增能力 = 登记一个 CapabilitySpec，router 候选集 / graph
节点边由本注册表驱动（P4 后段改 router/graph 动态化时使用）。

本阶段先落声明层：内置 5 能力登记 + echo_demo 预留 spec。router/graph 仍走
现状硬编码（零行为变更）；动态化接线（router.py:29-130 / graph.py 按 registry
建边）作为 P4 后段单独验证（router 意图集不降）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilitySpec:
    """一个可路由能力的自描述。"""

    id: str
    description: str
    node: str  # 对应 graph 节点名（解析入口见 agent.nodes.base.builtin_specs）
    model_slots: List[str] = field(default_factory=list)  # 需要哪些模型槽位
    tools: List[str] = field(default_factory=list)        # 暴露的工具名（tools.py 门面）


class CapabilityRegistry:
    """id → CapabilitySpec 注册表（进程级，幂等）。"""

    def __init__(self) -> None:
        self._capabilities: Dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        if spec.id in self._capabilities:
            logger.warning("capability %r 重复注册，覆盖", spec.id)
        self._capabilities[spec.id] = spec
        logger.info("capability registered: %s (node=%s)", spec.id, spec.node)

    def get(self, capability_id: str) -> Optional[CapabilitySpec]:
        return self._capabilities.get(capability_id)

    def all(self) -> List[CapabilitySpec]:
        return list(self._capabilities.values())

    def ids(self) -> List[str]:
        return list(self._capabilities.keys())


def _seed_builtin_specs() -> List[CapabilitySpec]:
    return [
        CapabilitySpec(id="knowledge_qa", node="knowledge_qa",
                       description="专业知识问答（RAG，经 kb_search 工具）",
                       model_slots=["kb_generation"], tools=["kb_search"]),
        CapabilitySpec(id="price_inquiry", node="price_inquiry",
                       description="结构化业务查询（企业/处罚/标讯）",
                       model_slots=["price_intent"], tools=["price_query"]),
        CapabilitySpec(id="general_chat", node="general_chat",
                       description="通用对话（静态引导）"),
        CapabilitySpec(id="doc_qa", node="doc_qa",
                       description="文档问答（占位）"),
        CapabilitySpec(id="fallback", node="fallback",
                       description="兜底引导"),
    ]


# 进程级默认注册表（惰性 seed）
_default_registry: Optional[CapabilityRegistry] = None


def get_default_registry() -> CapabilityRegistry:
    """返回进程级默认注册表（seed 内置 5 能力 + echo_demo 预留）。"""
    global _default_registry
    if _default_registry is None:
        reg = CapabilityRegistry()
        for spec in _seed_builtin_specs():
            reg.register(spec)
        # P4 演示预留：仅登记一个 spec 即完成接入（动态路由后即可用）
        reg.register(CapabilitySpec(
            id="echo_demo",
            node="echo_demo",
            description="回显演示（P4 验证：登记即接入，路由接线见动态化后段）",
        ))
        _default_registry = reg
    return _default_registry


def reset_default_registry() -> None:
    """清空默认注册表（测试用）。"""
    global _default_registry
    _default_registry = None
