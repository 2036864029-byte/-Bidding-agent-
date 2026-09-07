# 功能：版面还原链路真实数据诊断——对任意 PDF 抽样/连续取页，跑当前唯一解析+clean 链，
#      输出布局指标与字符保真（灰度开关 pdf_layout_v2 删除后，v1 对比侧随之退役，2026-09-07）。
"""pilot_book2_layout —— 版面还原计划 P1~P4 真实数据试点（诊断脚本，单链路版）。

用法：
  python scripts/pilot_book2_layout.py                # 默认书2（白如银），均匀抽 50 页
  python scripts/pilot_book2_layout.py --pdf <path> --num-pages 50 --start 15

流程：每页走当前唯一链路
  iter_lines → 去页码/页眉 → 串栏防御（two_col 标记页或防御信号 → reflow_two_columns_v2，
  否则 sort_single_column）→ paragraph_markdown（三判据段落化 + 版面锚定标题）
  → assemble（--continuous 开跨页续段）→ clean_markdown

产出（DATA/raw_data/pilot_book2_v2/）：
  layout.md（清洗后全文）、fidelity_report.txt（页级字符保真，对照源文本行）、
  指标打印：≤30 字行占比（G1）、标题数/标题正文比（G3）、正文行数。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pymupdf  # noqa: E402

from core.config.settings import Settings  # noqa: E402
from public_kb.ingestion.handoff import clean_markdown  # noqa: E402
from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (  # noqa: E402
    detect_header_candidates,
    iter_lines,
    remove_header_lines,
    remove_page_numbers,
    sort_single_column,
)
from public_kb.ingestion.normalizers.pdf.pdf_markdown_assembler import (  # noqa: E402
    ParsedPage,
    assemble_pages,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import (  # noqa: E402
    build_page_profile,
)
from public_kb.ingestion.normalizers.pdf.pdf_paragraph import (  # noqa: E402
    paragraph_markdown,
)
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (  # noqa: E402
    needs_two_column_defense,
    reflow_two_columns_v2,
)

_DEFAULT_PDF = (
    _PROJECT_ROOT / "DATA" / "raw_data" / "law_pdf"
    / "招标投标法律解读与风险防范实务 (白如银) (Z-Library).pdf"
)
_OUT_DIR = _PROJECT_ROOT / "DATA" / "raw_data" / "pilot_book2_v2"

_CJK_RE = re.compile(r"[一-鿿]")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def _body_lines(markdown: str) -> list[str]:
    return [
        ln.strip() for ln in markdown.split("\n")
        if ln.strip()
        and not ln.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
    ]


def _metrics(markdown: str) -> dict:
    body = _body_lines(markdown)
    headings = [
        m.group(2).strip() for ln in markdown.split("\n")
        if (m := _MD_HEADING_RE.match(ln.strip()))
    ]
    levels = Counter(
        len(m.group(1)) for ln in markdown.split("\n")
        if (m := _MD_HEADING_RE.match(ln.strip()))
    )
    short = sum(1 for ln in body if len(ln) <= 30)
    return {
        "body_lines": len(body),
        "headings": len(headings),
        "levels": dict(sorted(levels.items())),
        "short_le30": short,
        "short_ratio": short / len(body) if body else 0.0,
        "head_ratio": len(headings) / len(body) if body else 0.0,
        "median_body_len": sorted(len(ln) for ln in body)[len(body) // 2] if body else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=str(_DEFAULT_PDF))
    ap.add_argument("--num-pages", type=int, default=50)
    ap.add_argument("--start", type=int, default=15, help="正文起始页（前置区跳过）")
    ap.add_argument(
        "--continuous", action="store_true",
        help="连续取 start~start+num-pages 页（跨页续段启用）",
    )
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    total = doc.page_count
    n = min(args.num_pages, total - args.start)
    if args.continuous:
        # 连续取页（测跨页续段 + 前缀页真实形态）
        idxs = list(range(args.start, args.start + n))
    else:
        # 均匀抽样（非连续取页——测版面质量，不测跨页续段）
        idxs = sorted({args.start + round(k * (total - args.start - 1) / (n - 1)) for k in range(n)})

    # 文档级页眉候选（预扫抽样页）
    lines_list = [iter_lines(doc[i]) for i in idxs]
    header_candidates = detect_header_candidates(
        lines_list, page_heights=[doc[i].rect.height for i in idxs]
    )
    print(f"页眉候选: {sorted(header_candidates)}")

    pages, fidelity_rows = [], []
    for page_idx, lines in zip(idxs, lines_list):
        # 当前唯一链路：同一行流 → 串栏防御 → 段落化
        ordered_lines = remove_header_lines(
            remove_page_numbers(lines), header_candidates,
            page_height=float(doc[page_idx].rect.height or 0),
        )
        page_width = float(doc[page_idx].rect.width or 0)
        profile = build_page_profile(
            doc[page_idx],
            page_idx,
            background_img_ratio=0.35,
        )
        if needs_two_column_defense(ordered_lines, page_width=page_width):
            split_x = profile.two_col_split_x or page_width / 2.0
            ordered = reflow_two_columns_v2(
                ordered_lines, split_x=split_x, page_width=page_width
            )
        else:
            ordered = sort_single_column(ordered_lines)
        md = paragraph_markdown(ordered)
        pages.append(ParsedPage(page_idx=page_idx, markdown=md, parser="fast_text"))
        c_in = Counter(ch for ln in lines for ch in ln.text if _CJK_RE.match(ch))
        c_out = Counter(ch for ch in md if _CJK_RE.match(ch))
        if c_in != c_out:
            lost = {ch: c_in[ch] - c_out.get(ch, 0) for ch in c_in if c_in[ch] > c_out.get(ch, 0)}
            gained = {ch: c_out[ch] - c_in.get(ch, 0) for ch in c_out if c_out[ch] > c_in.get(ch, 0)}
            fidelity_rows.append(f"p{page_idx + 1}: lost={lost} gained={gained}")

    doc_md = assemble_pages(
        pages, page_marker=True, join_cross_page=args.continuous
    )
    clean = clean_markdown(doc_md, Settings())
    m = _metrics(clean)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / "layout.md").write_text(clean, encoding="utf-8")
    (_OUT_DIR / "sampled_pages.txt").write_text(
        " ".join(str(i + 1) for i in idxs), encoding="utf-8"
    )
    fid = "\n".join(fidelity_rows) if fidelity_rows else "全部抽样页 CJK 字符保真 100%"
    (_OUT_DIR / "fidelity_report.txt").write_text(fid, encoding="utf-8")

    print(f"\n抽样页数: {len(idxs)}（p{idxs[0] + 1} ~ p{idxs[-1] + 1}"
          f"{' 连续' if args.continuous else ' 均匀分布'}）")
    print(f"{'指标':<18} {'当前链路':>14}")
    print("-" * 50)
    print(f"{'正文行数':<18} {m['body_lines']:>14}")
    print(f"{'标题行数':<18} {m['headings']:>14}")
    print(f"{'标题/正文比':<18} {m['head_ratio']:>14.1%}")
    print(f"{'≤30字行占比(G1)':<18} {m['short_ratio']:>14.1%}")
    print(f"{'正文行长中位数':<18} {m['median_body_len']:>14}")
    print(f"标题级别分布: {m['levels']}")
    print(f"\n字符保真: {len(fidelity_rows)}/{len(idxs)} 页存在差异（详见 fidelity_report.txt）")
    print(f"产出目录: {_OUT_DIR}")


if __name__ == "__main__":
    main()
