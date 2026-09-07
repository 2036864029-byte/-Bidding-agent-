# 功能：只读统计三书文本层行首括号列表标记的全角/半角分布（决定是否补半角模式）。
"""diag_bracket_width —— 扫描三书全部页的行文本，统计行首
`（N）`（全角）vs `(N)`（半角）的出现次数与样例。只读，不改任何产物。
"""
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pymupdf

_PDF_DIR = _PROJECT_ROOT / "DATA" / "raw_data" / "law_pdf"
_BOOKS = [
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).pdf",
    "招标投标法律解读与风险防范实务 (白如银) (Z-Library).pdf",
    "政府采购、工程招标、投标与评标1200问（第3版）_刘海桑 编著.pdf",
]
_FULL = re.compile(r"^（\s*[0-9一二三四五六七八九十百]{1,4}\s*）")
_HALF = re.compile(r"^\(\s*[0-9一二三四五六七八九十百]{1,4}\)")
# 目录条目特征：点线引导 / 行尾页码——排除后才是真实列表标记
_TOC_NOISE = re.compile(r"[⋯…\.]{2,}|\s\(|\(\d+\)\s*$")

import os

for name in _BOOKS:
    path = _PDF_DIR / name
    if not path.exists():
        print(f"跳过（未找到）: {name}")
        continue
    doc = pymupdf.open(str(path))
    n_full = n_half = 0
    half_samples: list[str] = []
    # 书1 前置区 p1~14 跳过（用户决策）；其余书从 p1 起扫
    start = 14 if "(OCR)" in name else 0
    for pno in range(start, doc.page_count):
        for raw in doc[pno].get_text("text").split("\n"):
            t = raw.strip()
            if not t:
                continue
            if _FULL.match(t):
                n_full += 1
            elif _HALF.match(t) and not _TOC_NOISE.search(t):
                n_half += 1
                if len(half_samples) < 8:
                    half_samples.append(f"p{pno + 1}: {t[:40]}")
    short = name[:12]
    print(f"{short}… 正文区 全角（N）: {n_full}  半角(N): {n_half}")
    for s in half_samples:
        print(f"   {s}")
