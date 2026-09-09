"""CapabilityRegistry — 能力数据驱动注册表（方案 Phase 4 / P4-3 + 动态化接线）。

目标（解 R8/R9）：新增可路由能力 = 在注册表登记一个 CapabilitySpec（含该能力
自己的路由原文/示例/工具名/模型槽位），router 候选集、系统提示词、route_* tool、
graph 节点与条件边全部由此驱动 —— 不再改 router.py / graph.py 任何硬编码清单。

字段约定：
    id              能力唯一 id（= 路由意图值）。
    node            对应 graph 节点名；缺省 = id。
    description     一句话能力说明（注册表/日志用途，不直接进路由提示）。
    route_desc      路由**系统提示**条目正文 → 渲染为 "- {id}: {route_desc}"。
    route_tool_doc  路由 **tool** 描述（tool-calling 路径，LLM 实际看到）。
                    ⚠ 保留现状原文（含内部缩进），改动 = 改路由行为。
    examples        归属本能力的 few-shot 提问 → "用户: \"{q}\" → {id}"。
    model_slots / tools  本能力需要的模型槽位 / 暴露工具（声明用）。
    enabled         True=接线并进入路由候选集；False=声明但预留（不路由、不建节点）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilitySpec:
    """一个可路由能力的自描述（数据单一来源）。"""

    id: str
    node: Optional[str] = None
    description: str = ""
    route_desc: str = ""
    route_tool_doc: str = ""
    examples: List[str] = field(default_factory=list)
    model_slots: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    enabled: bool = True

    @property
    def graph_node(self) -> str:
        """实际挂到 graph 的节点名（缺省 = 能力 id）。"""
        return self.node or self.id


class CapabilityRegistry:
    """id → CapabilitySpec 注册表（进程级，幂等，保持登记顺序）。"""

    def __init__(self) -> None:
        self._capabilities: Dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        if spec.id in self._capabilities:
            logger.warning("capability %r 重复注册，覆盖", spec.id)
        self._capabilities[spec.id] = spec
        logger.info("capability registered: %s (node=%s, enabled=%s)",
                    spec.id, spec.graph_node, spec.enabled)

    def get(self, capability_id: str) -> Optional[CapabilitySpec]:
        return self._capabilities.get(capability_id)

    def all(self) -> List[CapabilitySpec]:
        return list(self._capabilities.values())

    def ids(self) -> List[str]:
        return list(self._capabilities.keys())

    def enabled_specs(self) -> List[CapabilitySpec]:
        """路由候选集 / graph 接线集：仅 enabled 且按登记顺序。"""
        return [s for s in self._capabilities.values() if s.enabled]


def seed_builtins(registry: CapabilityRegistry) -> CapabilityRegistry:
    """登记 5 个既有能力（含现状路由原文），返回同一注册表。"""
    for spec in _seed_builtin_specs():
        registry.register(spec)
    return registry


def _seed_builtin_specs() -> List[CapabilitySpec]:
    """5 个既有能力。路由原文与 router.py 现状逐字节一致（P4 冻结真值）。"""
    return [
        CapabilitySpec(
            id="knowledge_qa", node="knowledge_qa",
            description="专业知识问答（RAG，经 kb_search 工具）",
            route_desc="招投标专业知识问答（法律法规、招标方式、评标规则、投标流程、履约验收等）",
            route_tool_doc=(
                "招投标专业知识问答：法律法规、招标方式、评标规则、投标流程、履约验收等。\n\n"
                "    触发：用户询问招投标法规、程序、资格、责任等专业知识。"
            ),
            examples=["评标委员会怎么组成？"],
            model_slots=["kb_generation"], tools=["kb_search"],
        ),
        CapabilitySpec(
            id="price_inquiry", node="price_inquiry",
            description="结构化业务查询（企业/处罚/标讯）",
            route_desc=(
                "结构化业务数据查询，包括以下场景：\n"
                "  · 企业不良记录/处罚/违法/黑名单/信用查询\n"
                "  · 企业中标记录/采购项目/招标历史查询\n"
                "  · 供应商推荐/企业详情/资质查询"
            ),
            route_tool_doc=(
                "结构化业务数据查询（三大核心能力）：企业工商信息、风控黑名单、招投标中标情报。\n\n"
                "    触发场景：\n"
                "    - 查企业详情/工商信息/资质/经营状态/行业/注册资本 → 企业工商情报查询\n"
                "    - 查企业不良记录/处罚/违法/黑名单/信用 → 企业风控黑名单查询\n"
                "    - 查企业中标记录/采购项目/招标历史 → 招投标中标情报查询\n"
                "    - 查某个项目的中标情况/中标供应商/中标金额 → 招投标中标情报查询\n"
                "    - 查询供应商推荐/找几个公司/哪些公司 → 企业推荐"
            ),
            examples=["XX公司有没有不良记录？", "XX公司被处罚过吗？", "XX公司中标了哪些项目？"],
            model_slots=["price_intent"], tools=["price_query"],
        ),
        CapabilitySpec(
            id="general_chat", node="general_chat",
            description="通用对话（静态引导）",
            route_desc="通用对话（问候、自我介绍、功能咨询等非业务聊天）",
            route_tool_doc=(
                "通用对话：问候、自我介绍、功能咨询、操作引导等非业务聊天。\n\n"
                "    触发：打招呼、问能力、闲聊。"
            ),
            examples=["你能做什么？"],
        ),
        CapabilitySpec(
            id="doc_qa", node="doc_qa",
            description="文档问答（占位）",
            route_desc="文档问答（上传文件后要求分析、解读、对比文档内容）",
            route_tool_doc=(
                "文档问答：上传文件后要求分析、解读、对比、提取文档内容。\n\n"
                "    触发：提到上传文件、分析文档、解读合同、对比标书。"
            ),
            examples=["帮我分析这个文档"],
        ),
        CapabilitySpec(
            id="fallback", node="fallback",
            description="兜底引导",
            route_desc="意图无法确定时使用",
            route_tool_doc="兜底引导：意图无法确定、模棱两可时使用。",
        ),
    ]


def _seed_echo_demo() -> CapabilitySpec:
    return CapabilitySpec(
        id="echo_demo", node="echo_demo",
        description="回显演示（P4 验证：登记即接入；默认关闭防误路由）",
        route_desc="回显演示：请用户说一句话，原样复述",
        route_tool_doc="回显演示：用户要求复述/回显一句话时使用",
        examples=["请复述这句话"],
        enabled=False,
    )


def _seed_doc_agent() -> CapabilitySpec:
    """doc_agent（P6 Step 3 工具化复用验证；enabled=False 预留）。

    工具形态复用 kb_search_as_tool + price_query_as_tool；路由给真实用户需先
    数据清理 + router golden 重生成（见附录 H 执行状态）。
    """
    return CapabilitySpec(
        id="doc_agent", node="doc_agent",
        description="工具驱动 agent（kb_search + price_query 组合，P6 验证预留）",
        route_desc="文档/法规与数据综合问答：知识检索、政策解释、结构化查询组合",
        route_tool_doc=(
            "文档与数据综合问答：需要同时结合法规知识与结构化业务数据的复杂问题，"
            "或纯知识/纯数据均可的工具化回答。"
        ),
        examples=["查一下这个法规怎么规定的"],
        model_slots=["doc_agent"], tools=["kb_search", "price_query"],
        enabled=False,
    )


def _seed_deep_agent() -> CapabilitySpec:
    """deep_agent（W3-3 Plan-and-Solve + Reflection；enabled=False 预留）。

    三阶段管线（规划→执行→综合→反思）比 doc_agent 更适合复杂综合问题；路由给
    真实用户需先数据清理 + router golden 重生成（同 doc_agent 先例）。
    """
    return CapabilitySpec(
        id="deep_agent", node="deep_agent",
        description="深度综合 agent（规划→执行→综合→反思，W3-3 预留）",
        route_desc="深度综合问答：跨知识检索与结构化数据的多步自主规划",
        route_tool_doc=(
            "深度综合问答：需要多步检索/多工具组合才能回答的复杂问题（如先查企业"
            "中标数据、再查相关法规）。触发：问题同时涉及法规知识与企业数据、或"
            "需要先拆解再回答的场景。"
        ),
        examples=["XX公司最近中标项目涉及哪些法律风险？"],
        model_slots=["deep_agent"], tools=["kb_search", "price_query"],
        enabled=False,
    )


# 进程级默认注册表（惰性 seed：5 内置 enabled + echo_demo/doc_agent/deep_agent 预留 disabled）
_default_registry: Optional[CapabilityRegistry] = None


def get_default_registry() -> CapabilityRegistry:
    """返回进程级默认注册表。"""
    global _default_registry
    if _default_registry is None:
        reg = CapabilityRegistry()
        seed_builtins(reg)
        reg.register(_seed_echo_demo())
        reg.register(_seed_doc_agent())
        reg.register(_seed_deep_agent())
        _default_registry = reg
    return _default_registry


def reset_default_registry() -> None:
    """清空默认注册表（测试用）。"""
    global _default_registry
    _default_registry = None
