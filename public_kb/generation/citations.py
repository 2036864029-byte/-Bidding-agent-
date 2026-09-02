# 功能：引用溯源 — 对外门面，指向已迁 guardrails/ 的护栏实现（Phase 1 / B-3）。
"""引用溯源门面 — 保持既有导入路径不变。

护栏实现已迁 public_kb/guardrails/（models/build/validate）；本模块仅 re-export，
保证 `from public_kb.generation.citations import ...`（测试、agent、__main__、
chain 等调用方）无需改动。
"""

from __future__ import annotations

from ..guardrails.citations_build import build_citations, format_citations
from ..guardrails.citations_models import (
    Citation,
    RuleResult,
    CitationValidationReport,
    _compute_cited_sets,
    parse_citation_markers,
)
from ..guardrails.citations_validate import CitationValidator

__all__ = [
    "Citation",
    "RuleResult",
    "CitationValidationReport",
    "build_citations",
    "format_citations",
    "parse_citation_markers",
    "CitationValidator",
]
