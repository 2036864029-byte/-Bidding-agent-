"""L2 快路径与双栏重建单元测试（不依赖 pymupdf）。"""

from __future__ import annotations

from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
    TextLine,
    detect_header_candidates,
    generate_markdown,
    normalize_header_text,
    remove_header_lines,
    remove_page_numbers,
    sort_single_column,
)
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (
    is_full_width_line,
    reflow_two_columns,
)


def _line(text, y0, x0=0, x1=100, size=10, bold=False, centered=False):
    return TextLine(
        text=text, x0=x0, y0=y0, x1=x1, y1=y0 + 12,
        size=size, bold=bold, centered=centered,
    )


# ── 单栏排序 ─────────────────────────────────────────────

def test_sort_single_column_by_y_then_x():
    a = _line("第一行", y0=100, x0=10)
    b = _line("第二行", y0=200, x0=10)
    c = _line("同 y 左", y0=200, x0=5)
    ordered = sort_single_column([b, a, c])
    assert [ln.text for ln in ordered] == ["第一行", "同 y 左", "第二行"]


# ── 页码剔除 ─────────────────────────────────────────────

def test_page_number_removed():
    lines = [_line("12", y0=10), _line("正文", y0=50)]
    assert [ln.text for ln in remove_page_numbers(lines)] == ["正文"]


def test_non_number_kept():
    lines = [_line("12号文", y0=10), _line("正文", y0=50)]
    assert len(remove_page_numbers(lines)) == 2


# ── 页眉剔除（文档级候选，A1-1/A1-3） ─────────────────────

def test_normalize_header_text_strips_page_number():
    assert normalize_header_text("78 一、招标投标") == "一、招标投标"
    assert normalize_header_text("一、招标投标") == "一、招标投标"
    assert normalize_header_text("488") == ""


def test_normalize_header_text_strips_trailing_page_number():
    """侧标/书签页码在行尾（"1.一般规定17"）；数字前是 CJK 才剥。"""
    assert normalize_header_text("1.一般规定17") == "1.一般规定"
    assert normalize_header_text("GB 50430-2019") == "GB 50430-2019"


def test_trailing_pagenum_sidebar_detected_as_header():
    """侧标逐页仅页码变化（"1.一般规定17"/"1.一般规定21"）→ Signal A 候选。"""
    pages = [
        [_line("1.一般规定17", y0=20), _line("正文内容" * 8, y0=300)],
        [_line("1.一般规定21", y0=20), _line("正文内容" * 8, y0=300)],
        [_line("1.一般规定27", y0=20), _line("正文内容" * 8, y0=300)],
    ]
    candidates = detect_header_candidates(pages, min_pages=2)
    assert "1.一般规定" in candidates


def test_zone_aware_removal_keeps_inpage_real_heading():
    """候选文本位于版心（节标题原文）→ 保留；页顶同文本 → 删除。"""
    cand = ["1.一般规定"]
    top_tab = _line("1.一般规定17", y0=20)
    mid_heading = _line("1.一般规定", y0=300)
    kept = remove_header_lines(
        [top_tab, mid_heading], cand, page_height=728.0
    )
    assert [ln.text for ln in kept] == ["1.一般规定"]


def test_header_line_removed_when_in_candidates():
    h = _line("一、招标投标", y0=5)
    body = _line("本条正文内容足够长确保保留", y0=400)
    kept = remove_header_lines([h, body], header_candidates=["一、招标投标"])
    assert [ln.text for ln in kept] == ["本条正文内容足够长确保保留"]


def test_no_candidates_removes_nothing():
    # 旧行为按位置硬切（top_ratio=0.12，阈值 95pt）会误删 y=76pt 的正文首行；
    # 无文档级候选时不再删任何行。
    first = _line("第一条 本法所称招标投标活动是指", y0=76)
    body = _line("本条正文内容足够长确保保留", y0=400)
    kept = remove_header_lines([first, body], header_candidates=None)
    assert [ln.text for ln in kept] == [first.text, body.text]


def test_legal_heading_not_detected_as_header():
    pages = [[_line("第一章 总则", y0=5)]] * 3
    candidates = detect_header_candidates(pages, min_pages=2)
    assert "第一章 总则" not in candidates


