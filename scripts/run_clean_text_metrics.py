# -*- coding: utf-8 -*-
"""文本级保真回归脚本（Phase A A5）— 抽取层指标 vs 提交基线。

对照 `docs/plan1/数据修复/数据解析质量评估报告_20260904.md` 的基线数字，对
handoff 三本电子书跑「当前抽取层（after）」指标，与 `test/golden/
clean_text_metrics.json`（提交基线）比对，指标不劣化才算通过——任何抽取/清洗
改动必须过保真比对（正面回应「几百个测试没守住质量」：既有单测守行为/形状，
本脚本守内容保真）。

指标（与评估报告同口径）：
  header_leak      页眉候选仍出现在输出中的行数
  page_number_res  输出中独立纯数字行数（页码残留）
  first_line_loss  页首「非页眉/非页码」正文行在输出中缺失的页数（正文首行误删）
  chars            输出总字符数（供上下文参考，不判回归）

用法：
  python scripts/run_clean_text_metrics.py                    # 三本书全跑
  python scripts/run_clean_text_metrics.py 全书                # 单本
  python scripts/run_clean_text_metrics.py --update-baseline   # 达标后刷新提交基线
退出码：任一指标劣化 → 1；通过 → 0。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # 独立脚本运行需仓库根在 sys.path

import pymupdf

from core.config.settings import Settings
from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
    detect_header_candidates,
    generate_markdown,
    iter_lines,
    normalize_header_text,
    remove_header_lines,
    remove_page_numbers,
    sort_single_column,
)
from public_kb.ingestion.normalizers.pdf.pdf_legal_page_classifier import (
    LegalPageClassifier,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import build_page_profile
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import reflow_two_columns

PDF_DIR = Path("DATA/raw_data/law_pdf")
BASELINE = ROOT / "test" / "golden" / "clean_text_metrics.json"

BOOKS = {
    "全书": "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).pdf",
    "白如银": "招标投标法律解读与风险防范实务 (白如银) (Z-Library).pdf",
    "1200问": "政府采购、工程招标、投标与评标1200问（第3版）_刘海桑 编著.pdf",
}

_PAGE_NUM_RE = re.compile(r"^\s*\d{1,4}\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+")


def _extract_after(lines, is_two_col, split_x, page_width, header_candidates):
    """当前抽取层（after）逐页路径，与 pdf_router worker 同口径。"""
    if is_two_col:
        kept = remove_page_numbers(lines)
        kept = remove_header_lines(kept, header_candidates)
        result = reflow_two_columns(kept, split_x=split_x, page_width=page_width)
        return generate_markdown(result.lines)
    kept = remove_page_numbers(lines)
    kept = remove_header_lines(kept, header_candidates)
    kept = sort_single_column(kept)
    return generate_markdown(kept)


def _line_present(text: str, md: str) -> bool:
    t = text.strip()
    return bool(t) and t in md


def run_book_metrics(pdf_path: Path, settings: Settings) -> dict:
    """跑一本书的抽取层指标（after 路径），返回与基线同构的 dict。"""
    classifier = LegalPageClassifier(
        min_text_chars=settings.pdf_tiered_min_text_chars,
        image_area_ratio=settings.pdf_tiered_image_area_ratio,
        two_col_confidence=settings.pdf_tiered_two_col_confidence,
    )
    doc = pymupdf.open(str(pdf_path))
    try:
        pages = []
        for idx in range(doc.page_count):
            page = doc.load_page(idx)
            profile = build_page_profile(
                page, idx,
                background_img_ratio=settings.pdf_tiered_background_img_ratio,
            )
            decision = classifier.classify(profile)
            pages.append((page, iter_lines(page), profile, decision))

        header_candidates = detect_header_candidates(
            [pl for _, pl, _, _ in pages],
            page_heights=[float(p.rect.height or 0) for p, _, _, _ in pages],
            min_pages=settings.pdf_header_repeat_min,
            min_repeat=settings.pdf_header_repeat_plain,
            max_len=settings.pdf_header_max_len,
        )
        after = Counter()
        n_tier_a = n_two_col = 0
        first_checked = first_loss = 0
        for page, lines, profile, decision in pages:
            label = decision.page_label
            if label not in ("two_col_text", "text"):
                continue
            n_tier_a += 1
            is_two_col = label == "two_col_text"
            if is_two_col:
                n_two_col += 1
            md = _extract_after(
                lines, is_two_col, profile.two_col_split_x, profile.width,
                header_candidates,
            )
            stripped = [l.strip() for l in md.split("\n") if l.strip()]
            after["header_leak"] += sum(
                1 for l in stripped
                if normalize_header_text(l) in header_candidates
            )
            after["page_number_res"] += sum(1 for l in stripped if _PAGE_NUM_RE.match(l))
            after["chars"] += len(md)

            raw = page.get_text("text") or ""
            raw_lines = [l.strip() for l in raw.split("\n") if l.strip()]
            if raw_lines:
                first = raw_lines[0]
                if normalize_header_text(first) in header_candidates:
                    continue
                if _PAGE_NUM_RE.match(first):
                    continue
                first_checked += 1
                if not _line_present(first, md):
                    first_loss += 1
    finally:
        doc.close()
    return {
        "tier_a_pages": n_tier_a,
        "two_col_pages": n_two_col,
        "header_leak": after["header_leak"],
        "page_number_res": after["page_number_res"],
        "first_line_loss": first_loss,
        "first_line_checked": first_checked,
        "chars": after["chars"],
    }


def compare(book: str, current: dict, baseline: dict) -> list[str]:
    """返回劣化/变化说明列表；空 = 通过。"""
    notes: list[str] = []
    for metric in ("header_leak", "page_number_res", "first_line_loss"):
        cur, base = current[metric], baseline.get(metric)
        if base is None:
            notes.append(f"基线缺失 {metric}")
        elif cur > base:
            notes.append(f"{metric} 劣化 {base} → {cur}")
        elif cur < base:
            notes.append(f"{metric} 改善 {base} → {cur}（可更新基线）")
    if current["tier_a_pages"] != baseline.get("tier_a_pages"):
        notes.append(
            f"tier_a_pages 变化 {baseline.get('tier_a_pages')} → {current['tier_a_pages']}"
        )
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "book", nargs="?", default=None,
        help="书名简写（全书/白如银/1200问）；缺省跑全部",
    )
    parser.add_argument("--pdf-dir", default=str(PDF_DIR), help="PDF 目录")
    parser.add_argument("--update-baseline", action="store_true",
                        help="用本次结果刷新提交基线（达标后人工确认再执行）")
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.exists():
        print(f"PDF 目录不存在: {pdf_dir}（本机回归门禁，需 DATA/raw_data/law_pdf）")
        return 2
    baseline = (
        json.loads(BASELINE.read_text(encoding="utf-8"))["books"]
        if BASELINE.exists() else {}
    )
    settings = Settings()
    targets = [args.book] if args.book else list(BOOKS)
    fail = False
    current_books = {}
    for book in targets:
        pdf_path = pdf_dir / BOOKS[book]
        if not pdf_path.exists():
            print(f"✗ {book}: PDF 缺失 {pdf_path.name}")
            fail = True
            continue
        cur = run_book_metrics(pdf_path, settings)
        current_books[book] = cur
        base = baseline.get(book, {})
        notes = compare(book, cur, base)
        print(f"== {book} == Tier A={cur['tier_a_pages']}（双栏 {cur['two_col_pages']}） "
              f"header_leak={cur['header_leak']} page_number_res={cur['page_number_res']} "
              f"first_line_loss={cur['first_line_loss']}/{cur['first_line_checked']}")
        for note in notes:
            print(f"   {note}")
        if any(n.startswith(("劣化", "基线缺失", "tier_a")) for n in notes):
            fail = True
    if args.update_baseline:
        # 显式快照：用本次结果覆盖提交基线（含首次建基线）。达标后人工确认再执行。
        data = {"schema_version": 1, "books": current_books}
        BASELINE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"基线已更新: {BASELINE}")
    elif fail:
        print("存在劣化/缺失，未更新基线。")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
