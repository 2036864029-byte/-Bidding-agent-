# 功能：只读干跑——书3 定级规则对书1 的影响测量（用户要求的 30 页验证）。
"""diag_rules_book3_on_book1 —— 在书1 抽取 30 页，对比
「现状 heading_level_layout」 vs 「模拟规则①：编号级先于居中大字」的
标题决定差异，量化新增规则对书1 的波及面。不改任何生产代码/产物。

模拟的规则① = book3 问项定级修复的核心：big/bold 分支里先查
_list_pattern_level（一、→4 / （一）→5 / N.→6），命中即返回；
居中大字（H1/H2）不再吞掉编号行。编/章/节（_CHAPTER_LEVEL_RE）不受影响。
"""
import glob
import re
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


def simulated_heading_level_layout(
    line, m, *, col_right=None,
):
    """规则①变体：编号级先于居中大字（其余逐字节同现状）。"""
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
    if big or bold_short:
        for pat, lv in P._CHAPTER_LEVEL_RE:
            if pat.match(text):
                return lv
        # 规则①：编号级先于居中大字（编号编码层级，字号只证"是标题"）
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
    """跑 assemble_paragraphs，返回 {标题文本: 级}（现状布局 fn 已装好）。"""
    blocks = P.assemble_paragraphs(lines)
    return {b.text: b.level for b in blocks if b.kind == "heading"}


def main() -> int:
    src = glob.glob("DATA/raw_data/law_pdf/*OCR*.pdf")[0]
    doc = pymupdf.open(src)
    total = doc.page_count
    start, end = 14, min(total, 572)  # 0 基正文区 p15~p572
    k_total = end - start
    n_sample = 30
    idxs = [start + round(k * k_total / n_sample) for k in range(n_sample)]
    idxs = sorted(set(min(idx, end - 1) for idx in idxs))

    changed_pages = 0
    total_changed = 0
    detail: list[tuple[int, str, object, object]] = []
    skipped_c = []
    q_lines = fig_lines = 0
    orig_fn = P.heading_level_layout
    try:
        for i in idxs:
            page = doc.load_page(i)
            decision = clf.classify(
                build_page_profile(
                    page, i, background_img_ratio=s.pdf_tiered_background_img_ratio
                )
            )
            if decision.tier != "A":
                skipped_c.append((i + 1, decision.page_label, decision.reason))
                continue
            lines = remove_page_numbers(iter_lines(page))
            page_width = float(page.rect.width or 0)
            if decision.page_label == "two_col_text" or needs_two_column_defense(
                lines, page_width=page_width
            ):
                profile = build_page_profile(
                    page, i, background_img_ratio=s.pdf_tiered_background_img_ratio
                )
                split_x = profile.two_col_split_x or page_width / 2.0
                ordered = reflow_two_columns_v2(
                    lines, split_x=split_x, page_width=page_width
                )
            else:
                ordered = sort_single_column(lines)

            # 现状标题
            P.heading_level_layout = orig_fn
            cur = headings_of(ordered)
            # 规则①变体标题
            P.heading_level_layout = simulated_heading_level_layout
            new = headings_of(ordered)
            # 波及统计
            keys = set(cur) | set(new)
            page_changes = []
            for t in keys:
                if cur.get(t) != new.get(t):
                    page_changes.append((t, cur.get(t), new.get(t)))
                    total_changed += 1
            if page_changes:
                changed_pages += 1
                detail.append((i + 1, len(cur), len(new), page_changes))
            # 规则②③扫描（只读计数：？结尾编号行 / 图表题注形态行）
            for ln in ordered:
                txt = ln.text.strip()
                if re.match(r"^\d{1,3}\s*[.．、]\S", txt) and txt.endswith("？"):
                    q_lines += 1
                if re.match(r"^(图|表)\s*\d", txt):
                    fig_lines += 1
    finally:
        P.heading_level_layout = orig_fn

    print(f"书1 正文区 p{start+1}~p{end}，均匀抽 {len(idxs)} 页，MinerU(Tier C) 跳过 {len(skipped_c)} 页")
    for pg, label, reason in skipped_c:
        print(f"  skip p{pg}: {label} ({reason})")
    print(f"== 受影响页 {changed_pages}/{len(idxs)}，标题决定变化 {total_changed} 条 ==")
    for pg, n_cur, n_new, ch in detail:
        print(f"p{pg}: 标题 {n_cur}→{n_new}")
        for t, a, b in ch:
            old = a if a else "正文"
            nv = b if b else "正文"
            print(f"    「{t[:36]}」 {old}→{nv}")
    if not detail:
        print("（无任何标题决定变化）")
    print(f"规则②③形态计数（全样本）: ?结尾编号行 {q_lines}、图/表题注形态 {fig_lines}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