def test_header_with_page_number_detected_across_pages():
    # "78 一、招标投标" / "288 一、招标投标" 归一化后同一条、页码变化 → Signal A 候选
    pages = [
        [_line("78 一、招标投标", y0=5), _line("正文内容足够长保留一", y0=400)],
        [_line("288 一、招标投标", y0=5), _line("正文内容足够长保留二", y0=400)],
    ]
    candidates = detect_header_candidates(pages, min_pages=2)
    assert "一、招标投标" in candidates


def test_body_phrase_repeat_two_pages_not_header():
    # 跨页重复的正文短语无页码前缀、页数 < min_repeat → 不得误判为页眉（A6 反过拟合）
    pages = [
        [_line("(1)代理机构", y0=100), _line("正文内容足够长保留一", y0=400)],
        [_line("(1)代理机构", y0=100), _line("正文内容足够长保留二", y0=400)],
    ]
    candidates = detect_header_candidates(pages, min_pages=2)
    assert "(1)代理机构" not in candidates


def test_plain_repeating_header_detected_by_signal_b():
    # 无页码、但稳定跨 >= min_repeat 页出现的短行 → Signal B 候选
    pages = [[_line("二、政府采购", y0=5)] for _ in range(6)]
    candidates = detect_header_candidates(pages, min_pages=2, min_repeat=5)
    assert "二、政府采购" in candidates


def test_mid_page_repeated_phrase_not_header_with_heights():
    # 正文短语跨页重复但在版心（y=400，页高 800，非顶/底区）→ 不得误判
    # 即使重复页数达标；位置佐证排除（全书实测：`(一)技术复杂…` 跨 7 页）
    pages = [[_line("(一)技术复杂、有特殊要求或者受自然环境限", y0=400)] for _ in range(7)]
    heights = [800.0] * 7
    candidates = detect_header_candidates(
        pages, page_heights=heights, min_pages=2, min_repeat=5
    )
    assert candidates == set()


def test_top_zone_repeated_line_detected_with_heights():
    pages = [[_line("二、政府采购", y0=5)] for _ in range(6)]
    heights = [800.0] * 6
    candidates = detect_header_candidates(
        pages, page_heights=heights, min_pages=2, min_repeat=5
    )
    assert "二、政府采购" in candidates


def test_single_page_short_line_not_header():
    pages = [[_line("正文内容仅出现一次", y0=5)]]
    candidates = detect_header_candidates(pages, min_pages=2)
    assert candidates == set()


# ── Markdown 标题生成 ────────────────────────────────────

def test_generate_markdown_heading_levels():
    lines = [
        _line("第一章 总则", y0=10, size=16, centered=True),
        _line("第一条 条文内容", y0=30, size=12),
        _line("一、列表项", y0=50, size=10),
        _line("正文段落", y0=70, size=10),
    ]
    md = generate_markdown(lines)
    assert "## 第一章 总则" in md
    assert "#### 第一条 条文内容" in md
    assert "#### 一、列表项" in md
    assert "正文段落" in md


# ── 双栏列序重建 ─────────────────────────────────────────

def test_reflow_left_then_right():
    left1 = _line("左栏第一条", y0=10, x0=50, x1=150)
    right1 = _line("右栏第一条", y0=10, x0=300, x1=400)
    left2 = _line("左栏第二条", y0=30, x0=50, x1=150)
    right2 = _line("右栏第二条", y0=30, x0=300, x1=400)
    result = reflow_two_columns(
        [right1, left1, right2, left2], split_x=225, page_width=500
    )
    assert [ln.text for ln in result.lines] == [
        "左栏第一条", "左栏第二条", "右栏第一条", "右栏第二条",
    ]
    assert result.n_left == 2
    assert result.n_right == 2


def test_reflow_full_width_title_first_and_once():
    title = _line("跨栏标题", y0=5, x0=10, x1=490, size=16)
    left1 = _line("左栏内容", y0=20, x0=50, x1=150)
    right1 = _line("右栏内容", y0=20, x0=300, x1=400)
    result = reflow_two_columns(
        [right1, title, left1], split_x=225, page_width=500
    )
    assert result.lines[0].text == "跨栏标题"
    assert result.n_full_width == 1
    # 跨栏标题只出现一次
    assert [ln.text for ln in result.lines].count("跨栏标题") == 1


def test_is_full_width_line_threshold():
    assert is_full_width_line(_line("宽", y0=10, x0=10, x1=490), page_width=500, ratio=0.7) is True
    assert is_full_width_line(_line("窄", y0=10, x0=50, x1=150), page_width=500, ratio=0.7) is False
