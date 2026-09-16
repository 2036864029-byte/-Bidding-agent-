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
from typing import Dict, List, Optional, Tuple

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
        """路由候选集 / graph 接线集：仅 enabled 且按登记顺序。

        `enabled` 取 **spec 的声明默认值 + Settings 覆盖**（见 `is_capability_enabled`）：
        本方法是路由候选与 graph 接线**共用的唯一 chokepoint**，覆盖放在这里
        才能保证"路由到它"与"图里挂了它"不会脱节。
        """
        return [
            s for s in self._capabilities.values()
            if is_capability_enabled(s)
        ]


def _split_ids(value: str) -> frozenset:
    """逗号（半角/全角）分隔的能力 id → 集合。"""
    return frozenset(
        x.strip() for x in (value or "").replace("，", ",").split(",") if x.strip()
    )


_overrides: Optional[Tuple[frozenset, frozenset]] = None


def _enabled_overrides() -> Tuple[frozenset, frozenset]:
    """Settings 的能力开关覆盖 → ``(强制启用集, 强制停用集)``（首次调用后缓存）。

    为什么读 Settings 而不是直接 os.getenv：与全项目配置单源一致，
    且测试可用 monkeypatch.setenv 注入（配 `reset_capability_overrides`）。
    配置不可用时返回空集 —— 退化为只用 spec 的声明默认值。
    """
    global _overrides
    if _overrides is None:
        try:
            from core.config.settings import Settings

            s = Settings()
            _overrides = (
                _split_ids(getattr(s, "capability_enabled", "")),
                _split_ids(getattr(s, "capability_disabled", "")),
            )
        except Exception as exc:  # noqa: BLE001  配置不可用 → 只用声明默认值
            logger.warning("能力开关配置不可用，退化为声明默认值: %s", exc)
            _overrides = (frozenset(), frozenset())
    return _overrides


def reset_capability_overrides() -> None:
    """清空覆盖缓存（测试 / 运行时改配置后重载用）。"""
    global _overrides
    _overrides = None


def is_capability_enabled(spec: CapabilitySpec) -> bool:
    """能力的实际启用状态：**Settings 覆盖 > spec 声明默认值**。

    ``CAPABILITY_ENABLED=deep_agent`` 强制启用（无需改代码）；
    ``CAPABILITY_DISABLED=doc_agent,tool_agent`` 强制停用。两者都未列出 → 用声明默认值。
    """
    on, off = _enabled_overrides()
    if spec.id in on:
        return True
    if spec.id in off:
        return False
    return spec.enabled


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
                "  · 供应商推荐/企业详情/资质查询\n"
                "  · 采购需求参考：我要采购/需要采购 N 台（个/套/批）某产品，问历史相似项目的中标价"
            ),
            route_tool_doc=(
                "结构化业务数据查询（三大核心能力）：企业工商信息、风控黑名单、招投标中标情报。\n\n"
                "    触发场景：\n"
                "    - 查企业详情/工商信息/资质/经营状态/行业/注册资本 → 企业工商情报查询\n"
                "    - 查企业不良记录/处罚/违法/黑名单/信用 → 企业风控黑名单查询\n"
                "    - 查企业中标记录/采购项目/招标历史 → 招投标中标情报查询\n"
                "    - 查某个项目的中标情况/中标供应商/中标金额 → 招投标中标情报查询\n"
                "    - 查询供应商推荐/找几个公司/哪些公司 → 企业推荐\n"
                "    - 我要采购/需要采购 N 台（个/套/批）某产品（摄像头、服务器、家具…），"
                "想参考历史相似项目的中标价 → 招投标中标情报查询"
            ),
            examples=[
                "XX公司有没有不良记录？",
                "XX公司被处罚过吗？",
                "XX公司中标了哪些项目？",
                "我要采购120台摄像头",
                "采购50台服务器大概多少钱？",
            ],
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
    """doc_agent（**验证性产物**：P6 Step 3 工具化复用验证，enabled=False 预留）。

    ⚠️ 定位：这是早期"工具化能不能复用"的**可行性验证**，不是待上线能力
    （Phase G-5 决策：明确标注为验证性产物，避免继续投入）。它的 doc 能力已被
    `read_file` 工具 + `deep_agent` 覆盖。
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
    """deep_agent（W3-3 Plan-and-Solve + Reflection）。

    **触发面（2026-09-13 第 3 项落地后）**：主触发是 supervisor 的**确定性闸门**
    ——「带文件 + 跨源关键词」→ 直接路由本能力（见 `routing_rules.yaml` 的
    `agent_gate`，实测误入 3.8% / 漏答 0~13%）。

    下面的 route_desc / route_tool_doc 是给 **L3 LLM 路由**看的兜底文案，
    刻意写成**只描述带文件场景**：启用后本能力会同时进入 L3 候选集，
    若文案写宽（旧版写的是"多原子"），无文件的纯文本复合问就会被 L3 拉进来
    ——那正是 E5 实测天花板 ~73%、Phase G-5 明确不走的「口径 B」。

    历史：原触发维度曾由「跨源（数据+法规）」改为「多原子」（E5）；现在**两条
    文本判据都不用**，改为确定性闸门（唯一未撞墙的信号是"是否带文件"）。
    """
    return CapabilitySpec(
        id="deep_agent", node="deep_agent",
        description="深度综合 agent（带文件 + 跨源：拆解→分别检索→综合→反思）",
        route_desc=(
            "深度综合问答（**仅用于用户本轮上传了文件的场景**）：需要把文件内容"
            "与法规知识库 / 结构化业务数据**交叉比对**才能回答（如「这份标书这样约定"
            "符合规定吗」「这个采购人有没有不良记录」）。**未上传文件时不要选本能力。**"
        ),
        route_tool_doc=(
            "深度综合问答（带文件 + 跨源）：用户上传了文件，且问题需要文件内容与"
            "法规知识库或结构化业务数据交叉比对。\n\n"
            "    触发：带文件 + 合规判断/法规依据/企业记录等跨源需求。\n"
            "    不触发（选 knowledge_qa）：**未上传文件**的纯法规问题；\n"
            "    不触发（选 doc_qa）：带文件，但问题只需在该文件内查找。"
        ),
        examples=[
            "这份标书要求的投标保证金比例，符合招标投标法吗？",
            "这份标书的采购人有没有行政处罚或者不良记录？",
            "这份标书的资质要求是不是过严？依据是什么？",
        ],
        model_slots=["deep_agent"], tools=["kb_search", "price_query", "read_file"],
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
