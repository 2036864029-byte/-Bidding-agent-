# 功能：public_kb/guardrails 包。
"""引用溯源护栏（R1~R7）— 由 public_kb/generation/citations_* 迁入（Phase 1 / B-3）。

护栏不依赖生成链，可被多 Agent / kb_search 独立复用。
generation/citations.py 保留为对外门面（format_citations 等既有导入路径不变）。
"""

from .citations_models import Citation, CitationValidationReport, RuleResult, parse_citation_markers
from .citations_build import build_citations, format_citations
from .citations_validate import CitationValidator

__all__ = [
    "Citation",
    "RuleResult",
    "CitationValidationReport",
    "parse_citation_markers",
    "build_citations",
    "format_citations",
    "CitationValidator",
]
