"""Phase A A3-5 前置页/目录标记 + A4 结构校验闭环单元测试（structure.py）。"""

from __future__ import annotations

from core.config.settings import Settings
from public_kb.ingestion.handoff import clean_markdown
from public_kb.ingestion.transforms.structure import (
    mark_front_matter,
    mark_toc_page,
    repair_structure,
)

# ── A3-5 前置页标记 ───────────────────────────────────────

def test_front_matter_marked_before_first_anchor():
    md = (
        "# 中华人民共和国招标投标法律法规全书\n"
        "# 版权信息\n"
        "# 前言\n"
        "## 第一章 总则\n"
        "正文内容足够长。\n"
    )
    out = mark_front_matter(md)
    assert out.startswith("<!-- frontmatter:start -->")
    assert "<!-- frontmatter:end -->" in out
    assert "## 第一章 总则" in out
    head, rest = out.split("<!-- frontmatter:end -->", 1)
    assert "# 版权信息" in head
    assert "## 第一章 总则" in rest


def test_front_matter_skip_strips_block():
    md = "# 版权信息\n# 前言\n## 第一章 总则\n正文内容足够长。\n"
    out = mark_front_matter(md, skip=True)
    assert "版权信息" not in out
    assert "前言" not in out
    assert out.startswith("## 第一章 总则")


def test_front_matter_skip_guards_manual_opening_body():
    """手册体（1200问）以内容章+问项开篇、首个 第X 锚在后：skip 不剥离不标记。

    剥离会吞真实开篇章（政府采购概述 + Q1-86），标记会把它当待弃前置页；
    守卫探测到编号问项/答：正文形态 → 原样返回。"""
    md = (
        "## 政府采购概述\n"
        "###### 1.什么是采购人？\n"
        "答：采购主体选择的规则。\n"
        "###### 2.什么是政府采购？\n"
        "答：政府采购的界定各国不同。\n"
        "### 第一章 总则\n"
        "正文内容足够长。\n"
    )
    out = mark_front_matter(md, skip=True)
    assert "政府采购概述" in out
    assert "1.什么是采购人？" in out
    assert "答：" in out
    assert "第一章 总则" in out
    assert "frontmatter:start" not in out
    assert out == md  # 原样返回，无任何标记/剥离


def test_front_matter_no_anchor_noop():
    md = "只有正文，没有任何条标题。\n"
    assert mark_front_matter(md) == md


def test_front_matter_empty_before_anchor_noop():
    md = "## 第一章 总则\n正文内容足够长。\n"
    assert mark_front_matter(md) == md


def test_front_matter_boundary_is_first_chapter_anchor():
    # 1200问：列表项 `1.` 不是前置页边界；正文从 `第一章` 起
    md = "# 封面\n# 前言\n## 第一章 总则\n#### 1.什么是采购？\n内容。\n"
    out = mark_front_matter(md)
    head, rest = out.split("<!-- frontmatter:end -->", 1)
    assert "## 第一章 总则" in rest
    assert "#### 1.什么是采购？" in rest
    assert "# 封面" in head


# ── A3-5 目录页标记 ───────────────────────────────────────

def test_toc_heading_marked():
    md = "## 目 录\n第一章 综合⋯⋯⋯⋯(1)\n## 第一章 总则\n正文。\n"
    out = mark_toc_page(md)
    assert "<!-- toc: 目录 -->" in out
    assert "## 目 录" not in out


def test_toc_marked_idempotent():
    md = "<!-- toc: 目录 -->\n## 第一章 总则\n正文。\n"
    assert mark_toc_page(md) == md


def test_toc_no_heading_noop():
    md = "## 第一章 总则\n正文内容足够长。\n"
    assert mark_toc_page(md) == md


# ── A4 结构校验闭环 ───────────────────────────────────────

def test_repair_heading_level_fixed():
    # 用「第五章」避免被 A3-2 内联拆分（拆的是条款项，不是章节）
    md = "### 第五章 内容\n## 第一章 总则\n"
    out, report = repair_structure(md)
    assert "## 第五章 内容" in out
    assert report["heading_level"] == 1
    assert report["rounds"] == 2  # 修复 1 轮 + 确认无残留 1 轮


def test_repair_heading_level_book_list_item():
    # 书籍路径（一）→ 内容级 5（#### → #####），与 A3-1 口径一致
    md = "#### （一） 使用不可替代的专利、专有技术。\n"
    out, report = repair_structure(md)
    assert "##### （一） 使用不可替代的专利、专有技术。" in out
    assert report["heading_level"] == 1


def test_repair_inline_split():
    md = "#### 第十二条【自行办理招标和招标代理】招标人有权自行选择招标代理机构。\n"
    out, report = repair_structure(md)
    assert "#### 第十二条【自行办理招标和招标代理】" in out
    assert "招标人有权自行选择招标代理机构。" in out
    assert report["inline_split"] == 1


