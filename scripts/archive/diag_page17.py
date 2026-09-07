# 功能：复现 p17 装配——逐行打印段落装配决策（is_paragraph_start 三判据）。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf

from core.config.settings import Settings
from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
    detect_header_candidates,
    iter_lines,
    remove_header_lines,
    remove_page_numbers,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import build_page_profile
from public_kb.ingestion.normalizers.pdf.pdf_paragraph import (
    assemble_paragraphs,
    compute_column_metrics,
    is_paragraph_start,
    _is_raw_line,
    _cluster_left_edges,
)
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (
    needs_two_column_defense,
    reflow_two_columns_v2,
)

PDF = Path(
    r"DATA/raw_data/law_pdf_book1/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).pdf"
)
PAGE = 16  # 0-based → 书页 17

doc = pymupdf.open(str(PDF))
page = doc.load_page(PAGE)
lines = remove_page_numbers(iter_lines(page))
lines = remove_header_lines(lines, set())
pw = float(page.rect.width or 0)
print("page_width", pw, "n_lines", len(lines))

profile = build_page_profile(page, PAGE)
defense = needs_two_column_defense(lines, page_width=pw)
print("label", profile.label if hasattr(profile, "label") else "?",
      "two_col_split_x", profile.two_col_split_x, "defense", defense)
split_x = profile.two_col_split_x or pw / 2
ordered = reflow_two_columns_v2(lines, split_x=split_x, page_width=pw)
doc.close()

m = compute_column_metrics(ordered)
col_lefts = _cluster_left_edges(ordered, m)
print(f"metrics: left={m.left_edge:.1f} right={m.right_edge:.1f} gap={m.line_gap:.2f} "
      f"body={m.body_size:.2f} spread={m.size_spread:.2f} size_ok={m.size_spread <= 0.06*m.body_size}")

for i, ln in enumerate(ordered):
    if not ln.text.strip():
        continue
    prev = ordered[i - 1] if i > 0 else None
    reasons = []
    if prev is not None:
        left = col_lefts[i]
        if ln.x0 > left + 1.5 * m.body_size:
            reasons.append("indent")
        if prev.text.rstrip()[-1:] in "。！？…；" and prev.x1 < m.right_edge - 2 * m.body_size:
            reasons.append("prev_short")
        if ln.y0 - prev.y0 > 1.5 * m.line_gap:
            reasons.append(f"vgap({ln.y0 - prev.y0:.0f}>{1.5*m.line_gap:.0f})")
        raw = _is_raw_line(ln, m)
    else:
        reasons.append("first")
        raw = False
    print(f"[{i:02d}] x0={ln.x0:6.1f} y0={ln.y0:6.1f} x1={ln.x1:6.1f} "
          f"col={col_lefts[i]:6.1f} sz={ln.size:.2f} ctr={int(ln.centered)} "
          f"raw={int(raw)} brk={','.join(reasons) or '-'} | {ln.text[:30]!r}")
