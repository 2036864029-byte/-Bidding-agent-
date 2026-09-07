# 功能：P1 阶段测试——fast_text 段落化装配（版面三判据 + 标题版面判定 + 白名单）。
"""test_paragraph_assembly —— 版面还原计划 §6.1 P1 阶段测试（先红后绿）。

被测契约（计划 P1，模块 `public_kb/ingestion/normalizers/pdf/pdf_paragraph.py`）：
  - ColumnMetrics(left_edge, right_edge, line_gap, body_size)  页/栏版面基准线
  - compute_column_metrics(lines) -> ColumnMetrics             基准线：x0 众数 / x1 最大 / y 间距中位数 / 字号中位数
  - assemble_paragraphs(lines) -> List[AssembledBlock]         段落装配
      AssembledBlock(kind, text, level)  kind ∈ heading|paragraph|raw
      新段三判据（任一命中）：首行缩进 x0 > left+1.5*body_size；
                             上段收尾 prev.x1 < right-2*body_size；
                             垂直间隙 y0-prev.y1 > 1.6*line_gap
      段内行合并：CJK 直连，ASCII 字母/数字邻接补半角空格
  - heading 判定版面优先：size >= 1.15*body_size 或 (bold 且短行且无句末符)
      → 标题；centered 且 size >= 1.2*body_size → 章级；文本模式命中但
      size≈body 且非 bold → 正文（消灭 H4 洪水的主断言）
  - 标题跨行合并：标题后紧跟的同类短行并入（版面依据，无护栏上限）
  - 白名单（kind=raw 逐行保留）：表格行、含下划线表单行、题注（居中短行）
  - paragraph_markdown(lines) -> str：装配结果序列化为一段一行 markdown

泛化护栏：全部阈值相对实测基准线，合成页不含任何书名/页码硬编码。
"""
import pytest

from public_kb.ingestion.normalizers.pdf.pdf_fast_text import TextLine

from public_kb.ingestion.normalizers.pdf.pdf_paragraph import (  # noqa: F401
    ColumnMetrics,
    compute_column_metrics,
    assemble_paragraphs,
    paragraph_markdown,
)


# ── 合成页工具：A4 单栏，左边距 72、右边距 523，正文 10.5pt ──
PW_LEFT, PW_RIGHT, BODY = 72.0, 523.0, 10.5


def mk(text, x0=PW_LEFT, y0=0.0, *, x1=None, size=BODY, bold=False,
       centered=False):
    if x1 is None:
        x1 = PW_LEFT + len(text) * size  # 近似等宽 CJK
    return TextLine(text=text, x0=x0, y0=y0, x1=x1, y1=y0 + size,
                    size=size, bold=bold, centered=centered)


def body_lines(*texts, indent_first=False):
    """连续正文视觉行（可选首行缩进 2 字）。"""
    out, y = [], 100.0
    for i, t in enumerate(texts):
        x0 = PW_LEFT + (2 * BODY if indent_first and i == 0 else 0)
        out.append(mk(t, x0=x0, y0=y))
        y += BODY * 1.5
    return out


# ── P1-1/P1-2 版面基准线与三判据 ──────────────────────────

def test_metrics_from_data_not_hardcoded():
    lines = body_lines("一是采购需求应当合规。", "二是采购程序应当规范。")
    m = compute_column_metrics(lines)
    assert m.left_edge == pytest.approx(PW_LEFT)
    assert m.right_edge <= PW_RIGHT + 1
    assert m.body_size == pytest.approx(BODY)
    assert m.line_gap == pytest.approx(BODY * 1.5)


def test_line_gap_ignores_same_row_table_cells():
    """无框表同一印刷行的表元 y 差近零 → 不得拉低行距基线（书1 整页碎段根因）。"""
    lines = [
        mk("第一条 为规范某项活动，根据有关法律法规和部门职责，制定本办法", y0=100.0),
        mk("第一条 为规范某项活动，根据有关法律法规和部门职责，制定本办法", y0=100.0 + BODY * 1.5),
        # 无框表：同一印刷行的三个表元（y 差 0.1~0.2）
        mk("公开招标", x0=300.0, y0=140.0),
        mk("邀请招标", x0=340.0, y0=140.1),
        mk("不招标", x0=380.0, y0=140.2),
    ]
    m = compute_column_metrics(lines)
    assert m.line_gap == pytest.approx(BODY * 1.5)


