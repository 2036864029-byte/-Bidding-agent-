"""Phase A A3-4 表格引擎注册表单元测试（table_extract.py）。"""

from __future__ import annotations

import pymupdf

from public_kb.ingestion.normalizers.pdf.table_extract import (
    ENGINES,
    extract_tables,
    find_tables_engine,
    html_engine,
)


def _gridded_pdf() -> pymupdf.Document:
    """构造一张含矢量网格表格的 PDF（3 行 × 3 列，有边框线 + 文本层）。"""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    x0, y0 = 50, 50
    xs = [0, 100, 200, 300]
    ys = [0, 40, 80, 120]
    for x in xs:
        page.draw_line((x0 + x, y0), (x0 + x, y0 + ys[-1]))
    for y in ys:
        page.draw_line((x0, y0 + y), (x0 + xs[-1], y0 + y))
    texts = [("名称", 0, 0), ("金额", 1, 0), ("数量", 2, 0),
             ("甲", 0, 1), ("100", 1, 1), ("2", 2, 1),
             ("乙", 0, 2), ("200", 1, 2), ("3", 2, 2)]
    for text, cx, cy in texts:
        page.insert_text(
            (x0 + xs[cx] + 5, y0 + ys[cy] + 28), text, fontname="china-s"
        )
    return doc


def _two_column_page() -> pymupdf.Document:
    """构造无网格的双栏文本页（find_tables 默认策略不应误判为表格）。"""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    for y in range(60, 500, 30):
        page.insert_text((30, y), f"左栏正文行{y}")
        page.insert_text((230, y), f"右栏正文行{y}")
    return doc


def test_find_tables_engine_extracts_gridded_table():
    doc = _gridded_pdf()
    tables = find_tables_engine(doc.load_page(0))
    doc.close()
    assert len(tables) == 1
    rows = tables[0].split("\n")
    assert rows[0].startswith("| ") and rows[0].endswith(" |")
    assert "名称" in rows[0] and "金额" in rows[0]
    assert "甲" in tables[0] and "100" in tables[0]


def test_find_tables_default_no_false_positive_on_two_column():
    # 双栏无网格页：默认(lines_strict)策略不得把栏排版当表格（存量核实教训）
    doc = _two_column_page()
    tables = find_tables_engine(doc.load_page(0))
    doc.close()
    assert tables == []


def test_extract_tables_routes_pdf_page_to_find_tables():
    doc = _gridded_pdf()
    tables = extract_tables(doc.load_page(0))
    doc.close()
    assert tables and "甲" in tables[0]


def test_extract_tables_unknown_engine_raises():
    import pytest

    with pytest.raises(KeyError):
        extract_tables("dummy", engine="not_exist")


def test_html_engine_extracts_table():
    html = """
    <html><body>
      <table>
        <tr><th>项目</th><th>金额</th></tr>
        <tr><td>甲</td><td>100</td></tr>
        <tr><td>乙</td><td>200</td></tr>
      </table>
    </body></html>
    """
    tables = html_engine(html)
    assert len(tables) == 1
    assert "| 项目 | 金额 |" in tables[0]
    assert "| --- | --- |" in tables[0]
    assert "| 甲 | 100 |" in tables[0]


def test_html_engine_empty_table_skipped():
    assert html_engine("<html><table></table></html>") == []


def test_registry_has_expected_slots():
    # 槽位预留声明（PaddleOCR / 列聚类 未实现，触发条件到达再装）
    from public_kb.ingestion.normalizers.pdf.table_extract import (
        COLUMN_CLUSTER_ENGINE,
        PADDLEOCR_ENGINE,
    )

    assert "find_tables" in ENGINES and "html" in ENGINES
    assert PADDLEOCR_ENGINE == "paddleocr"
    assert COLUMN_CLUSTER_ENGINE == "column_cluster"
