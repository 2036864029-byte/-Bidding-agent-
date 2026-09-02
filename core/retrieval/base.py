# 功能：RecallStage 协议 + RecallOutcome。
"""召回阶段协议与结果（方案 Phase 3 / D-1）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RecallOutcome:
    """一次 RecallPipeline.run 的结果。"""

    ok: bool
    rows: List[Dict[str, Any]] = field(default_factory=list)
    stage_trace: List[str] = field(default_factory=list)
    reason: Optional[str] = None  # 失败/降级原因（ok 时 None）
    elapsed_ms: int = 0


class RecallStage(ABC):
    """单个召回阶段。

    实现约定：``run`` 返回 (hit: bool, rows)；hit=True 且 rows 非空 → 流水线停在此阶段。
    超时/异常由流水线捕获处理；阶段内不再负责熔断。
    """

    #: 阶段名（trace 用）
    name: str = "stage"
    #: 是否为全表扫描类阶段——仅 allow_scan=True 时允许执行
    is_scan: bool = False

    @abstractmethod
    def run(
        self,
        ctx: Dict[str, Any],
        *,
        deadline_ms: int,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        raise NotImplementedError