def test_repair_citation_misread():
    md = (
        "## 第十一条 第一款的规定，建立健全政府采购内部管理制度，"
        "在编制政府采购预算时加强管理。\n"
    )
    out, report = repair_structure(md)
    assert "## 第十一条" not in out
    assert "第十一条 第一款的规定" in out
    assert report["citation_misread"] == 1


def test_repair_toc_mark():
    md = "## 目 录\n## 第一章 总则\n"
    out, report = repair_structure(md)
    assert "<!-- toc: 目录 -->" in out
    assert report["toc_mark"] == 1


def test_repair_converges_without_change():
    # 干净输入：已固定级 + 括号标题（无内联正文可拆）
    md = "## 第一章 总则\n#### 第五条【内容】\n正文内容足够长。\n"
    out, report = repair_structure(md)
    assert out == md
    assert report["rounds"] == 1
    assert all(
        report[k] == 0
        for k in ("heading_level", "inline_split", "citation_misread", "toc_mark")
    )


def test_repair_bounded_rounds():
    # 构造每轮都产生修复的输入不可行（各规则一次收敛），此处验证 max_rounds=0
    # 边界不循环、rounds 记录为 0。
    out, report = repair_structure("## 第一章 总则\n", max_rounds=0)
    assert out == "## 第一章 总则\n"
    assert report["rounds"] == 0


def test_split_inline_articles_idempotent():
    # 已拆出的 `第X条【标题】` 行不得被再次拆分（A4 二次执行防吞标题回归）
    from public_kb.ingestion.transforms.structure import split_inline_articles

    md = (
        "#### 第十二条【自行办理招标和招标代理】\n"
        "招标人有权自行选择招标代理机构。\n"
    )
    assert split_inline_articles(md) == md


# ── clean_markdown 集成 ───────────────────────────────────

def test_clean_markdown_book_marks_front_matter_and_toc():
    # A3-5 标记模式（pdf_skip_front_matter=false，Phase B 消费标记）
    s = Settings(pdf_skip_front_matter=False)
    md = (
        "# 中华人民共和国招标投标法律法规全书\n"
        "# 前言\n"
        "## 目 录\n"
        "第一章 综合⋯⋯⋯⋯(1)\n"
        "## 第一章 总则\n"
        "#### 第一条 为了规范招标投标活动。\n"
    )
    out = clean_markdown(md, s)
    assert "<!-- frontmatter:start -->" in out
    assert "<!-- frontmatter:end -->" in out
    assert "<!-- toc: 目录 -->" in out
    assert "## 目 录" not in out
    # 目录点线行被剔除
    assert "⋯⋯" not in out
    # 内联正文拆出（A3-2 后不随标题行吞并）
    assert "为了规范招标投标活动。" in out


def test_clean_markdown_book_default_strips_front_matter():
    # 默认 pdf_skip_front_matter=true：书籍前置区（封面/序言/目录）剥离，
    # 但正文开头的书名/法名标题（A3-5 误吞回归修复）保留并置顶为 H1 书根。
    s = Settings()
    md = (
        "# 中华人民共和国招标投标法律法规全书\n"
        "# 前言\n"
        "## 目 录\n"
        "第一章 综合⋯⋯⋯⋯(1)\n"
        "## 第一章 总则\n"
        "#### 第一条 为了规范招标投标活动。\n"
    )
    out = clean_markdown(md, s)
    assert "frontmatter:start" not in out
    # 书名标题不再被当前置页吞掉（设计性改善）→ H1 置顶
    assert out.startswith("# 中华人民共和国招标投标法律法规全书")
    # 真正的 前言/目录/纲要 仍被剥离
    assert "前言" not in out
    assert "目 录" not in out
    assert "第一章 综合" not in out
    assert "## 第一章 总则" in out
    assert "为了规范招标投标活动。" in out


def test_clean_markdown_book_skip_front_matter():
    s = Settings(pdf_skip_front_matter=True)
    md = "# 版权信息\n# 前言\n## 第一章 总则\n正文内容足够长。\n"
    out = clean_markdown(md, s)
    assert "frontmatter:start" not in out
    assert "版权信息" not in out
    assert out.startswith("## 第一章 总则")


def test_clean_markdown_external_untouched_by_front_matter():
    # 外部路径不做前置页标记（标题是正文一部分）
    s = Settings()
    md = "# 上海市政府采购实施办法\n\n## 第一条  （目的和依据） 为了规范采购。\n"
    out = clean_markdown(md, s, external=True)
    assert "frontmatter:start" not in out
    assert "# 上海市政府采购实施办法" in out
