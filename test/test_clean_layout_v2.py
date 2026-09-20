# 功能：P4 阶段测试——clean 层收缩（灰度开关 pdf_layout_v2 删除后收口：仅 R1/R2 兜底、
# A3-1 仅 external 保留、R5 折行退役）。
"""test_clean_layout_v2 —— 版面还原计划 §6.1 P4 阶段测试（开关删除后的唯一行为）。

被测契约（`core/config/settings.py` + `handoff.clean_markdown`，2026-09-07）：
  - 灰度开关 pdf_layout_v2 已删除，v2 行为成为唯一路径（旧 R4/R5 折行与 A3-1
    书路径重写随之删除）。
  - 书路径 clean 链：
      * 不做 44 字句末折行（R5 退役）；
      * 保留 R1（目录残标题剔除）/ R2（引用误标题降级）兜底；
      * 跳过 A3-1 层级重写——解析层 `#` 级为准（版面锚定）；
  - external 路径保留 A3-1（无版面几何，模式级重写是唯一归一手段）。
  - 幂等契约成立：clean(clean(x)) == clean(x)。

泛化护栏：不引入书名/页码常量；降级/保留规则全部模式驱动。
"""

from core.config.settings import Settings
from public_kb.ingestion.handoff import (
    _leading_doc_title_heading,
    clean_markdown,
)

# ── A3-5 书名恢复守卫（Step2b，2026-09-06）──────────────

def test_leading_title_matches_genuine_book_title():
    """书名/法名标题（含书名号或 法/条例…收尾）仍被识别为卷首标题。"""
    md = "# 中华人民共和国招标投标法律法规全书\n## 第一章 总则\n正文。\n"
    title, yange = _leading_doc_title_heading(md)
    assert title == "中华人民共和国招标投标法律法规全书"
    assert yange == []


def test_leading_title_skips_numbered_question():
    """手册体编号问项（N.xxx？）不得当书名置顶。"""
    md = "###### 4.政府采购法的宗旨是什么？\n### 第一章\n正文。\n"
    title, _ = _leading_doc_title_heading(md)
    assert title is None


def test_leading_title_stops_at_first_anchor():
    """遇首个 第X 结构锚停止扫描，其后内容不参与书名识别。"""
    md = "## 第一章 总则\n###### 4.政府采购法的宗旨是什么？\n正文。\n"
    title, _ = _leading_doc_title_heading(md)
    assert title is None


def test_leading_title_captures_yange_block():
    """D1=B：标题后、首个锚前的沿革行（年份+通过/公布/施行/修正）随标题一并捕获；
    "目 录"等干扰行不进块。"""
    md = "\n".join([
        "# 中华人民共和国招标投标法",
        "1. 1999年8月30日第九届全国人民代表大会常务委员会第十一次会议通过",
        "目 录",
        "## 第一章 总 则",
        "#### 第一条【立法目的】",
    ])
    title, yange = _leading_doc_title_heading(md)
    assert title == "中华人民共和国招标投标法"
    assert len(yange) == 1
    assert "1999年" in yange[0] and "通过" in yange[0]


def test_clean_book1_first_law_structure():
    """P4 锁定：首法 clean 头 = # 法名 + 沿革行（正文）+ ## 第一章（存活）+ #### 第一条。

    合成输入 = 书1 页 15 解析产物形态（P1+P2 后）：
      mini 目录（第一章~第六章）+ 真实第一章相邻 + 第一条【题】标题。
    """
    s = Settings()
    md = "\n".join([
        "# 中华人民共和国招标投标法",
        "1. 1999年8月30日第九届全国人民代表大会常务委员会第十一次会议通过 2.根据2017年12月27日第十二届全国人民代表大会常务委员会第三十一次会议《关于修改〈中华人民共和国招标投标法〉的决定》修正",
        "目 录",
        "## 第一章 总 则",
        "## 第二章 招标",
        "## 第三章 投 标",
        "## 第四章 开标、评标和中标",
        "## 第五章 法律责任",
        "## 第六章 附 则",
        "## 第一章 总 则",
        "#### 第一条【立法目的】",
        "为了规范招标投标活动，保护国家利益、社会公共利益和招标投标活动当事人的合法权益，制定本法。",
        "#### 第二条【适用范围】",
    ])
    out = clean_markdown(md, s)
    head = "\n".join(out.split("\n")[:14])
    assert head.startswith("# 中华人民共和国招标投标法")
    assert "1999年8月30日" in head          # 沿革行恢复为正文
    assert "## 第一章 总 则" in head         # 真实第一章存活
    assert "## 第二章 招标" not in head       # mini 目录剥离
    assert "#### 第一条【立法目的】" in head   # 条标题存活
    assert "第一章 总 则" in head and head.count("## 第一章 总 则") == 1


