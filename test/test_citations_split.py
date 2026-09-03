"""M5 Step B / Phase1 B-3：citations 护栏迁 guardrails/ 后可导入性测试。"""

from __future__ import annotations

import public_kb.guardrails as facade
import public_kb.guardrails.citations_build as build
import public_kb.guardrails.citations_models as models
import public_kb.guardrails.citations_validate as validate


def test_facade_reexports_all_public_symbols():
    assert facade.Citation is models.Citation
    assert facade.RuleResult is models.RuleResult
    assert facade.CitationValidationReport is models.CitationValidationReport
    assert facade.build_citations is build.build_citations
    assert facade.format_citations is build.format_citations
    assert facade.parse_citation_markers is models.parse_citation_markers
    assert facade.CitationValidator is validate.CitationValidator


def test_guardrails_facade_path_works():
    # 护栏门面路径（generation.citations 壳已删除，P6 收敛）
    from public_kb.guardrails import (  # noqa: F811
        Citation,
        CitationValidator,
        build_citations,
        format_citations,
        parse_citation_markers,
    )
    assert callable(build_citations)
    assert callable(format_citations)
    assert callable(parse_citation_markers)
    assert callable(CitationValidator)
    assert Citation is models.Citation


def test_submodule_internals_available():
    # 校验器依赖 models 内的私有常量（正确归属）
    assert hasattr(models, "_UNKNOWN_DOC_NAME")
    assert hasattr(models, "_UNKNOWN_CHAPTER")
    # build 依赖 compute_chunk_uid
    assert callable(build.compute_chunk_uid)