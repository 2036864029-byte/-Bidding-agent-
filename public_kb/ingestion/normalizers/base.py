"""Normalizer ABC — Stage B 规范化接口（方案 Phase 5 / E-4）。

一个来源文档可规范化出多个 IR（如 csv → 1 个 table 摘要 + N 个 markdown 行块），
故 ``normalize`` 返回 ``List[NormalizedDocument]``。

quality_score 约定（E-11 质量关卡用）：
  - 1.0 完整规范化；
  - 0.8 部分成功（部分行/块被跳过）；
  - <0.8 解析异常降级（仍产出但标记，E-11 可旁路）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..ir import NormalizedDocument, SourceFormat


class Normalizer(ABC):
    """把某个 source_format 的来源规范化为一组 NormalizedDocument。"""

    #: 本 normalizer 处理的来源格式（router 分发依据）
    source_format: SourceFormat = "text"

    @abstractmethod
    def normalize(self, source: NormalizedDocument) -> List[NormalizedDocument]:
        """规范化输入 IR（可能为空占位）→ 一个或多个完整 IR。"""
        raise NotImplementedError