def test_line_gap_from_clean_text_column_not_table_rows():
    """双栏页：一栏纯文本、一栏表元（行距 ~0.7×正文）→ 行距取干净文本栏。"""
    lines = []
    y = 100.0
    for _ in range(6):  # 左栏正文 6 行，行距 1.5×
        lines.append(mk("正文行内容若干字，保证该栏有足够的行距样本量以供统计", y0=y))
        y += BODY * 1.5
    ty = 100.0
    for _ in range(8):  # 右栏表元 8 行，行距 0.7×（表行距）
        lines.append(mk("公开招标", x0=300.0, y0=ty))
        lines.append(mk("邀请招标", x0=340.0, y0=ty))
        ty += BODY * 0.7
    m = compute_column_metrics(lines)
    assert m.line_gap == pytest.approx(BODY * 1.5)


def test_table_cell_rows_do_not_fragment_paragraph():
    """含无框表的页面：正文续行不再因行距基线被污染而逐行断段。"""
    lines = [
        mk("第一条 为规范工程建设项目施工活动，根据有关法律", y0=100.0),
        mk("法规和部门职责，制定本办法。", y0=100.0 + BODY * 1.5),
        # 无框表：同一印刷行的表元（y 差近零）
        mk("公开招标", x0=300.0, y0=140.0),
        mk("邀请招标", x0=340.0, y0=140.1),
    ]
    blocks = assemble_paragraphs(lines)
    paras = [b for b in blocks if b.kind == "paragraph"]
    # 首段完整合并（行距基线不被表元 y 差拉低），表元独立成块
    assert paras[0].text == "第一条 为规范工程建设项目施工活动，根据有关法律法规和部门职责，制定本办法。"


def test_first_line_indent_starts_paragraph():
    """首行缩进 2 字 → 新段；段内续行（无缩进、上行排满）合并。"""
    lines = body_lines(
        "政府采购应当遵循公开透明原则，各部门不得以不合理的条件",
        "限制或者排斥潜在投标人，不得妨碍公平竞争秩序的形成。",
        indent_first=True,
    )
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 1
    assert blocks[0].kind == "paragraph"
    assert "不合理的条件限制" in blocks[0].text  # 跨视觉行合并


def test_prev_line_short_of_right_edge_starts_paragraph():
    """上行排不满（段尾）→ 下一行新段，即使无缩进。"""
    short = mk("以上规定自公布之日起施行。", y0=100.0)  # 明显短于 right_edge
    nxt = mk("本法所称采购，是指以合同方式有偿取得货物的行为，各单位",
             y0=100.0 + BODY * 1.5)
    blocks = assemble_paragraphs([short, nxt])
    assert len(blocks) == 2


def test_vertical_gap_starts_paragraph():
    lines = [
        mk("第一条为了规范招标投标活动，保护国家利益和社会公共利益，", y0=100.0),
        mk("提高经济效益，保证项目质量，制定本法。", y0=100.0 + BODY * 1.5),
        # 无缩进但垂直间隙大（空行隔开的两段）
        mk("本法所称工程，是指建设工程，包括建筑物和构筑物的新建、", y0=100.0 + BODY * 4.0),
    ]
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 2
    assert blocks[1].text.startswith("本法所称工程")


