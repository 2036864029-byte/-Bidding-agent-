# 功能：定位 raw 中某行所在页 → 复现该页装配决策。用法: python diag_page_decisions.py "工)招标投标活动"
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys as _sys

_sys.path.insert(0, str(ROOT))
import pymupdf

from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
    iter_lines, remove_header_lines, remove_page_numbers,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import build_page_profile
from public_kb.ingestion.normalizers.pdf.pdf_paragraph import (
    _cluster_left_edges, _is_raw_line, assemble_paragraphs,
    compute_column_metrics, heading_level_layout, is_paragraph_start,
)
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (
    needs_two_column_defense, reflow_two_columns_v2,
)

PDF = (
    ROOT / "DATA/raw_data/law_pdf_book1"
    / "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).pdf"
)
KEY = sys.argv[1] if len(sys.argv) > 1 else "工)招标投标活动"

doc = pymupdf.open(str(PDF))
hits = []
for pageno in range(doc.page_count):
    text = doc.load_page(pageno).get_text("text")
    if KEY in text.replace("\n", ""):
        hits.append(pageno)
print("pages containing key:", [p + 1 for p in hits][:5])

for pageno in hits[:2]:
    page = doc.load_page(pageno)
    lines = remove_page_numbers(iter_lines(page))
    lines = remove_header_lines(lines, set(), page_height=float(page.rect.height or 0))
    pw = float(page.rect.width or 0)
    profile = build_page_profile(page, pageno)
    split_x = profile.two_col_split_x or pw / 2
    defense = needs_two_column_defense(lines, page_width=pw)
    ordered = reflow_two_columns_v2(lines, split_x=split_x, page_width=pw)
    m = compute_column_metrics(ordered)
    col_lefts = _cluster_left_edges(ordered, m)
    print(f"\n=== page {pageno + 1} defense={defense} gap={m.line_gap:.2f} "
          f"body={m.body_size:.2f} spread={m.size_spread:.2f} left={m.left_edge:.1f} right={m.right_edge:.1f}")
    show = False
    for i, ln in enumerate(ordered):
        if not ln.text.strip():
            continue
        if KEY in ln.text:
            show = True
        if not show:
            continue
        prev = ordered[i - 1] if i > 0 else None
        reasons = []
        raw = int(_is_raw_line(ln, m, prev=prev))
        if prev is not None:
            left = col_lefts[i]
            if ln.x0 > left + 1.5 * m.body_size:
                reasons.append("indent")
            if (prev.text.rstrip()[-1:] in "。！？…；"
                    and prev.x1 < m.right_edge - 2 * m.body_size):
                reasons.append("prev_short")
            if ln.y0 - prev.y0 > 1.5 * m.line_gap:
                reasons.append(f"vgap({ln.y0 - prev.y0:.0f})")
        if heading_level_layout(ln, m) is not None:
            reasons.append("heading")
        print(f"[{i:02d}] x0={ln.x0:6.1f} y0={ln.y0:6.1f} x1={ln.x1:6.1f} "
              f"sz={ln.size:.2f} ctr={int(ln.centered)} raw={raw} "
              f"brk={','.join(reasons) or '-'} | {ln.text[:30]!r}")
        if reasons and ("heading" in reasons) and i > 0:
            pass
doc.close()
