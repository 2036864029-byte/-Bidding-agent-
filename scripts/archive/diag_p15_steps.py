# 只读诊断：书1 第15页 逐步骤复现 pdf_router worker 管线，定位 法规名/第一章/第一条 丢失环节。
import io, sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import pymupdf

from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
    detect_header_candidates,
    iter_lines,
    remove_header_lines,
    remove_page_numbers,
    sort_single_column,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import build_page_profile
from public_kb.ingestion.normalizers.pdf.pdf_paragraph import paragraph_markdown
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (
    needs_two_column_defense,
    reflow_two_columns_v2,
)

PDF = r"DATA/raw_data/law_pdf/中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).pdf"
PAGE_IDX = 14  # 0-based = 印刷第 15 页
OUT = "analysis_out/_diag_p15.txt"

out = io.open(OUT, "w", encoding="utf-8")
def w(*a):
    out.write(" ".join(str(x) for x in a) + "\n")

doc = pymupdf.open(PDF)
page = doc.load_page(PAGE_IDX)

# 1) 文档级 header_candidates（全页预扫，与 router 同口径）
all_lines = [iter_lines(doc[i]) for i in range(doc.page_count)]
header_candidates = detect_header_candidates(
    all_lines, page_heights=[doc[i].rect.height for i in range(doc.page_count)]
)
w("header_candidates:", sorted(header_candidates)[:20])

lines = iter_lines(page)
w(f"\n== 0) iter_lines n={len(lines)} ==")
for ln in lines:
    w(f"  y0={ln.y0:6.1f} x0={ln.x0:6.1f} x1={ln.x1:6.1f} size={ln.size:.1f} bold={ln.bold} | {ln.text}")

lines2 = remove_page_numbers(lines)
w(f"\n== 1) remove_page_numbers n={len(lines2)} ==")
for ln in lines2:
    w(f"  y0={ln.y0:6.1f} x0={ln.x0:6.1f} x1={ln.x1:6.1f} size={ln.size:.1f} | {ln.text}")

lines3 = remove_header_lines(lines2, header_candidates, page_height=float(page.rect.height or 0))
w(f"\n== 2) remove_header_lines n={len(lines3)} ==")
for ln in lines3:
    w(f"  y0={ln.y0:6.1f} x0={ln.x0:6.1f} x1={ln.x1:6.1f} size={ln.size:.1f} | {ln.text}")

page_width = float(page.rect.width or 0)
defense = needs_two_column_defense(lines3, page_width=page_width)
w(f"\n== 3) needs_two_column_defense = {defense}, page_width={page_width:.1f}")
if defense:
    profile = build_page_profile(page, PAGE_IDX, background_img_ratio=0.35)
    split_x = profile.two_col_split_x or page_width / 2.0
    w(f"  two_col_split_x = {profile.two_col_split_x}")
    ordered = reflow_two_columns_v2(lines3, split_x=split_x, page_width=page_width)
else:
    ordered = sort_single_column(lines3)
w(f"== 4) after reflow/sort n={len(ordered)} ==")
for ln in ordered:
    w(f"  y0={ln.y0:6.1f} x0={ln.x0:6.1f} x1={ln.x1:6.1f} size={ln.size:.1f} bold={ln.bold} | {ln.text}")

md = paragraph_markdown(ordered)
w(f"\n== 5) paragraph_markdown output ==")
w(md)

out.close()
print("written", OUT)
