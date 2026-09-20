"""Phase A A3 结构归一单元测试（transforms/structure.py + clean_markdown 集成）。"""

from __future__ import annotations

from core.config.settings import Settings
from public_kb.ingestion.handoff import clean_markdown
from public_kb.ingestion.transforms.structure import (
    normalize_external_md,
    normalize_heading_levels_full,
    split_inline_articles,
)

# ── A3-1 标题级归一 ───────────────────────────────────────

def test_pattern_headings_fixed_levels():
    # 模式标题不受来源 # 数量影响，映射到固定级
    md = "# 第一章 总则\n### 第五条 内容\n## 第一节 适用范围\n# 第一编 总则\n"
    out = normalize_heading_levels_full(md)
    assert "## 第一章 总则" in out
    assert "#### 第五条 内容" in out
    assert "### 第一节 适用范围" in out
    assert "# 第一编 总则" in out


def test_inverted_chapter_section_fixed():
    # 白如银倒挂：`# 节` 出现在 `## 章` 之前 → 归一后 节(3) > 章(2) 不再倒挂
    md = "# 第八节 强制招标制度\n## 第二章 招标阶段法律实务\n"
    out = normalize_heading_levels_full(md)
    assert "### 第八节 强制招标制度" in out
    assert "## 第二章 招标阶段法律实务" in out


def test_mineru_flat_list_item_gets_content_level():
    # 1200问 MinerU 扁平 #：`1.什么是采购？` → 内容级 4（与 fast_text 一致）
    md = "## 第一章 政府采购概述\n# 1.什么是采购？\n# 2.什么是政府采购？\n"
    out = normalize_heading_levels_full(md)
    assert "#### 1.什么是采购？" in out
    assert "#### 2.什么是政府采购？" in out


def test_non_pattern_under_anchor_gets_anchor_plus_one():
    md = "## 第一章 总则\n# 政府采购概述\n# 相关概念\n"
    out = normalize_heading_levels_full(md)
    assert "### 政府采购概述" in out
    assert "### 相关概念" in out


def test_front_matter_without_anchor_keeps_level():
    # 无锚（前置页）：保持原级，不被 last_anchor 逻辑改写
    md = "# 版权信息\n# 第3版前言\n## 第一章 总则\n"
    out = normalize_heading_levels_full(md)
    assert out.startswith("# 版权信息\n# 第3版前言\n## 第一章 总则\n")


# ── A3-2 标题行内联拆分 ───────────────────────────────────

def test_split_inline_article_bracketed():
    # 《全书》格式：`第X条【标题】正文`
    md = "#### 第十二条【自行办理招标和招标代理】招标人有权自行选择招标代理机构。\n"
    out = split_inline_articles(md)
    assert "#### 第十二条【自行办理招标和招标代理】" in out
    assert "招标人有权自行选择招标代理机构。" in out


def test_split_inline_article_parenthesized_cleaned_v1():
    # cleaned_v1 格式：`## 第X条  （标题） 正文`
    md = "## 第一条  （目的和依据） 为了规范本市政府采购行为。\n"
    out = split_inline_articles(md)
    assert "## 第一条  （目的和依据）" in out
    assert "为了规范本市政府采购行为。" in out


def test_split_inline_article_plain_space():
    md = "## 第十条 供应商参加政府采购活动，应当公平竞争。\n"
    out = split_inline_articles(md)
    assert "## 第十条" in out
    assert "供应商参加政府采购活动，应当公平竞争。" in out


def test_chapter_heading_not_split():
    # 章/节标题的后续词是标题本身，不拆分
    md = "## 第一章 总则\n"
    assert split_inline_articles(md) == md


# ── A3-3 外部 markdown 治理 ───────────────────────────────

def test_citation_misread_demoted():
    # 长行 `## 第X条 第X款…` 是交叉引用，剥离 # 并入正文
    md = "## 第十一条 第一款的规定，建立健全政府采购内部管理制度，在编制政府采购预算时加强管理。\n"
    out = normalize_external_md(md)
    assert "## 第十一条" not in out
    assert "第十一条 第一款的规定" in out


def test_citation_short_line_not_demoted():
    # 短标题即使匹配引用特征也不降级（长度守卫）
    md = "## 第五条 的规定\n"
    assert normalize_external_md(md) == md


def test_real_article_heading_with_title_not_demoted():
    md = "## 第一条  （目的和依据） 为了规范本市政府采购行为，提高采购效率。\n"
    out = normalize_external_md(md)
    assert "## 第一条" in out  # 真实条标题不被降级


def test_h4_list_demoted_to_bullet():
    md = "#### （一） 使用不可替代的专利、专有技术。\n#### 一、 采购人应当依法采购。\n"
    out = normalize_external_md(md)
    assert "- （一） 使用不可替代的专利、专有技术。" in out
    assert "- 一、 采购人应当依法采购。" in out
    assert "#### （一）" not in out


def test_meta_blockquote_stripped():
    md = "> 发布：2020-11-18 09:45:00　来源：https://example.com/1\n正文内容足够长。\n"
    out = normalize_external_md(md)
    assert "发布" not in out
    assert "正文内容足够长。" in out


# ── clean_markdown 集成（external=True） ──────────────────

def test_clean_markdown_external_pipeline():
    """cleaned_v1 样例走完整清洗：元信息剥离 + 引用误读降级 + 标题归一 + 内联拆分。"""
    s = Settings()
    md = (
        "# 上海市政府采购实施办法\n"
        "\n"
        "> 发布：2020-11-18 09:45:00　来源：https://example.com\n"
        "\n"
        "## 第一条  （目的和依据） 为了规范本市政府采购行为，提高采购效率。\n"
        "\n"
        "## 第十一条 第一款的规定，建立健全政府采购内部管理制度，在编制政府采购预算和实施计划、确定采购需求时加强管理。\n"
    )
    out = clean_markdown(md, s, external=True)
    assert "> 发布" not in out
    # 引用误读被降级为正文（无 # 前缀）
    assert "## 第十一条 第一款" not in out
    assert "第十一条 第一款的规定，建立健全政府采购内部管理制度" in out
    # 真实条标题归一为 #### 并拆出正文
    assert "#### 第一条  （目的和依据）" in out
    assert "为了规范本市政府采购行为，提高采购效率。" in out


def test_clean_markdown_book_path_splits_bracketed_article():
    """书籍路径：《全书》`第X条【…】正文` 拆出标题与正文（修标题吞正文）。"""
    s = Settings()
    md = "#### 第十二条【自行办理招标和招标代理】招标人有权自行选择招标代理机构。\n正文内容足够长。\n"
    out = clean_markdown(md, s)
    assert "#### 第十二条【自行办理招标和招标代理】" in out
    assert "招标人有权自行选择招标代理机构。" in out