# ── R5 折行退役：clean 不按 44 字折行 ──────────────────

LONG_BODY = (
    "政府采购应当遵循公开透明公平公正和诚实信用原则。采购人不得以不合理的"
    "条件对供应商实行差别待遇或者歧视待遇，不得妨碍供应商之间的公平竞争秩"
    "序。这是法律体系对采购活动的最基本的程序性要求之一。"
)


def test_clean_does_not_reflow_at_width():
    """解析层已一段一行，clean 不得按 44 字再折行。"""
    s = Settings()
    md = LONG_BODY  # 单行超 44 字
    out = clean_markdown(md, s)
    body_lines = [ln for ln in out.split("\n") if ln.strip()]
    assert len(body_lines) == 1  # 未被 R5 折成多行


# ── R1/R2 兜底保留 ────────────────────────────────────

def test_clean_keeps_toc_leader_dropping():
    s = Settings()
    md = "#### 1.一般规定 3\n\n正文第一段开始陈述具体内容，成句表达。"
    out = clean_markdown(md, s)
    assert "1.一般规定 3" not in out


def test_clean_keeps_citation_demotion():
    s = Settings()
    md = (
        "#### 第五百条和第五百零一条的规定，认定该投标文件无效。据此，评标委"
        "员会作出了否决全部投标的处理决定，招标人依法重新组织了招标活动。"
    )
    out = clean_markdown(md, s)
    assert not out.lstrip().startswith("#")


# ── A3-1：书路径版面锚定（保留解析层级）；external 仍模式级重写 ──

def test_clean_keeps_parse_layer_heading_level():
    """书路径：解析层给的 `### 一、导论`（版面锚定节级）→ 保留，不得被模式改写为 ####。"""
    s = Settings()
    md = "### 一、导论\n\n本部分介绍研究背景与制度沿革的相关内容。"
    out = clean_markdown(md, s)
    assert "### 一、导论" in out


def test_external_still_rewrites_pattern_level():
    """external 无版面几何：A3-1 仍按模式固定级重写（一、→####）。"""
    s = Settings()
    md = "### 一、导论\n\n本部分介绍研究背景与制度沿革的相关内容。"
    out = clean_markdown(md, s, external=True)
    assert "#### 一、导论" in out


# ── P3 mini 目录剥离接线（书路径）────────────────────────

def test_clean_strips_inline_toc_block():
    """书路径 clean 对连续短章节标题块剥离；真实末行标题保留。"""
    s = Settings()
    md = "\n".join([
        "## 第二章 招标",
        "## 第五章 法律责任",
        "## 第一章 总 则",
        "",
        "第一条为了规范招标投标活动，保护国家利益。",
    ])
    out = clean_markdown(md, s)
    assert "## 第一章 总 则" in out
    assert "## 第二章 招标" not in out
    assert "inline-toc" not in out


def test_clean_does_not_strip_legal_cascade():
    """编→章→节 级联后跟正文是合法形态，不剥离。"""
    s = Settings()
    md = "\n".join([
        "## 第一编 总则",
        "## 第一章 招标",
        "## 第一节 一般规定",
        "第一条为了规范招标投标活动，保护国家利益。",
    ])
    out = clean_markdown(md, s)
    assert "第一编 总则" in out
    assert "第一节 一般规定" in out


# ── 幂等 ─────────────────────────────────────────────

def test_clean_idempotent():
    s = Settings()
    md = "\n".join([
        "## 第一章 总则",
        "第一条为了规范招标投标活动，保护国家利益、社会公共利益。",
        LONG_BODY,
        "#### 1.一般规定 3",
        "第二条本法适用于中华人民共和国境内的招标投标活动。",
    ])
    once = clean_markdown(md, s)
    twice = clean_markdown(once, s)
    assert twice == once
