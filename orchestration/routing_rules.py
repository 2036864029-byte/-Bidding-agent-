"""L1 路由规则 / handoff 规则的加载与缓存（W2-1 数据化）。

规则源：`orchestration/routing_rules.yaml`。改规则只改 YAML，不改代码。
文件缺失或解析失败 → 记录 error 并返回空规则（L1 与 handoff 退化为不生效），
服务仍可用；测试 `test/orchestration/test_routing_rules.py` 守卫文件存在与内容。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Pattern

import yaml

logger = logging.getLogger(__name__)

RULES_PATH = Path(__file__).resolve().parent / "routing_rules.yaml"


@dataclass(frozen=True)
class L1Rule:
    """L1 确定性快路径规则。"""

    id: str
    target: str
    pattern: Pattern[str]
    max_len: Optional[int] = None


@dataclass(frozen=True)
class HandoffRule:
    """跨 agent 交接规则。"""

    from_agent: str
    to: str
    reason: str
    keywords: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RoutingRules:
    l1: List[L1Rule] = field(default_factory=list)
    handoff: List[HandoffRule] = field(default_factory=list)


_cache: Optional[RoutingRules] = None


def _compile(pattern: str, flags: Optional[List[str]]) -> Pattern[str]:
    value = 0
    for flag in flags or []:
        if flag.upper() == "IGNORECASE":
            value |= re.IGNORECASE
    return re.compile(pattern, value)


def load_rules(path: Optional[Path] = None) -> RoutingRules:
    """读取并解析规则文件（不缓存；load_rules_cached 供运行时用）。"""
    source = path or RULES_PATH
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.error("路由规则文件不存在: %s（L1 与 handoff 规则不生效）", source)
        return RoutingRules()
    except Exception as exc:
        logger.error("路由规则文件解析失败 %s: %s（规则不生效）", source, exc)
        return RoutingRules()

    l1 = [
        L1Rule(
            id=item["id"],
            target=item["target"],
            pattern=_compile(item["pattern"], item.get("flags")),
            max_len=item.get("max_len"),
        )
        for item in (raw.get("l1") or [])
    ]
    handoff = [
        HandoffRule(
            from_agent=item["from"],
            to=item["to"],
            reason=item.get("reason", ""),
            keywords=tuple(item.get("keywords") or ()),
        )
        for item in (raw.get("handoff") or [])
    ]
    return RoutingRules(l1=l1, handoff=handoff)


def load_rules_cached() -> RoutingRules:
    """进程级缓存（规则文件变更需重启，与配置读取口径一致）。"""
    global _cache
    if _cache is None:
        _cache = load_rules()
    return _cache


def reset_rules_cache() -> None:
    """清空缓存（测试用）。"""
    global _cache
    _cache = None


__all__ = [
    "L1Rule",
    "HandoffRule",
    "RoutingRules",
    "RULES_PATH",
    "load_rules",
    "load_rules_cached",
    "reset_rules_cache",
]
