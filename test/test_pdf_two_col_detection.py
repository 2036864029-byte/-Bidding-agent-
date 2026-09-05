"""修复②回归：has_two_col 须两侧成簇多行才算双栏（单栏+孤立标题不再误判）。"""

from __future__ import annotations

import pytest

pymupdf = pytest.importorskip("pymupdf")

from public_kb.ingestion.normalizers.pdf.pdf_page_profile import build_page_profile


def _build(lines_left, lines_right, isolated=None):
    """构造一页：左栏 lines_left 行、右栏 lines_right 行，另加 isolated 个孤立标题块。"""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    y = 60
    for _ in range(lines_left):
        page.insert_text((50, y), "左栏正文行内容足够长用于构成文本块。", fontname="china-s")
        y += 20
    y = 60
    for _ in range(lines_right):
        page.insert_text((320, y), "右栏正文行内容足够长用于构成文本块。", fontname="china-s")
        y += 20
    for i in range(isolated or 0):
        page.insert_text((250, 700 - i * 20), "居中孤立标题", fontname="china-s")
    prof = build_page_profile(page, 0)
    doc.close()
    return prof


def test_single_col_with_isolated_heading_not_two_col():
    # 修复前：左栏 20 行 + 右侧 1 个孤立标题 → x0 间隙 > 15% 页宽 → 误判双栏
    prof = _build(lines_left=20, lines_right=0, isolated=1)
    assert prof.has_two_col is False


def test_single_col_page_not_two_col():
    prof = _build(lines_left=20, lines_right=0)
    assert prof.has_two_col is False


def test_true_two_col_detected():
    prof = _build(lines_left=12, lines_right=12)
    assert prof.has_two_col is True


def test_two_col_weak_side_below_min_not_detected():
    # 右栏仅 2 块（< min_side_blocks=3）→ 不判双栏
    prof = _build(lines_left=12, lines_right=2)
    assert prof.has_two_col is False
