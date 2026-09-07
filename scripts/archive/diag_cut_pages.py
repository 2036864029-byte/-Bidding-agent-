# 功能：诊断特定页的段落装配决策（可以否/受法 切断段）。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf

from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
    iter_lines,
    remove_header_lines,
    remove_page_numbers,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import build_page_profile
from public_kb.ingestion.normalizers.pdf.pdf_paragraph import (
    _cluster_left_edges,
    _is_raw_line,
    compute_column_metrics,
)
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (
    needs_two_column_defense,
    reflow_two_columns_v2,
)

PDF = (
    r"DATA/raw_data/law_pdf_book1/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).pdf"
)
FOCUS = ("可以否", "决所有投标", "履约保证金", "受法", "律保护", "中标人提交")

doc = pymupdf.open(PDF)
for pageno in (17, 33):
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
    print(f"=== page {pageno + 1} defense={defense} gap={m.line_gap:.2f} "
          f"body={m.body_size:.2f} spread={m.size_spread:.2f}")
    show = False
    for i, ln in enumerate(ordered):
        if not ln.text.strip():
            continue
        if any(k in ln.text for k in FOCUS):
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
        print(f"[{i:02d}] x0={ln.x0:6.1f} y0={ln.y0:6.1f} x1={ln.x1:6.1f} "
              f"sz={ln.size:.2f} ctr={int(ln.centered)} raw={raw} "
              f"brk={','.join(reasons) or '-'} | {ln.text[:26]!r}")
doc.close()
