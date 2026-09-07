# 功能：诊断书2 列表项折行误判标题——打印目标行的版面证据（x0/x1/size/bold/y0）。
"""diag_book2_listitem —— 定位包含目标文本的页，打印该页全部行版面证据，
对照（1）~（5）单行项与（2）/（4）折行项的 bold/size/x1 差异。
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pymupdf

from public_kb.ingestion.normalizers.pdf.pdf_fast_text import iter_lines

_PDF = (
    _PROJECT_ROOT / "DATA" / "raw_data" / "law_pdf"
    / "招标投标法律解读与风险防范实务 (白如银) (Z-Library).pdf"
)
_NEEDLES = ("明确信用信息查", "查询有关信")

doc = pymupdf.open(str(_PDF))
for pno in range(15, 70):
    page_text = doc[pno].get_text("text")
    if any(n in page_text.replace("\n", "") for n in _NEEDLES):
        print(f"=== p{pno + 1} ===")
        for ln in iter_lines(doc[pno]):
            t = ln.text.strip()
            if not t:
                continue
            print(
                f"x0={ln.x0:7.1f} x1={ln.x1:7.1f} y0={ln.y0:7.1f} "
                f"size={ln.size:5.2f} bold={int(ln.bold)} cent={int(ln.centered)} | {t[:44]}"
            )
        break
