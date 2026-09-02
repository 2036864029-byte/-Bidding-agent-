# 功能：RecallPipeline 熔断状态机。
"""召回流水线（方案 Phase 3 / D-1、D-6）。

语义：
  - 按序执行 stages，任一阶段 hit(rows 非空) → 停，返回该批行。
  - deadline_ms 全局熔断：到点未命中 → 停止后续阶段，返回空 + 降级原因。
  - 阶段超时/异常即熔断（不裸进后续阶段，防扫描类长尾拖垮节点）。
  - is_scan 阶段仅 allow_scan=True 时执行；否则跳过并记 full_scan_disabled。

price_engine/recall 的 5 级链下沉后实现为 RecallStage 接入；FULL_SCAN 默认禁入。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence

from .base import RecallOutcome, RecallStage

logger = logging.getLogger(__name__)


class RecallPipeline:
    """多级召回流水线，带 deadline/扫描放行熔断。"""

    def __init__(
        self,
        stages: Sequence[RecallStage],
        *,
        deadline_ms: int = 3000,
        allow_scan: bool = False,
    ) -> None:
        self._stages = list(stages)
        self._deadline_ms = deadline_ms
        self._allow_scan = allow_scan

    @property
    def allow_scan(self) -> bool:
        return self._allow_scan

    def run(self, ctx: Optional[Dict[str, Any]] = None) -> RecallOutcome:
        ctx = dict(ctx or {})
        t0 = time.perf_counter()
        trace: List[str] = []
        outcome = RecallOutcome(ok=False)

        for stage in self._stages:
            elapsed = int((time.perf_counter() - t0) * 1000)
            remaining = self._deadline_ms - elapsed

            # 扫描类阶段仅在显式放行时执行
            if getattr(stage, "is_scan", False) and not self._allow_scan:
                trace.append(f"{stage.name}:skip(full_scan_disabled)")
                continue

            if remaining <= 0:
                trace.append(f"{stage.name}:deadline_exceeded")
                break

            try:
                hit, rows = stage.run(ctx, deadline_ms=remaining)
            except TimeoutError:
                # 阶段超时 → 熔断，不再尝试后续（防扫描长尾）
                trace.append(f"{stage.name}:timeout")
                logger.warning("recall stage %s timeout → 熔断", stage.name)
                break
            except Exception as exc:  # 阶段自身异常按失败处理，继续下一阶段
                trace.append(f"{stage.name}:error:{type(exc).__name__}")
                logger.warning("recall stage %s error: %s", stage.name, exc)
                continue

            trace.append(f"{stage.name}:hit={bool(hit)} rows={len(rows or [])}")
            if hit and rows:
                outcome.ok = True
                outcome.rows = rows
                break

        outcome.stage_trace = trace
        outcome.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not outcome.ok:
            outcome.reason = ";".join(trace) or "no_stage"
        logger.info(
            "RecallPipeline: ok=%s elapsed=%dms trace=%s",
            outcome.ok, outcome.elapsed_ms, trace,
        )
        return outcome
