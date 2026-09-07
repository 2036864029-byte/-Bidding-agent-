# 功能：只读干跑——P2（法规名→H1 + 沿革行降级）对书2/书3 的波及测量。
"""diag_p2_wave_book23 —— 在书2/书3 抽样页对比「现状（P2 后）heading_level_layout」
vs「P2 前旧规则」的标题决定差异，量化新增规则对书2/书3 的影响面。
不改任何生产代码/产物（只读复现，同 diag_rules_book3_on_book1 模式）。

P2 新增三处：① 沿革行降级（年份+通过/公布/施行/修正 → 正文）；
② 法规名→H1 规则（内容形态 + 间隙隔离/居中/粗字号证据）；③ gap_isolated 参数。
目标：书2/书3 零/可接受波及——重点盯「新增 H1」（法规名误伤）与
「H6→正文」（沿革行误伤正常列表项）。
"""
import glob
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

import pymupdf  # noqa: E402

from core.config.settings import Settings  # noqa: E402
from public_kb.ingestion.normalizers.pdf import pdf_paragraph as P  # noqa: E402
from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (  # noqa: E402
    iter_lines,
    remove_page_numbers,
    sort_single_column,
)
from public_kb.ingestion.normalizers.pdf.pdf_legal_page_classifier import (  # noqa: E402
    LegalPageClassifier,
)
from public_kb.ingestion.normalizers.pdf.pdf_page_profile import (  # noqa: E402
    build_page_profile,
)
from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (  # noqa: E402
    needs_two_column_defense,
    reflow_two_columns_v2,
)

s = Settings()
clf = LegalPageClassifier(
    min_text_chars=s.pdf_tiered_min_text_chars,
    image_area_ratio=s.pdf_tiered_image_area_ratio,
    two_col_confidence=s.pdf_tiered_two_col_confidence,
    img_min_text_chars=s.pdf_tiered_img_min_text_chars,
)


def old_heading_level_layout(line, m, *, col_right=None, gap_isolated=None):
    """P2 前旧规则（逐字节还原 09-06 定稿；gap_isolated 参数被调用方传入但旧规则不用）。"""
    text = line.text.strip()
    if not text or len(text) > 60:
        return None
    full_width = (
        col_right is not None
        and col_right - m.left_edge > 0
        and line.x1 >= col_right - 1.5 * m.body_size
    )
    size_ok = m.size_spread <= 0.06 * m.body_size
    big = size_ok and line.size >= m.body_size * 1.10
    bold_short = (
        line.bold
        and len(text) <= 40
        and not text.endswith(tuple(P._HEAD_BAD_TAIL))
        and not full_width
    )
    numbered_bold = (
        line.bold
        and len(text) <= 60
        and not full_width
        and bool(P._LIST_HEAD_RE.match(text))
    )
    if big or bold_short or numbered_bold:
        for pat, lv in P._CHAPTER_LEVEL_RE:
            if pat.match(text):
                return lv
        lv = P._list_pattern_level(text)
        if lv is not None:
            return lv
        if line.centered and line.size >= m.body_size * 1.2:
            return 1 if line.size >= m.body_size * 1.4 else 2
        if line.bold:
            return 4
        return 3 if line.size >= m.body_size * 1.3 else 4
    if full_width and P._LIST_HEAD_RE.match(text):
        return None
    return P._pattern_level(text)


def headings_of(lines):
    blocks = P.assemble_paragraphs(lines)
    return {b.text: b.level for b in blocks if b.kind == "heading"}


def sample_page(doc, i, s, clf):
    page = doc.load_page(i)
    decision = clf.classify(
        build_page_profile(
            page, i, background_img_ratio=s.pdf_tiered_background_img_ratio
        )
    )
    if decision.tier != "A":
        return None, decision
    lines = remove_page_numbers(iter_lines(page))
    page_width = float(page.rect.width or 0)
    if decision.page_label == "two_col_text" or needs_two_column_defense(
        lines, page_width=page_width
    ):
        profile = build_page_profile(
            page, i, background_img_ratio=s.pdf_tiered_background_img_ratio
        )
        split_x = profile.two_col_split_x or page_width / 2.0
        ordered = reflow_two_columns_v2(lines, split_x=split_x, page_width=page_width)
    else:
        ordered = sort_single_column(lines)
    return ordered, decision


def measure(path, body_start_frac=0.15, n_sample=30) -> dict:
    doc = pymupdf.open(path)
    total = doc.page_count
    start = int(total * body_start_frac)
    end = total
    k_total = max(end - start, 1)
    idxs = [start + round(k * k_total / n_sample) for k in range(n_sample)]
    idxs = sorted(set(min(idx, end - 1) for idx in idxs))

    orig_fn = P.heading_level_layout
    stat = {"new_h1": [], "demoted": [], "other": [], "skipped": []}
    try:
        for i in idxs:
            ordered, decision = sample_page(doc, i, s, clf)
            if ordered is None:
                stat["skipped"].append((i + 1, decision.page_label, decision.reason))
                continue
            P.heading_level_layout = old_heading_level_layout
            cur = headings_of(ordered)
            P.heading_level_layout = orig_fn
            new = headings_of(ordered)
            for t in set(cur) | set(new):
                a, b = cur.get(t), new.get(t)
                if a == b:
                    continue
                if b == 1 and a is None:
                    stat["new_h1"].append((i + 1, t, a, b))
                elif a is not None and b is None:
                    stat["demoted"].append((i + 1, t, a, b))
                else:
                    stat["other"].append((i + 1, t, a, b))
    finally:
        P.heading_level_layout = orig_fn
    return stat


def main() -> int:
    paths = {
        "书2 解读": "DATA/raw_data/law_pdf/招标投标法律解读与风险防范实务 (白如银) (Z-Library).pdf",
        "书3 1200问": "DATA/raw_data/law_pdf/政府采购、工程招标、投标与评标1200问（第3版）_刘海桑 编著.pdf",
    }
    bad = 0
    for label, path in paths.items():
        if not Path(path).exists():
            print(f"缺失: {path}")
            bad = 1
            continue
        st = measure(path)
        print(f"\n===== {label}: {path.split('/')[-1][:30]}… =====")
        print(f"  抽样页 {len(st['skipped'])} 页跳过（非 Tier A）：")
        for pg, lb, rs in st["skipped"]:
            print(f"    skip p{pg}: {lb} ({rs})")
        for tag, rows in [("新增 H1（法规名误伤风险）", st["new_h1"]),
                          ("标题→正文（沿革行/规则误伤）", st["demoted"]),
                          ("其它级变化", st["other"])]:
            print(f"  {tag}: {len(rows)}")
            for pg, t, a, b in rows[:15]:
                old = a if a is not None else "正文"
                nv = b if b is not None else "正文"
                print(f"    p{pg} 「{t[:40]}」 {old}→{nv}")
            if len(rows) > 15:
                print(f"    …（共 {len(rows)} 条）")
        if st["new_h1"] or st["demoted"]:
            bad = 1
    print("\n== 波及判定：", "超阈（需人工复核）" if bad else "零/可接受 ==")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
