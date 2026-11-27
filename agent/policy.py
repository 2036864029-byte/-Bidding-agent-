"""agent.policy — 能力 → 工具白名单（`CapabilitySpec.tools` 的执行期落地）。

为什么单独一层
--------------
`agent/registry.py` 里每个能力都声明了 `tools=["kb_search", ...]`，但此前它只是
**文档**：真正挂给 LLM 的工具写死在 `deep_agent._default_tools()`，LLM 一旦幻觉出
未声明的工具名，执行器会照单全收（`_execute_step` 只在工具不存在时才报错）。
两处各写一份 = 声明与实现必然漂移，也无法回答"这个 agent 凭什么能读文件"。

本模块把声明变成**双层执行**：

1. **装配期（最有效的一层）**：只把白名单内的工具暴露给模型
   （`bind_tools` / 规划器提示里的工具清单），模型看不到就没有调用机会；
2. **执行期（兜底）**：LLM 仍给出越权 tool_call 时**不执行**，写回拒绝原因，
   并在轨迹里留 `denied` 记录 —— 可审计、可告警。

命名口径
--------
白名单以 **registry 的声明名**授权，实际工具名由 catalog 解析（例如声明
`kb_search`，实现叫 `kb_search_tool`）。`ToolPolicy.allowed_names` 同时接受两者，
因此"改工具函数名"不会把权限悄悄改掉，也不会因为改名就全被拒。

fail-closed
-----------
能力未登记 / 未声明任何工具 → 白名单为空 → **拒绝全部**（而不是放行全部）。
宁可让 agent 少一个工具，也不让未声明的能力拿到文件读取权。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, Mapping, Optional, Sequence, Tuple

from .registry import CapabilityRegistry, get_default_registry

logger = logging.getLogger(__name__)


def tool_name(tool: Any) -> str:
    """工具的可调用名（BaseTool.name 优先，鸭子类型退化为 str）。"""
    return getattr(tool, "name", None) or str(tool)


@dataclass(frozen=True)
class ToolPolicy:
    """一个能力的工具白名单。

    Attributes:
        capability_id: 能力 id（= registry 的 CapabilitySpec.id）。
        declared: registry 声明的工具名集合。
        resolved: 按 catalog 解析出的**实际**工具名集合（可为空：纯声明态）。
        source: 白名单来源（registry / unregistered，用于诊断与审计）。
    """

    capability_id: str
    declared: FrozenSet[str] = frozenset()
    resolved: FrozenSet[str] = frozenset()
    source: str = "registry"

    @property
    def allowed_names(self) -> FrozenSet[str]:
        """声明名 + 解析名的并集（执行期按它判定）。"""
        return self.declared | self.resolved

    def is_allowed(self, name: str) -> bool:
        """该工具名是否在授权范围内（空名一律拒）。"""
        return bool(name) and name in self.allowed_names

    def filter(self, tools: Sequence[Any]) -> list:
        """只保留白名单内的工具（装配期用）。"""
        kept = [t for t in tools if self.is_allowed(tool_name(t))]
        dropped = [tool_name(t) for t in tools if not self.is_allowed(tool_name(t))]
        if dropped:
            logger.warning(
                "ToolPolicy[%s] 拒绝暴露未声明工具: %s（白名单=%s）",
                self.capability_id, dropped, sorted(self.allowed_names),
            )
        return kept

    def deny_message(self, name: str) -> str:
        """越权调用的回写文本（进 ToolMessage / 观测摘要，模型能看懂并改道）。"""
        return (
            f"工具未授权: {name}。当前能力 {self.capability_id} 仅允许 "
            f"{sorted(self.allowed_names) or '（无）'}。请改用已授权工具或直接作答。"
        )

    def describe(self) -> Dict[str, Any]:
        """审计/诊断快照（写日志或塞进 business_result.data 均可）。"""
        return {
            "capability": self.capability_id,
            "declared": sorted(self.declared),
            "resolved": sorted(self.resolved),
            "source": self.source,
        }


def policy_for(
    capability_id: str,
    *,
    registry: Optional[CapabilityRegistry] = None,
) -> ToolPolicy:
    """按能力 id 取白名单（未登记 → 空白名单，fail-closed）。"""
    reg = registry or get_default_registry()
    spec = reg.get(capability_id)
    if spec is None:
        logger.warning("ToolPolicy: 能力 %r 未登记，按 fail-closed 拒绝全部工具", capability_id)
        return ToolPolicy(capability_id=capability_id, source="unregistered")
    return ToolPolicy(
        capability_id=capability_id,
        declared=frozenset(spec.tools or ()),
        source="registry",
    )


@dataclass(frozen=True)
class BoundTools:
    """一次能力绑定结果：实际工具 + 与之匹配的策略（两者必须一起用）。"""

    policy: ToolPolicy
    tools: Tuple[Any, ...] = ()


def bind_capability_tools(
    capability_id: str,
    catalog: Mapping[str, Callable[[], Any]],
    *,
    registry: Optional[CapabilityRegistry] = None,
) -> BoundTools:
    """按能力声明从 catalog 装配工具，并返回填好 `resolved` 的策略。

    Args:
        capability_id: 能力 id。
        catalog: 声明名 → 工具工厂（惰性构造，未命中的不创建）。
        registry: 能力注册表（默认进程级）。

    Returns:
        BoundTools(policy, tools)。`policy.resolved` 为实际构造出的工具名，
        因此执行期判定既能认声明名，也能认实际名（含改名场景）。
    """
    policy = policy_for(capability_id, registry=registry)
    tools: list = []
    missing: list = []
    for name in sorted(policy.declared):
        factory = catalog.get(name)
        if factory is None:
            missing.append(name)
            continue
        try:
            tools.append(factory())
        except Exception as exc:  # noqa: BLE001 单个工具构造失败不应拖垮整个 agent
            logger.warning("ToolPolicy[%s] 工具 %s 构造失败: %s", capability_id, name, exc)
            missing.append(name)
    if missing:
        logger.warning("ToolPolicy[%s] 声明但不可用: %s", capability_id, missing)
    resolved = frozenset(tool_name(t) for t in tools)
    return BoundTools(
        policy=ToolPolicy(
            capability_id=capability_id,
            declared=policy.declared,
            resolved=resolved,
            source=policy.source,
        ),
        tools=tuple(tools),
    )


def split_denied(
    policy: Optional[ToolPolicy], names: Iterable[str],
) -> Tuple[list, list]:
    """把一批工具名拆成 (放行, 拒绝)；policy 为 None 表示不启用策略（全放行）。"""
    if policy is None:
        return list(names), []
    allowed, denied = [], []
    for name in names:
        (allowed if policy.is_allowed(name) else denied).append(name)
    return allowed, denied


__all__ = [
    "BoundTools",
    "ToolPolicy",
    "bind_capability_tools",
    "policy_for",
    "split_denied",
    "tool_name",
]
