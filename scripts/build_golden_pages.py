# -*- coding: utf-8 -*-
"""黄金页对构建（Phase A A5）— 从《全书》抽代表页，冻结「页 → 抽取文本」。

黄金页对 = 「PDF 页 → 当前抽取层输出的 markdown」快照，覆盖通用页型（双栏/
单栏/章首页/条首页/页眉泄漏页/前置页/末页）。提交到 `test/golden/pages/
quanshu.json`，供 `test/test_golden_pages.py` 做页级内容保真回归（任何抽取/
清洗改动不得改变这些页的输出），也供人工核验「PDF 原文 vs 抽取文本」。

抽取口径与 pdf_router worker 完全一致（profile → classify → two_col 走
reflow_page_markdown / 单栏走 extract_page_markdown，均带文档级页眉候选）。

全书逐页扫描约 3.5 分钟（一次性的慢构建）；抽样结果写入后人工复核即可。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymupdf

from core.config.settings import Settings
from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
    detect_header_candidates,
    extract_page_markdown,
    iter_lines,
    normalize_header_text,
)
from public_kb.ingestion.normalizers.pdf.pdf_legal_page_classifier import (
    LegalPageClassifier,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import build_page_profile
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import reflow_page_markdown

PDF_DIR = Path("DATA/raw_data/law_pdf")
OUT = ROOT / "test" / "golden" / "pages" / "quanshu.json"
QUANSHU = (
    "中华人民共和国招标投标法律法规全书：含相关政策 "
    "(法律法规出版社法规中心) (Z-Library)(OCR).pdf"
)

_CHAPTER_RE = re.compile(r"^#{1,6}\s+第[一二三四五六七八九十百千\d]+[编章节]")
_ARTICLE_RE = re.compile(r"^#{1,6}\s+第[一二三四五六七八九十百千\d]+条")
_PAGE_NUM_RE = re.compile(r"^\s*\d{1,4}\s*$")


def _page_types(md: str, first_raw: str, header_candidates: set, is_last: bool):
    types = []
    if header_candidates and normalize_header_text(first_raw) in header_candidates:
        types.append("header_leak")
    if _CHAPTER_RE.match(md.strip()):
        types.append("chapter_start")
    if _ARTICLE_RE.match(md.strip()):
        types.append("article_start")
    if is_last:
        types.append("last_page")
    return types


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", default=str(PDF_DIR))
    parser.add_argument("--max-pages", type=int, default=12)
    args = parser.parse_args()

    pdf_path = Path(args.pdf_dir) / QUANSHU
    if not pdf_path.exists():
        print(f"PDF 缺失: {pdf_path}")
        return 2

    settings = Settings()
    classifier = LegalPageClassifier(
        min_text_chars=settings.pdf_tiered_min_text_chars,
        image_area_ratio=settings.pdf_tiered_image_area_ratio,
        two_col_confidence=settings.pdf_tiered_two_col_confidence,
    )
    doc = pymupdf.open(str(pdf_path))
    n = doc.page_count
    pages = []  # (idx, label, profile, lines)
    page_heights: list[float] = []
    try:
        for idx in range(n):
            page = doc.load_page(idx)
            page_heights.append(float(page.rect.height or 0))
            profile = build_page_profile(
                page, idx,
                background_img_ratio=settings.pdf_tiered_background_img_ratio,
            )
            decision = classifier.classify(profile)
            lines = iter_lines(page)
            pages.append((idx, decision.page_label, profile, lines))
        header_candidates = set(detect_header_candidates(
            [pl for _, _, _, pl in pages],
            page_heights=page_heights,
            min_pages=settings.pdf_header_repeat_min,
            min_repeat=settings.pdf_header_repeat_plain,
            max_len=settings.pdf_header_max_len,
        ))

        # 收集页型，取每型首个/代表性页
        selected: dict[str, int] = {}
        special_seen = set()
        for idx, label, profile, lines in pages:
            page = doc.load_page(idx)
            if label == "two_col_text":
                md = reflow_page_markdown(
                    lines,
                    split_x=profile.two_col_split_x,
                    page_width=profile.width,
                    header_candidates=sorted(header_candidates),
                )
            elif label == "text":
                md = extract_page_markdown(
                    page, idx, header_candidates=sorted(header_candidates)
                )
            else:
                continue  # 非 Tier A 页不入选（MinerU 等）
            raw = page.get_text("text") or ""
            first_raw = next(
                (l.strip() for l in raw.split("\n") if l.strip()), ""
            )
            types = _page_types(
                md, first_raw, header_candidates, is_last=(idx == n - 1)
            )
            for t in types:
                if t not in special_seen and len(special_seen) < args.max_pages:
                    special_seen.add(t)
                    selected.setdefault(t, idx)
            if label == "text" and "single_col" not in special_seen:
                special_seen.add("single_col")
                selected.setdefault("single_col", idx)

        # 若页型覆盖不足，补典型双栏页
        picked = list(selected.values())
        for idx, label, profile, _ in pages:
            if len(picked) >= args.max_pages:
                break
            if idx in picked or label != "two_col_text":
                continue
            picked.append(idx)
            selected.setdefault(f"two_col_{idx}", idx)
        picked = sorted(selected.values())[: args.max_pages]

        golden = {
            "book": QUANSHU,
            "schema_version": 1,
            "header_candidates": sorted(header_candidates),
            "pages": [],
        }
        for idx in picked:
            label = next(l for i, l, _, _ in pages if i == idx)
            profile = next(p for i, _, p, _ in pages if i == idx)
            lines = next(pl for i, _, _, pl in pages if i == idx)
            page = doc.load_page(idx)
            if label == "two_col_text":
                md = reflow_page_markdown(
                    lines,
                    split_x=profile.two_col_split_x,
                    page_width=profile.width,
                    header_candidates=sorted(header_candidates),
                )
            else:
                md = extract_page_markdown(
                    page, idx, header_candidates=sorted(header_candidates)
                )
            types = [t for t, i in selected.items() if i == idx]
            golden["pages"].append({
                "idx": idx,
                "label": label,
                "types": types,
                "markdown": md,
            })
    finally:
        doc.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(golden, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"黄金页对 {len(picked)} 页 → {OUT}")
    for p in golden["pages"]:
        print(f"  p{p['idx']} [{p['label']}] {p['types']} chars={len(p['markdown'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
