"""多轮对话金标集契约（多轮评测基座，2026-09-16）。

对齐 `evals/datasets/rag.py` 的工程化约束：schema 单一来源、JSONL 落盘、坏行报错。

每轮期望字段（`expected["turnN"]`）：
  precheck   确定性改写预判（`looks_like_followup`）应当输出的值 —— **Phase 1
             离线门禁**只断言它，零 LLM、不触网。
  rewrite    语义上是否应当改写/消解 —— Phase 2（合并理解节点）目标；可大于
             precheck（如"有哪些成员？"这种含省略但无线索词的追问，Phase 1
             确定性预判刻意不命中，由 Phase 2 的 LLM 判断补齐）。
  intent     期望路由能力 id（在线全图检查用）。
  branch     期望业务分支（在线全图检查用）。
  refusal    期望是否拒答（在线全图检查用；False=应正常作答）。

在线字段当前为**信息性**（真实 LLM/Milvus，进不了离线 CI），门禁以 precheck 为准。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Union

PathLike = Union[str, Path]


@dataclass
class MultiTurnSample:
    """一组多轮对话 + 逐轮期望。

    Args:
        id: 样本唯一 id（形如 mt-ell-01）。
        turns: 完整轮次（turn1, turn2, ...）。
        expected: {"turn2": {...}} —— 只列需要断言的轮次（通常从第 2 轮起）。
    """

    id: str
    turns: List[str]
    expected: Dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "turns": self.turns, "expected": self.expected}

    @classmethod
    def from_dict(cls, d: dict) -> "MultiTurnSample":
        return cls(
            id=str(d["id"]),
            turns=[str(t) for t in d["turns"]],
            expected={
                str(k): dict(v) for k, v in (d.get("expected") or {}).items()
            },
        )


def load_jsonl(path: PathLike) -> List[MultiTurnSample]:
    """读取 JSONL 金标集（逐行解析，坏行直接报错——金标集不允许静默缺样本）。"""
    samples: List[MultiTurnSample] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(MultiTurnSample.from_dict(json.loads(line)))
            except Exception as e:  # noqa: BLE001  带行号重抛，便于定位
                raise ValueError(f"{path}:{lineno} 金标解析失败: {e}") from e
    return samples


def dump_jsonl(samples: Iterable[MultiTurnSample], path: PathLike) -> None:
    """写出 JSONL 金标集（UTF-8，逐行 JSON）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")


def categories(samples: Iterable[MultiTurnSample]) -> Dict[str, int]:
    """按 id 前缀（mt-<类目>-NN）统计各类目条数（报告/断言用）。"""
    dist: Dict[str, int] = {}
    for s in samples:
        cat = s.id.rsplit("-", 1)[0]
        dist[cat] = dist.get(cat, 0) + 1
    return dist


__all__ = [
    "MultiTurnSample",
    "load_jsonl",
    "dump_jsonl",
    "categories",
]
