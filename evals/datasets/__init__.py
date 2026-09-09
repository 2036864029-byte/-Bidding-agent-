"""evals.datasets — 金标集契约、加载、抽样与切分。"""

from __future__ import annotations

from .rag import (
    MULTI_HOP,
    SINGLE_HOP,
    EvalSample,
    distribution,
    dump_jsonl,
    load_jsonl,
    split_holdout,
    stratified_sample,
)

__all__ = [
    "SINGLE_HOP",
    "MULTI_HOP",
    "EvalSample",
    "load_jsonl",
    "dump_jsonl",
    "stratified_sample",
    "split_holdout",
    "distribution",
]
