# 功能：Phase A 后置排版重构（relayout）单元测试——R1/R2 兜底
# （R4/R5 已随 pdf_layout_v2 灰度开关删除，2026-09-07）。
"""覆盖 transforms.relayout 的 R1/R2 与 clean_markdown 接线（书路径）。"""
from __future__ import annotations

from public_kb.ingestion.handoff import clean_markdown
from public_kb.ingestion.transforms.relayout import (
    demote_citation_headings,
    drop_toc_leader_headings,
    relayout_pipeline,
)
from core.config.settings import Settings


# ── R1 目录点线残标题剔除 ──────────────────────────────────


def test_drop_toc_leader_heading_removed_and_preserves_neighbors():
    md = (
        "#### 第四条【监督】\n"
        "财政部门依法实施监督。\n"
        "#### 1.一般规定 3\n"
        "监察机关依法对监察对象实施监察。\n"
    )
    out = drop_toc_leader_headings(md)
    assert "1.一般规定 3" not in out
    assert "#### 第四条【监督】" in out
    assert "财政部门依法实施监督。" in out
    assert "监察机关依法对监察对象实施监察。" in out


def test_drop_toc_leader_heading_idempotent():
    md = "#### 3.招标程序 47\n正文内容。\n"
    out = drop_toc_leader_headings(md)
    assert drop_toc_leader_headings(out) == out


# ── R2 引用误作标题降级 ───────────────────────────────────


def test_demote_citation_heading_unhash():
    md = (
        "#### 第七百九十一条第二款、第三款的规定，认定无效。”\n"
        "违反本条规定的分包合同无效。\n"
    )
    out = demote_citation_headings(md)
    assert not out.startswith("#")
    assert "第七百九十一条第二款、第三款的规定，认定无效。”" in out


def test_legit_article_heading_not_demoted():
    md = "#### 第二十条【招标文件的限制】\n招标人不得限制。\n"
    out = demote_citation_headings(md)
    assert out.startswith("#### 第二十条【招标文件的限制】")


# ── relayout_pipeline = R1 + R2（R4/R5 退役后唯一管线）────


def test_pipeline_r1_r2_only_no_reflow():
    """R1/R2 生效；超 44 字单行正文原样保留（R5 句末折行退役）。"""
    long_body = (
        "政府采购应当遵循公开透明公平公正和诚实信用原则。采购人不得以不合理的"
        "条件对供应商实行差别待遇或者歧视待遇，不得妨碍供应商之间的公平竞争秩序。"
    )
    md = (
        "#### 1.一般规定 3\n\n"
        + long_body + "\n"
        + "#### 第五十条的规定，认定该投标文件无效。”\n"
    )
    out = relayout_pipeline(md)
    assert "1.一般规定 3" not in out   # R1
    assert long_body in out            # 长正文行未被折行
    assert "第五十条的规定，认定该投标文件无效。”" in out  # R2 降级入正文
    assert not out.lstrip().startswith("#")


def test_pipeline_idempotent():
    md = (
        "#### 5.政府采购投标保证金的数额上限是多少？政府采购履约保证金的上限是多少？\n"
        "答：投标保证金不得超过采购预算的2%。\n"
        "#### 1.一般规定 3\n"
    )
    once = relayout_pipeline(md)
    assert relayout_pipeline(once) == once


# ── 接线：clean_markdown 书路径启用 R1/R2、无 R5 折行；external 不启用 ──


def test_clean_markdown_book_path_r1_r2_no_reflow():
    """书路径：R1/R2 兜底生效；超 44 字单行正文不被 R5 重新折行。"""
    s = Settings()
    md = (
        "#### 1.一般规定 3\n\n"
        "政府采购应当遵循公开透明公平公正和诚实信用原则。采购人不得以不合理的条件对"
        "供应商实行差别待遇或者歧视待遇，不得妨碍供应商之间的公平竞争秩序。\n"
    )
    out = clean_markdown(md, s)
    assert "1.一般规定 3" not in out
    body_lines = [
        ln for ln in out.split("\n")
        if ln.strip() and not ln.lstrip().startswith(("#", "<", "|"))
    ]
    assert len(body_lines) == 1  # 无 R5 折行


def test_external_path_skips_relayout():
    # 外部 md 治理：结构测试里的 external 用例不被 relayout 改写
    s = Settings()
    md = "# 上海市政府采购实施办法\n\n## 第一条  （目的和依据） 为了规范采购。\n"
    out = clean_markdown(md, s, external=True)
    assert "# 上海市政府采购实施办法" in out


def test_clean_markdown_book_preserves_leading_doc_title():
    # A3-5 误吞回归：书名/法名标题保留为 H1 书根
    s = Settings()
    md = (
        "### 中华人民共和国招标投标法\n"
        "#### 1. 1999年8月30日通过\n"
        "## 第二章 招标\n"
        "正文内容。\n"
    )
    out = clean_markdown(md, s)
    assert out.startswith("# 中华人民共和国招标投标法")
    assert "正文内容。" in out