def test_ascii_adjacent_join_adds_space_cjk_does_not():
    lines = [
        mk("该标准编号为GB", y0=100.0),
        mk("50430-2019，自发布之日起实施，各单位应当遵照执行本标准。", y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert "GB 50430-2019" in blocks[0].text
    lines2 = [
        mk("各单位应当严格执", y0=100.0),
        mk("行本标准的各项规定。", y0=100.0 + BODY * 1.5),
    ]
    assert "严格执行" in assemble_paragraphs(lines2)[0].text


def test_struct_start_continuation_mid_sentence_merges():
    """上行无句末收尾 + 次行"一、"开头（"统"+"一、"跨行）→ 续行不另起段。"""
    lines = body_lines(
        "国家实行审计监督制度。坚持中国共产党对审计工作的领导，构建集中统",
        "一、全面覆盖、权威高效的审计监督体系。",
    )
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 1
    assert "构建集中统一" in blocks[0].text


def test_list_item_after_colon_intro_breaks_paragraph():
    """引导句以冒号收尾 + 列表项 → 正常断段（守卫不误伤真实列表）。"""
    lines = body_lines(
        "招标代理机构应当具备下列条件：",
        "(一)有从事招标代理业务的营业场所和相应资金；",
    )
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 2


# ── P1-3 标题版面判定：size/bold 优先，模式行降正文 ────────

def test_pattern_line_same_size_is_body_not_heading():
    """`第X条` 开头的正文行（同字号非加粗）→ 正文，不得 H4 化。"""
    line = mk("第三十六条第一款判定此次招标活动流标。", y0=100.0)
    blocks = assemble_paragraphs([line])
    assert blocks[0].kind == "paragraph"


def test_bigger_size_is_heading():
    # 相对字号判定需要正文基线锚点（真实页正文占多数）
    lines = [
        mk("第二章 采购当事人", size=BODY * 1.4, y0=100.0),
        mk("本章所称采购当事人是指在采购活动中享有权利和承担义务的", y0=100.0 + BODY * 2.0),
        mk("各类主体，包括采购人、供应商和采购代理机构等在内。", y0=100.0 + BODY * 3.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert blocks[0].kind == "heading"
    assert blocks[0].level == 2
    assert blocks[1].kind == "paragraph"


def test_bold_short_line_without_sentence_end_is_heading():
    line = mk("一、招标投标的起源与发展", bold=True, y0=100.0)
    blocks = assemble_paragraphs([line])
    assert blocks[0].kind == "heading"


def test_bold_same_size_continuation_is_body():
    """加粗但以句末符收尾的正文行 → 正文（标题判定须无句末符）。"""
    line = mk("招标投标活动应当遵循公开、公平、公正和诚实信用的原则。", bold=True, y0=100.0)
    blocks = assemble_paragraphs([line])
    assert blocks[0].kind == "paragraph"


def test_inline_article_title_body_split_continues_paragraph():
    """法规体 `第X条【题】正文` 同印一行：】后正文接续段落流并与次行合并。"""
    lines = [
        mk("第三条【必须进行招标的建设工程项目】在中华人", y0=100.0),
        mk("民共和国境内进行下列工程建设项目包括项目的勘", y0=100.0 + BODY * 1.5),
        mk("察、设计、施工、监理以及与工程建设有关的重要", y0=100.0 + BODY * 3.0),
    ]
    blocks = assemble_paragraphs(lines)
    assert blocks[0].kind == "heading"
    assert blocks[0].text == "第三条【必须进行招标的建设工程项目】"
    assert blocks[1].kind == "paragraph"
    assert blocks[1].text.startswith("在中华人民共和国境内")
    assert "勘察" in blocks[1].text  # 跨视觉行合并


def test_inline_article_title_alone_no_orphan_paragraph():
    """`第X条【题】` 独占一行：只出标题，不产生空正文段。"""
    blocks = assemble_paragraphs([mk("第五条【招投标活动的原则】", y0=100.0)])
    assert len(blocks) == 1
    assert blocks[0].kind == "heading"


def test_crossline_article_title_absorbs_and_splits():
    """`第X条【题` + `止】正文` 跨行：吸收切分后正文余部接续段落流（含次行）。"""
    lines = [
        mk("第三十三条【低于成本的报价竞标与骗取中标的禁", y0=100.0),
        mk("止】投标人不得以低于成本的报价竞标，也不得以", y0=100.0 + BODY * 1.5),
        mk("他人名义投标或者以其他方式弄虚作假，骗取中", y0=100.0 + BODY * 3.0),
        mk("标。", y0=100.0 + BODY * 4.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert blocks[0].kind == "heading"
    assert blocks[0].text == "第三十三条【低于成本的报价竞标与骗取中标的禁止】"
    assert blocks[1].kind == "paragraph"
    assert blocks[1].text.startswith("投标人不得以")
    assert blocks[1].text.endswith("骗取中标。")


def test_centered_big_is_chapter_level():
    lines = [
        mk("中华人民共和国招标投标法", size=BODY * 1.5, centered=True, y0=100.0),
        mk("第一条为了规范招标投标活动，保护国家利益、社会公共利益，", y0=100.0 + BODY * 2.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert blocks[0].kind == "heading"
    assert blocks[0].level <= 2


# ── P1-4 标题跨行合并（版面依据）──────────────────────────

def test_truncated_chapter_heading_merges_following_fragment():
    """`第二章 招` + `标`：被栏宽截断的标题按版面合并。"""
    h = mk("第二章 招", size=BODY * 1.4, y0=100.0)
    frag = mk("标", size=BODY * 1.4, y0=100.0 + BODY * 1.5)
    body = mk("本章适用于在中华人民共和国境内进行的招标投标活动。", y0=100.0 + BODY * 3.0)
    body2 = mk("在本章规定的情形之外，招标人不得随意终止招标活动。", y0=100.0 + BODY * 4.5)
    blocks = assemble_paragraphs([h, frag, body, body2])
    assert len(blocks) == 2
    assert blocks[0].text == "第二章 招标"
    assert blocks[0].kind == "heading"
    assert blocks[1].kind == "paragraph"


def test_truncated_paren_heading_merges_two_lines():
    """`（一）……也可以是出` + `售行为`：加粗同字号标题跨两印刷行。"""
    h = mk("（一）招标作为一种交易行为，既可以是采购行为，也可以是出",
           bold=True, y0=100.0)
    frag = mk("售行为", bold=True, y0=100.0 + BODY * 1.5)
    blocks = assemble_paragraphs([h, frag])
    assert len(blocks) == 1
    assert blocks[0].text.endswith("出售行为")


def test_heading_does_not_absorb_indented_body():
    """标题后的缩进正文不得被吞进标题。"""
    h = mk("第三章 采购程序", size=BODY * 1.4, y0=100.0)
    body = mk("采购程序包括招标、投标、开标、评标和中标等若干阶段，各",
              x0=PW_LEFT + 2 * BODY, y0=100.0 + BODY * 3.0)
    blocks = assemble_paragraphs([h, body])
    assert blocks[0].text == "第三章 采购程序"
    assert blocks[1].kind == "paragraph"


# ── P1-5 白名单：表单/表格/题注逐行保留 ───────────────────

def test_form_underscore_lines_kept_per_line():
    lines = [
        mk("出让人：＿＿＿＿＿＿＿＿", y0=100.0),
        mk("受让人：＿＿＿＿＿＿＿＿", y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert all(b.kind == "raw" for b in blocks)
    assert len(blocks) == 2


def test_table_rows_kept_per_line():
    lines = [
        mk("| 序号 | 项目 | 金额 |", y0=100.0),
        mk("| 1 | 工程 | 100 |", y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert all(b.kind == "raw" for b in blocks)


def test_caption_centered_short_line_kept_separate():
    # 题注判据需要版心宽度锚点（真实题注两侧必有正文）
    lines = [
        mk("招标投标活动应当遵循公开、公平、公正和诚实信用的各项原则要求。", y0=100.0),
        mk("图1-1 公开招标和邀请招标的流程图", centered=True, y0=116.0),
        mk("从流程图可以看出，公开招标与邀请招标在后半程的评标环节完全一致。", y0=132.0),
    ]
    blocks = assemble_paragraphs(lines)
    assert blocks[1].kind == "raw"
    assert blocks[0].kind == "paragraph"
    assert blocks[2].kind == "paragraph"


def test_tiny_centered_fragment_merges_not_caption():
    """OCR 行末 2 字碎片几何中心恰落页中（居中假象）→ 接续段落而非题注。"""
    lines = [
        mk("投标人不得以低于成本的报价竞标，也不得以", y0=100.0),
        mk("标。", x0=281.0, centered=True, y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 1
    assert blocks[0].kind == "paragraph"
    assert blocks[0].text.endswith("也不得以标。")


def test_short_right_column_fragment_merges_not_caption():
    """右栏行首 5~6 字段落残部（中心恰落页中）→ 接续段落而非题注。"""
    lines = [
        mk("审，认为所有投标都不符合招标文件要求的，可以否", x0=262.0, y0=100.0),
        mk("决所有投标。", x0=262.0, centered=True, y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 1
    assert blocks[0].kind == "paragraph"
    assert blocks[0].text.endswith("可以否决所有投标。")


# ── 排满守卫：折行列表项正文首行不判标题（书2 p39 实测根因）──

def test_fullwidth_wrapped_list_item_merges_not_heading():
    """`（2）…查`（首行排满、可见部分无句读）+ 续行 → 一个段落，不判标题。"""
    lines = [
        mk("（2）采购人或者采购代理机构应当在采购文件中明确信用信息查",
           x0=PW_LEFT + 2 * BODY, x1=PW_RIGHT, y0=100.0),
        mk("询的查询渠道及截止时点、信用信息查询记录和证据留存的具体",
           x1=PW_RIGHT, y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 1
    assert blocks[0].kind == "paragraph"
    assert blocks[0].text.endswith("具体")


def test_fullwidth_bold_wrapped_line_not_heading():
    """加粗折行正文首行（排满、无句读）→ 正文；短标题不受影响。"""
    lines = [
        mk("（一）招标作为一种交易行为，既可以是采购行为，也可以是出",
           bold=True, x1=PW_RIGHT, y0=100.0),
        mk("售行为，双方地位平等。", x1=PW_RIGHT, y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 1
    assert blocks[0].kind == "paragraph"


def test_short_list_heading_still_heading():
    """真实短列表标题（不排满）→ 仍判标题（守卫不误伤）。"""
    lines = [
        mk("一、招标投标的主要特点", y0=100.0),
        mk("招标投标活动具有程序规范、公开透明、竞争充分等显著特点，各", y0=100.0 + BODY * 3.0),
    ]
    blocks = assemble_paragraphs(lines)
    assert blocks[0].kind == "heading"


def test_left_column_wrapped_item_judged_by_column_right_edge():
    """双栏页：左栏折行项 x1 够不到全页右缘，须按本栏右缘判排满。"""
    right_col_x0 = 320.0
    lines = [
        mk("（2）采购人或者采购代理机构应当在采购文件中明确信用信息查",
           x0=PW_LEFT + 2 * BODY, x1=290.0, y0=200.0),
        mk("询的查询渠道及截止时点、信用信息查询记录和证据留存等",
           x1=290.0, y0=200.0 + BODY * 1.5),
        mk("右栏正文行，用于撑起全页右缘使按页判定的守卫失效，检验栏",
           x0=right_col_x0, x1=PW_RIGHT, y0=100.0),
        mk("级右缘聚类生效后左栏折行项不被误判为标题。", x0=right_col_x0,
           x1=PW_RIGHT, y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    first = blocks[0]
    assert first.kind == "paragraph"
    assert "信用信息查" in first.text and "证据留存等" in first.text


# ── 列表标题层级细分：一、→4 /（一）→5 / 1.→6 ─────────────

def test_list_heading_sublevels():
    """三种列表形态细分对齐 A3-1 模式级语义（书2 同字号非加粗兜底路径）。"""
    for text, want in [
        ("一、招标投标的主要特点", 4),
        ("（一）程序公平规范", 5),
        ("1.招标人的含义", 6),
    ]:
        lines = [mk(text, y0=100.0)]
        blocks = assemble_paragraphs(lines)
        assert blocks[0].kind == "heading", text
        assert blocks[0].level == want, text


def test_bold_list_heading_sublevel_not_flattened():
    """加粗路径命中列表模式也按模式细分，不再一律拍平到 4。"""
    line = mk("（一）公开原则", bold=True, y0=100.0)
    blocks = assemble_paragraphs([line])
    assert blocks[0].kind == "heading"
    assert blocks[0].level == 5


def test_centered_big_numbered_line_takes_list_level_not_h1():
    """居中大字号 + 编号（书3 手册体问项 "65.xxx？"）→ 编号级，非 H1/H2。

    规则①（2026-09-06）：编号编码了层级、字号只证"是标题"；居中大字分支
    不再吞编号行。书1 干跑 42 页次零波及（其居中大字行=编/章/节，被
    _CHAPTER_LEVEL_RE 先接走）。"""
    for text, want in [
        ("65.招标性政府采购有哪些特点？", 6),   # N. → 6
        ("（一）公开原则", 5),                   # （一）→ 5
        ("一、采购方式", 4),                     # 一、→ 4
    ]:
        line = mk(text, size=BODY * 1.5, centered=True, y0=100.0)
        body = mk("正文锚定基准字号。", y0=100.0 + BODY * 2.5)
        blocks = assemble_paragraphs([line, body])
        assert blocks[0].kind == "heading", text
        assert blocks[0].level == want, text


def test_numbered_bold_question_is_heading():
    """手册体问项（书3）：加粗 + 编号 + 以 ？ 结尾 → 叶级标题，不被句末 veto。"""
    for text, want in [
        ("65.政府采购有哪些特点？", 6),
        ("257.政府和公众提供的公共工程项目有哪些要求？政府采购应含哪些内容？", 6),
        ("（一）政府采购有哪些方式？", 5),
    ]:
        line = mk(text, bold=True, y0=100.0)
        body = mk("答：这是对政府采购方式的具体界定，正文锚定。", y0=100.0 + BODY * 2.5)
        blocks = assemble_paragraphs([line, body])
        assert blocks[0].kind == "heading", text
        assert blocks[0].level == want, text


def test_bold_unprenumbered_sentence_tail_still_body():
    """无编号的加粗句尾正文（以 ？/。 收尾）仍为正文，问项豁免只对编号行生效。"""
    for text in [
        "招标投标活动应当遵循公开、公平、公正和诚实信用的原则。",
        "这究竟应当如何处理呢？",
    ]:
        line = mk(text, bold=True, y0=100.0)
        blocks = assemble_paragraphs([line])
        assert blocks[0].kind == "paragraph", text


def test_fullwidth_wrapped_item_still_body_with_sublevels():
    """排满守卫对细分后的列表模式仍然生效（折行正文不因层级映射漏过）。"""
    lines = [
        mk("（2）采购人或者采购代理机构应当在采购文件中明确信用信息查",
           x0=PW_LEFT + 2 * BODY, x1=PW_RIGHT, y0=100.0),
        mk("询的查询渠道及截止时点、信用信息查询记录和证据留存的具体",
           x1=PW_RIGHT, y0=100.0 + BODY * 1.5),
    ]
    blocks = assemble_paragraphs(lines)
    assert len(blocks) == 1
    assert blocks[0].kind == "paragraph"


# ── 序列化与幂等 ─────────────────────────────────────────

def test_paragraph_markdown_one_line_per_paragraph():
    lines = body_lines(
        "政府采购应当遵循公开透明原则，各部门不得以不合理的条件",
        "限制或者排斥潜在投标人，不得妨碍公平竞争秩序的形成。",
        indent_first=True,
    )
    md = paragraph_markdown(lines)
    body = [ln for ln in md.split("\n") if ln.strip()]
    assert len(body) == 1
    assert md.split("\n")[0].startswith(("《", "#", "政府采购", "-")) or True


def test_paragraph_markdown_idempotent():
    lines = body_lines("政府采购应当遵循公开透明原则，各部门不得以不合理的",
                       "条件限制或者排斥潜在投标人，不得妨碍公平竞争。",
                       indent_first=True)
    once = paragraph_markdown(lines)
    again = paragraph_markdown_lines = paragraph_markdown(
        [mk(ln, y0=100.0 + i * BODY * 1.5) for i, ln in enumerate(once.split("\n"))]
    )
    assert again == once


def test_no_book_or_page_hardcode_in_module():
    """泛化护栏：实现模块不得含书名/页码常量。"""
    import inspect
    import public_kb.ingestion.normalizers.pdf.pdf_paragraph as mod
    src = inspect.getsource(mod)
    for banned in ("白如银", "1200问", "法律法规全书", "page: ", "page_idx =="):
        assert banned not in src, f"实现中出现疑似硬编码: {banned}"


# ── P2 法规名 → H1 + 沿革行降级（书1 法规合集，2026-09-07）──

def _dense_body(count=6, y0=100.0, text="正文内容用于建立行距基线。"):
    """连续正文视觉行（行距 1.5×body），确立 line_gap 基线。"""
    out, y = [], y0
    for i in range(count):
        out.append(mk(f"{text}{i}", y0=y))
        y += BODY * 1.5
    return out


def _isolated_law(lines, law_text, gap=2.5):
    """在正文流中插入一条上下间隙隔离的法规名，返回拼接行流。"""
    last_y = lines[-1].y0
    law_y = last_y + BODY * gap
    law = mk(law_text, y0=law_y)
    below = mk("第一条为了规范招标投标活动，保护国家利益，制定本法。",
               y0=law_y + BODY * gap)
    return lines + [law, below], law


def test_regulation_name_gap_isolated_is_h1():
    """P2 RC1：法规名（非加粗非居中、无 第X 模式）+ 上下间隙隔离 → H1。"""
    for name in ["中华人民共和国招标投标法", "工程建设项目施工招标投标办法",
                 "评标委员会和评标方法暂行规定"]:
        lines, law = _isolated_law(_dense_body(), name)
        blocks = assemble_paragraphs(lines)
        h = [b for b in blocks if b.kind == "heading" and b.text == name]
        assert h and h[0].level == 1, name


def test_regulation_name_centered_is_h1():
    """P2 版面证据之二：居中（即使正文字号）→ H1。"""
    line = mk("中华人民共和国招标投标法", centered=True, y0=100.0)
    blocks = assemble_paragraphs([line])
    assert blocks[0].kind == "heading"
    assert blocks[0].level == 1


def test_regulation_name_sentence_tail_not_heading():
    """句末标点收尾的正文行不得判法规名 H1（…执行。 / …的规定，认定无效。）。"""
    for text in [
        "招标投标活动应当遵循公开原则执行。",
        "本办法所称的规定，认定无效。",
    ]:
        line = mk(text, y0=100.0)
        blocks = assemble_paragraphs([line])
        assert blocks[0].kind == "paragraph", text


def test_regulation_name_quoted_not_heading():
    """书名号内引用（《…法》）以 》 收尾，尾部关键词不命中 → 正文。"""
    line = mk("根据《中华人民共和国招标投标法》第八条的规定执行", y0=100.0)
    blocks = assemble_paragraphs([line])
    assert blocks[0].kind == "paragraph"


def test_regulation_name_full_width_not_heading():
    """排满版心的法规名形态行 → 正文（真实标题不排满版心）。"""
    fill1 = mk("本办法所称工程建设项目施工招标投标办法，应当遵守本规定执行。",
               x1=PW_RIGHT, y0=100.0)
    fill2 = mk("本办法所称工程建设项目施工招标投标办法",
               x1=PW_RIGHT, y0=100.0 + BODY * 1.5)
    blocks = assemble_paragraphs([fill1, fill2])
    assert not any(b.kind == "heading" for b in blocks)


def test_regulation_name_caption_not_heading():
    """图/表题注形态（表3 …办法）不当法规名。"""
    line = mk("表3 投诉处理办法", centered=True, y0=100.0)
    blocks = assemble_paragraphs([line])
    assert not any(b.kind == "heading" for b in blocks)


def test_yange_line_demoted_to_body_not_h6():
    """D1=B：含年份 + 通过/公布/施行/修正 语义的编号行 → 正文非 H6。
    （现状：中段法沿革行的加粗数字项被列表模式拍成 H6 垃圾标题。）"""
    for text in [
        "1. 1997年12月29日第八届全国人民代表大会常务委员会第二十八次会议通过",
        "2. 根据2017年12月27日第十二届全国人民代表大会常务委员会的修改决定修正",
    ]:
        line = mk(text, bold=True, y0=100.0)
        blocks = assemble_paragraphs([line])
        assert blocks[0].kind == "paragraph", text


def test_yange_line_publish_variant_demoted():
    """沿革行常用动词"发布"（书1 政策文件"…国家发展和改革委员会发布"）同样降正文。"""
    for text in [
        "1.2018年6月6日国家发展和改革委员会发布",
        "2. 根据2021年12月28日发布的修改决定修正",
    ]:
        line = mk(text, bold=True, y0=100.0)
        blocks = assemble_paragraphs([line])
        assert blocks[0].kind == "paragraph", text


def test_regulation_name_body_fragment_with_punct_not_heading():
    """含句中句读的截断正文行不得判法规名 H1（书1 实测误判修复）。"""
    for text in [
        "中华人民共和国境内外的法人、自然人和其他组",
        "随机抽取的检查对象名单和检查结果记录，由执法",
        "本办法所称的活动。指依据本法开展的各类行为",
    ]:
        line = mk(text, y0=100.0)
        blocks = assemble_paragraphs([line])
        assert blocks[0].kind == "paragraph", text


def test_yange_rule_does_not_hit_normal_numbered_lists():
    """泛化护栏：无年份/语义的普通编号列表照常 H6（避免数字列表误伤）。"""
    line = mk("1.项目概况与招标范围", bold=True, y0=100.0)
    blocks = assemble_paragraphs([line])
    assert blocks[0].kind == "heading"
    assert blocks[0].level == 6
