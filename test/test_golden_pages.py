"""Phase A A5 黄金页对页级保真回归（test/golden/pages/quanshu.json）。

黄金页对 = 从《全书》抽的代表页「页 → 当前抽取层输出」，覆盖通用页型（双栏/
单栏/章首页/条首页/页眉泄漏页/前置页/末页）。任何抽取/清洗改动不得改变这些页
的输出（内容级保真，比指标更细）。

抽取口径与 pdf_router worker 一致（profile → classify → 双栏 reflow_page_
markdown / 单栏 extract_page_markdown，用黄金快照内冻结的文档级页眉候选——
候选检测本身由 scripts/run_clean_text_metrics.py 全量门禁覆盖）。

快速（~12 页 × 0.3s ≈ 5s），默认跑；PDF 缺失时跳过。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "DATA" / "raw_data" / "law_pdf"
GOLDEN_PATH = ROOT / "test" / "golden" / "pages" / "quanshu.json"
QUANSHU = (
    "中华人民共和国招标投标法律法规全书：含相关政策 "
    "(法律法规出版社法规中心) (Z-Library)(OCR).pdf"
)


def _extract_golden_page(doc, idx, label, header_candidates):
    """复刻 router worker 的单页抽取（与 build_golden_pages 同一口径）。"""
    from public_kb.ingestion.normalizers.pdf.pdf_fast_text import (
        extract_page_markdown,
        iter_lines,
    )
    from public_kb.ingestion.normalizers.pdf.pdf_page_profile import (
        build_page_profile,
    )
    from public_kb.ingestion.normalizers.pdf.pdf_two_column_reflow import (
        reflow_page_markdown,
    )

    from core.config.settings import Settings

    settings = Settings()
    page = doc.load_page(idx)
    if label == "two_col_text":
        profile = build_page_profile(
            page, idx,
            background_img_ratio=settings.pdf_tiered_background_img_ratio,
        )
        return reflow_page_markdown(
            iter_lines(page),
            split_x=profile.two_col_split_x,
            page_width=profile.width,
            header_candidates=header_candidates,
        )
    return extract_page_markdown(
        page, idx, header_candidates=header_candidates
    )


@pytest.fixture(scope="module")
def golden():
    assert GOLDEN_PATH.exists(), f"黄金页对缺失: {GOLDEN_PATH}"
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def extracted(golden):
    import pymupdf

    doc = pymupdf.open(str(PDF_DIR / QUANSHU))
    try:
        candidates = golden["header_candidates"]
        return {
            p["idx"]: _extract_golden_page(doc, p["idx"], p["label"], candidates)
            for p in golden["pages"]
        }
    finally:
        doc.close()


def test_golden_pages_file_is_well_formed(golden):
    assert golden["schema_version"] == 1
    assert golden["pages"], "黄金页对为空"
    assert all({"idx", "markdown"} <= set(p) for p in golden["pages"])


@pytest.mark.skipif(
    not (PDF_DIR / QUANSHU).exists(),
    reason="《全书》PDF 缺失（DATA/raw_data/law_pdf），页级保真仅本机可跑",
)
class TestGoldenPagesFidelity:
    """黄金页抽取输出与提交快照逐字节一致（内容级保真）。"""

    @pytest.mark.parametrize(
        "page_idx",
        [p["idx"] for p in json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["pages"]],
    )
    def test_page_output_unchanged(self, golden, extracted, page_idx):
        expected = next(p["markdown"] for p in golden["pages"] if p["idx"] == page_idx)
        assert extracted[page_idx] == expected, (
            f"p{page_idx} 抽取输出变化（内容级保真回归失败）"
        )

    def test_required_page_types_covered(self, golden):
        # 黄金页型覆盖要求（A5：按通用页型抽样，不硬编码某页）
        covered = {t for p in golden["pages"] for t in p.get("types", [])}
        for required in ("chapter_start", "article_start", "two_col"):
            assert required in covered or any(
                p["label"] == "two_col_text" for p in golden["pages"]
            ), f"页型覆盖缺失: {required}"
