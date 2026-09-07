# 功能：P3 阶段测试——装配层跨页续段合并 + mini 目录剥离。
"""test_assemble_crosspage —— 版面还原计划 §6.1 P3 阶段测试（先红后绿）。

被测契约（计划 P3，`pdf_markdown_assembler.py` / `transforms/structure.py`）：
  - `assemble_pages(pages, page_marker=True, join_cross_page=True)`
      跨页续段合并：前页末段为正文行、无句末收尾（不以 。！？…；”』」等
      收尾）且次页首个正文块非标题/非结构起始 → 两段合并为一行；
      前页以句末收尾、次页以标题开头 → 不合并。
  - `structure.strip_inline_toc(markdown) -> str`（2026-09-07 剥离语义）
      mini 目录块剥离：连续 ≥2 个 `第X章/节` 级标题行（各 ≤20 字、行间
      无正文成句内容）→ 末行后紧跟正文则保留末行（真实标题）、仅剥离其余行；
      纯目录页整段剥离；正常章节标题（后跟正文）不受影响。幂等。

泛化护栏：合并/识别全部为模式与标点驱动，无页码/书名常量。
"""
import pytest

from public_kb.ingestion.normalizers.pdf.pdf_markdown_assembler import (
    ParsedPage,
    assemble_pages,
)
from public_kb.ingestion.transforms.structure import (  # noqa: F401
    strip_inline_toc,
)


def page(idx, md):
    return ParsedPage(page_idx=idx, markdown=md, parser="fast_text")


# ── P3-1 跨页续段合并 ────────────────────────────────────

CROSSPAGE_A = (
    "这里提醒读者注意，有的部门规章的内容已经修改了，但规章的标题"
    "没有改变，这就导致不同网页有着标题相同但内容不同的规"
)
CROSSPAGE_B = "章。鉴于此，建议读者可以在政府官方网站查找所需要的规章。"


def test_crosspage_paragraph_joined():
    pages = [page(0, f"<!-- page: 10 -->\n{CROSSPAGE_A}"),
             page(1, f"<!-- page: 11 -->\n{CROSSPAGE_B}")]
    out = assemble_pages(pages, page_marker=False, join_cross_page=True)
    assert f"{CROSSPAGE_A}{CROSSPAGE_B}" in out.replace("\n\n", "\n")


def test_crosspage_no_join_when_sentence_complete():
    pages = [page(0, "<!-- page: 10 -->\n第一段到这里就完整结束了。"),
             page(1, "<!-- page: 11 -->\n新的段落从下页开始陈述内容。")]
    out = assemble_pages(pages, page_marker=False, join_cross_page=True)
    assert "完整结束了。新的段落" not in out


def test_crosspage_no_join_when_next_starts_heading():
    pages = [page(0, f"<!-- page: 10 -->\n{CROSSPAGE_A}"),
             page(1, "<!-- page: 11 -->\n## 第二章 招标程序\n正文内容从章首开始。")]
    out = assemble_pages(pages, page_marker=False, join_cross_page=True)
    assert f"{CROSSPAGE_A}##" not in out.replace("\n", "")


def test_crosspage_no_join_when_next_starts_struct():
    """次页以结构起始（（一）/1./答：）开头 → 不并入上页段落。"""
    pages = [page(0, f"<!-- page: 10 -->\n{CROSSPAGE_A}"),
             page(1, "<!-- page: 11 -->\n（一）有限自然资源开发利用等事项")]
    out = assemble_pages(pages, page_marker=False, join_cross_page=True)
    assert f"{CROSSPAGE_A}（一）" not in out.replace("\n", "")


def test_crosspage_join_default_off_keeps_legacy():
    """灰度契约：不传 join_cross_page 时维持旧行为（不合并）。"""
    pages = [page(0, f"<!-- page: 10 -->\n{CROSSPAGE_A}"),
             page(1, f"<!-- page: 11 -->\n{CROSSPAGE_B}")]
    out = assemble_pages(pages, page_marker=False)
    assert f"{CROSSPAGE_A}{CROSSPAGE_B}" not in out.replace("\n\n", "\n")


def test_crosspage_join_skips_page_footnote():
    """前页末尾有页脚注（① 编号注释）：当正文行会阻断续段，须跳过。"""
    pages = [
        page(0, "<!-- page: 16 -->\n……报国务院项目审批部门批准，可以进行邀请招标\n"
                "① 条文主旨为编者所加，下同。"),
        page(1, "<!-- page: 17 -->\n。前款所列项目的具体范围和规模标准。"),
    ]
    out = assemble_pages(pages, page_marker=False, join_cross_page=True)
    assert "可以进行邀请招标。前款所列项目" in out.replace("\n\n", "\n")
    # 脚注行保留，不丢失
    assert "条文主旨为编者所加" in out


