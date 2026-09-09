"""RAG 金标集契约与抽样（E1-1 / E1-2）。

金标来源：`DATA/eval_rag/testset_1000_local.jsonl`（1000 条，650 single_hop / 350 multi_hop）。

工程化要点：
  - **schema 单一来源**：`EvalSample` 是金标字段的唯一契约，加载/落盘/抽样都走它。
  - **可复现**：抽样固定随机种子，结果落盘并写 manifest（种子 / 规模 / git commit）。
  - **holdout 隔离**：门禁用样本调 prompt/阈值时不可见，避免评测集过拟合。
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from pydantic import BaseModel, Field

SINGLE_HOP = "single_hop"
MULTI_HOP = "multi_hop"
COMPLEX = "complex"  # 长/复合/口语化的真实用户提问（≥300 字，需多步检索）


class EvalSample(BaseModel):
    """一条 RAG 金标样本（与 testset_1000_local.jsonl 字段一一对应）。"""

    question: str = Field(..., description="用户问题")
    reference: str = Field("", description="参考答案（Ragas 的 reference）")
    ground_truth_chunk_ids: List[int] = Field(
        default_factory=list, description="金标块 id（对应 Milvus chunk_id，供确定性检索指标）"
    )
    ground_truth_text: List[str] = Field(default_factory=list, description="金标块原文")
    query_type: str = Field(SINGLE_HOP, description="single_hop / multi_hop / complex")
    expected_route: str = Field(
        "", description="期望路由能力 id（如 deep_agent）；空=不做路由断言"
    )

    @property
    def is_multi_hop(self) -> bool:
        return self.query_type == MULTI_HOP

    @property
    def is_complex(self) -> bool:
        return self.query_type == COMPLEX


def load_jsonl(path: str | Path) -> List[EvalSample]:
    """读取 JSONL 金标集（逐行解析，坏行直接报错——金标集不允许静默缺样本）。"""
    samples: List[EvalSample] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(EvalSample(**json.loads(line)))
            except Exception as e:  # noqa: BLE001  带行号重抛，便于定位
                raise ValueError(f"{path}:{lineno} 金标解析失败: {e}") from e
    return samples


def dump_jsonl(samples: Iterable[EvalSample], path: str | Path) -> None:
    """写出 JSONL 金标集（UTF-8，逐行 JSON）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s.model_dump(), ensure_ascii=False) + "\n")


def _quotas(groups: dict[str, list], size: int) -> dict[str, int]:
    """按各层占比分配配额（最大余数法，保证 Σ配额 == size）。"""
    total = sum(len(v) for v in groups.values())
    quotas: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    for key, items in groups.items():
        exact = size * len(items) / total
        quotas[key] = int(exact)
        remainders.append((exact - int(exact), key))
    remainders.sort(reverse=True)
    for _, key in remainders[: size - sum(quotas.values())]:
        quotas[key] += 1
    return quotas


def stratified_sample(
    samples: Sequence[EvalSample], size: int, *, seed: int = 42
) -> List[EvalSample]:
    """按 `query_type` 分层抽样，保持各层比例；固定种子 → 结果可复现。

    Args:
        samples: 全量金标。
        size: 目标条数（≥ 总数时返回全量副本）。
        seed: 随机种子（写入 manifest，保证可复现）。

    Returns:
        抽样结果，按 (query_type, question) 排序以保证落盘 diff 稳定。
    """
    if size >= len(samples):
        return sorted(samples, key=lambda s: (s.query_type, s.question))

    groups: dict[str, list] = defaultdict(list)
    for s in samples:
        groups[s.query_type].append(s)
    quotas = _quotas(groups, size)

    rng = random.Random(seed)
    picked: List[EvalSample] = []
    for key, items in groups.items():
        pool = list(items)
        rng.shuffle(pool)
        picked.extend(pool[: quotas[key]])
    return sorted(picked, key=lambda s: (s.query_type, s.question))


def split_holdout(
    samples: Sequence[EvalSample], holdout_size: int, *, seed: int = 42
) -> Tuple[List[EvalSample], List[EvalSample]]:
    """分层切出 holdout。

    Returns:
        (dev, holdout)——dev 用于调 prompt/阈值，holdout 仅门禁判定时可见。
    """
    holdout = stratified_sample(samples, holdout_size, seed=seed)
    holdout_keys = {s.question for s in holdout}
    dev = [s for s in samples if s.question not in holdout_keys]
    return dev, holdout


def distribution(samples: Sequence[EvalSample]) -> dict[str, int]:
    """各 query_type 的条数分布（报告/断言用）。"""
    dist: dict[str, int] = defaultdict(int)
    for s in samples:
        dist[s.query_type] += 1
    return dict(dist)


__all__ = [
    "SINGLE_HOP",
    "MULTI_HOP",
    "COMPLEX",
    "EvalSample",
    "load_jsonl",
    "dump_jsonl",
    "stratified_sample",
    "split_holdout",
    "distribution",
]
