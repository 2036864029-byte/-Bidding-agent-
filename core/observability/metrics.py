# 功能：最小业务指标（分支耗时 / 护栏失败 / 召回降级层）。
"""metrics（方案 Phase 2 / C-7）— 3 个最小指标，先 log 输出。

埋点两处：graph._with_fallback（分支耗时）、knowledge_qa 护栏分支（校验失败计数）。
召回降级层指标 helper 先提供，待 retrieval 归 core（P3）后接入。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 轻量累计（供进程内汇总/测试断言）
_BRANCH_LATENCY: dict[str, list[int]] = {}
_GUARDRAIL_FAILS: int = 0
_RECALL_FALLBACKS: dict[str, int] = {}


def record_branch_latency(branch: str, latency_ms: float) -> None:
    """分支耗时（毫秒）。"""
    _BRANCH_LATENCY.setdefault(branch, []).append(int(latency_ms))
    logger.info("metric branch_latency branch=%s latency_ms=%d", branch, int(latency_ms))


def record_guardrail_fail(branch: Optional[str] = None) -> None:
    """护栏校验失败计数（回答返回但引用未全过）。"""
    global _GUARDRAIL_FAILS
    _GUARDRAIL_FAILS += 1
    logger.warning("metric guardrail_fail branch=%s", branch or "unknown")


def record_recall_fallback(layer: str) -> None:
    """召回降级命中层（dense_only / fulltext_missing / ...）。"""
    _RECALL_FALLBACKS[layer] = _RECALL_FALLBACKS.get(layer, 0) + 1
    logger.warning("metric recall_fallback layer=%s", layer)


def snapshot() -> dict:
    """进程内指标快照（测试/诊断用）。"""
    return {
        "branch_latency_ms": {k: v for k, v in _BRANCH_LATENCY.items()},
        "guardrail_fails": _GUARDRAIL_FAILS,
        "recall_fallbacks": dict(_RECALL_FALLBACKS),
    }


def reset() -> None:
    """清空累计（测试用）。"""
    _BRANCH_LATENCY.clear()
    global _GUARDRAIL_FAILS
    _GUARDRAIL_FAILS = 0
    _RECALL_FALLBACKS.clear()