def test_crosspage_no_join_footnote_is_real_content_tail():
    """前页真实末行以句末收尾 + 脚注：不合并（脚注跳过不改变句末判定）。"""
    pages = [
        page(0, "<!-- page: 16 -->\n本法自公布之日起施行。\n"
                "① 条文主旨为编者所加，下同。"),
        page(1, "<!-- page: 17 -->\n新的条文从下页开始陈述内容。"),
    ]
    out = assemble_pages(pages, page_marker=False, join_cross_page=True)
    assert "施行。新的条文" not in out


# ── P3 mini 目录剥离 ─────────────────────────────────────

def test_inline_toc_strip_keeps_last_heading_before_body():
    """书1 页 15 形态：6 行 mini 章名录 + 真实「第一章 总 则」+ 正文。
    → 前 6 行剥离、末行（真实标题）保留为 `##`。"""
    md = "\n".join([
        "## 第二章 招标",
        "## 第三章 投标",
        "## 第四章 开标评标",
        "## 第五章 中标",
        "## 第六章 法律责任",
        "## 第七章 附则",
        "## 第一章 总 则",
        "",
        "第一条为了规范招标投标活动，保护国家利益。",
    ])
    out = strip_inline_toc(md)
    assert "## 第一章 总 则" in out          # 真实标题保留
    assert "## 第二章 招标" not in out        # mini 目录行剥离
    assert "## 第七章 附则" not in out
    assert "第一条为了规范招标投标活动" in out  # 正文不损
    assert "inline-toc" not in out            # 不产生标记


def test_inline_toc_pure_toc_page_stripped_whole():
    """纯目录页（run 后无正文）→ 整段剥离。"""
    md = "\n".join([
        "## 第二章 招标",
        "## 第五章 法律责任",
        "## 第六章 附则解释",
    ])
    out = strip_inline_toc(md)
    assert "第二章" not in out
    assert "第五章" not in out
    assert out.strip() == ""


def test_inline_toc_legacy_markers_cleared():
    """旧 inline-toc 标记行（v3 存量形态）被清除。"""
    md = "\n".join([
        "<!-- inline-toc:start -->",
        "## 第二章 招标",
        "## 第五章 法律责任",
        "<!-- inline-toc:end -->",
        "",
        "第一条为了规范招标投标活动，保护国家利益。",
    ])
    out = strip_inline_toc(md)
    assert "inline-toc" not in out
    assert "第一条为了规范招标投标活动" in out


def test_real_chapters_not_stripped():
    """真实章节标题（每个后跟正文、不构成连续块）不受影响。"""
    md = "\n".join([
        "## 第一章 总则",
        "第一条为了规范招标投标活动，保护国家利益、社会公共利益。",
        "",
        "## 第二章 招标",
        "第八条招标人是依照本法规定提出招标项目、进行招标的法人。",
    ])
    out = strip_inline_toc(md)
    assert "## 第一章 总则" in out
    assert "## 第二章 招标" in out


def test_legal_cascade_not_stripped():
    """编→章→节 严格递降级联是正文开篇合法形态，不得误剥。"""
    md = "\n".join([
        "## 第一编 总则",
        "## 第一章 招标",
        "## 第一节 一般规定",
        "第一条为了规范招标投标活动，保护国家利益。",
    ])
    out = strip_inline_toc(md)
    assert "第一编 总则" in out
    assert "第一节 一般规定" in out


def test_partial_cascade_not_stripped():
    """章→节 两级级联后跟正文，同样是合法形态。"""
    md = "\n".join([
        "## 第三章 开标评标",
        "## 第二节 评标委员会",
        "评标委员会由招标人代表和有关技术、经济方面的专家组成，成句内容。",
    ])
    out = strip_inline_toc(md)
    assert "第三章 开标评标" in out
    assert "第二节 评标委员会" in out


def test_level_jump_back_keeps_last_before_body():
    """层级回跳（节→章）或同级重复不构成合法级联 → 剥离；末行后跟正文保留末行。"""
    md = "\n".join([
        "## 第一节 一般规定",
        "## 第五章 法律责任",
        "第一条为了规范招标投标活动，保护国家利益。",
    ])
    out = strip_inline_toc(md)
    assert "第一节 一般规定" not in out
    assert "## 第五章 法律责任" in out  # 末行被当真实标题保留


def test_inline_toc_keeps_last_before_article_heading():
    """书1 实测定稿：真实第一章后接的是 `#### 第一条【立法目的】`（标题）而非正文——
    末行仍须保留（run 后跟内容即保留，不要求是正文行）。"""
    md = "\n".join([
        "## 第二章 招标",
        "## 第五章 法律责任",
        "## 第一章 总 则",
        "#### 第一条【立法目的】",
        "为了规范招标投标活动，保护国家利益。",
    ])
    out = strip_inline_toc(md)
    assert "## 第一章 总 则" in out
    assert "## 第二章 招标" not in out
    assert "#### 第一条【立法目的】" in out


def test_inline_toc_idempotent():
    md = "\n".join([
        "## 第二章 招标",
        "## 第五章 法律责任",
        "## 第六章 附则解释",
    ])
    once = strip_inline_toc(md)
    again = strip_inline_toc(once)
    assert again == once
