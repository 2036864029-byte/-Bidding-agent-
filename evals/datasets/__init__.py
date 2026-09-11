"""evals.datasets — 金标集契约、加载、抽样与切分。"""

from __future__ import annotations

from .rag import (
    COMPLEX,
    MULTI_HOP,
    SINGLE_HOP,
    EvalSample,
    distribution,
    dump_jsonl,
    load_jsonl,
    split_holdout,
    stratified_sample,
)
from .remap import build_index, match_passage, remap_samples

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
    "build_index",
    "match_passage",
    "remap_samples",
]
