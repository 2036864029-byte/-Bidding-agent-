# 功能：查看某页各 x0 簇的行距样本分布。用法: python diag_gap_dist.py <pageno1> <pageno2>
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pymupdf

from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
    iter_lines, remove_header_lines, remove_page_numbers,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import build_page_profile
from public_kb.ingestion.normalizers.pdf.pdf_paragraph import (
    _cluster_lines_by_x0, compute_column_metrics,
)
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (
    needs_two_column_defense, reflow_two_columns_v2,
)

PDF = (
    ROOT / "DATA/raw_data/law_pdf_book1"
    / "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).pdf"
)

for arg in sys.argv[1:]:
    pageno = int(arg) - 1
    doc = pymupdf.open(str(PDF))
    page = doc.load_page(pageno)
    lines = remove_page_numbers(iter_lines(page))
    lines = remove_header_lines(lines, set(), page_height=float(page.rect.height or 0))
    pw = float(page.rect.width or 0)
    profile = build_page_profile(page, pageno)
    split_x = profile.two_col_split_x or pw / 2
    ordered = reflow_two_columns_v2(lines, split_x=split_x, page_width=pw)
    m = compute_column_metrics(ordered)
    print(f"=== page {pageno + 1} line_gap={m.line_gap:.2f} body={m.body_size:.2f}")
    clusters = _cluster_lines_by_x0([ln for ln in ordered if ln.text.strip()],
                                    m.right_edge - m.left_edge)
    for ci, cluster in enumerate(clusters):
        xs = sorted(ln.x0 for ln in cluster)
        ys = sorted(ln.y0 for ln in cluster)
        gs = [b - a for a, b in zip(ys, ys[1:]) if b - a >= m.body_size * 0.5]
        binned = Counter(round(g) for g in gs)
        print(f"  cluster {ci}: n={len(cluster)} x0=[{xs[0]:.0f}~{xs[-1]:.0f}] "
              f"gaps={dict(sorted(binned.items()))}")
    doc.close()
