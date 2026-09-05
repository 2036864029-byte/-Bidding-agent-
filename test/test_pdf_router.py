"""L4 — pdf_router 编排器单元测试（覆盖开关分支 + Tier B 自动升级）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config.settings import Settings
from public_kb.ingestion.normalizers.pdf.pdf_router import PdfRouter
from public_kb.services.mineru_parser import MinerUParser


# ── 开关关闭 → 回退 MinerUParser（M1 行为）───────────────────


def test_router_disabled_uses_mineru_parser_fallback():
    s = Settings()
    # 显式关闭三档开关（不依赖 .env 的 PDF_TIERED_ROUTING_ENABLED）
    s.pdf_tiered_routing_enabled = False
    r = PdfRouter(s)
    assert r.enabled is False
    assert isinstance(r._fallback_parser, MinerUParser)


def test_router_enabled_creates_components():
    s = Settings()
    s.pdf_tiered_routing_enabled = True
    r = PdfRouter(s)
    assert r.enabled is True
    assert r._classifier is not None
    assert r._miner_u_router is not None
    assert r._fallback_parser is None


def test_router_threshold_propagates_to_classifier():
    s = Settings()
    s.pdf_tiered_routing_enabled = True
    s.pdf_tiered_min_text_chars = 123
    s.pdf_tiered_image_area_ratio = 0.42
    s.pdf_tiered_two_col_confidence = 0.77
    r = PdfRouter(s)
    assert r._classifier is not None
    assert r._classifier._min_text_chars == 123
    assert abs(r._classifier._image_area_ratio - 0.42) < 1e-6
    assert abs(r._classifier._two_col_confidence - 0.77) < 1e-6


# ── Tier B 自动升级到 Tier C（纯逻辑）────────────────────────


def test_tier_b_upgrade_logic():
    """从 dataclasses.replace 推导：Tier B 决策 → Tier C，reason 加前缀。"""
    from dataclasses import replace

    from public_kb.ingestion.normalizers.pdf.pdf_legal_page_classifier import (
        PageRouteDecision,
    )

    b = PageRouteDecision(
        page_idx=5,
        page_label="table_regular",
        tier="B",
        reason="检测到规整有框表格",
        confidence=0.85,
        parser="table_extractor",
        features={},
    )
    upgraded = replace(
        b, tier="C",
        reason=f"tier_b_unimplemented_fallback_to_c: {b.reason}",
        parser="mineru",
    )
    assert upgraded.tier == "C"
    assert upgraded.parser == "mineru"
    assert "tier_b_unimplemented_fallback_to_c" in upgraded.reason
    assert upgraded.page_idx == 5  # 其他字段保留


# ── 下列端到端测试需要 pymupdf + 真实 PDF，本机未装时跳过 ──


@pytest.mark.skipif(
    not pytest.importorskip("pymupdf", reason="pymupdf not installed"),
    reason="pymupdf required",
)
def test_router_end_to_end_with_mocked_mineru(tmp_path, monkeypatch):
    """构造一个最小 PDF，跑三档路由全流程，mock MinerUApiParser。

    本测试需要 pymupdf + langchain_core（依赖从 transforms/__init__ 引入）。
    """
    import pymupdf

    from public_kb.ingestion.normalizers.pdf.pdf_mineru_router import MinerURouter

    # 构造 5 页单栏 PDF
    src = tmp_path / "src.pdf"
    doc = pymupdf.open()
    try:
        for _ in range(5):
            doc.new_page(width=400, height=600)
        doc.save(str(src))
    finally:
        doc.close()

    s = Settings()
    s.pdf_tiered_routing_enabled = True
    s.mineru_output_dir = str(tmp_path)
    s.pdf_tiered_manifest_dir = str(tmp_path / "manifest")
    s.pdf_tiered_allow_partial = False

    # 用 fake 替换 MinerURouter 的远端解析
    from public_kb.services.mineru_api_parser import MinerUApiParser

    class _FakeApi:
        """替身 MinerUApiParser：返回固定 Markdown，记录调用。"""

        def __init__(self) -> None:
            self.calls: list = []

        def health(self) -> dict:
            return {"parser_version": "1.3.12"}

        def parse(self, pdf_path, *, page_range=None):
            self.calls.append(Path(pdf_path))
            return f"<!-- mineru fake for {Path(pdf_path).name} -->\n"

    fake = _FakeApi()
    r = PdfRouter(s, miner_u_parser=fake)
    md = r.parse(src)
    assert isinstance(md, str)
    # 没有 Tier C 页时，markdown 应只有页标记 + 快路径产物
    assert "<!-- page:" in md

    # manifest 已落盘
    manifests = list((tmp_path / "manifest").glob("*.manifest.json"))
    assert manifests, "manifest 未生成"


@pytest.mark.skipif(
    not pytest.importorskip("pymupdf", reason="pymupdf not installed"),
    reason="pymupdf required",
)
def test_tier_a_excludes_range_covered_pages_and_fills_route(tmp_path):
    """§6 T3 回归：被 Tier C 范围（含边界扩展）覆盖的页不再重复走快路径；
    范围产物的 route 由编排器补填，保证 manifest 每页可溯源。"""
    import pymupdf

    from public_kb.ingestion.normalizers.pdf.pdf_complex_range import (
        aggregate_complex_ranges,
    )
    from public_kb.ingestion.normalizers.pdf.pdf_legal_page_classifier import (
        PageRouteDecision,
    )
    from public_kb.ingestion.normalizers.pdf.pdf_markdown_assembler import ParsedPage

    # 最小 3 页 PDF（页 0/2 是 A 类、页 1 是 C 类）
    src = tmp_path / "src.pdf"
    doc = pymupdf.open()
    try:
        for _ in range(3):
            doc.new_page(width=400, height=600)
        doc.save(str(src))
    finally:
        doc.close()

    s = Settings()
    s.pdf_tiered_routing_enabled = True
    s.mineru_output_dir = str(tmp_path)
    s.pdf_tiered_manifest_dir = str(tmp_path / "manifest")
    s.pdf_tiered_expand_boundary_pages = 1

    from public_kb.services.mineru_api_parser import MinerUApiParser

    class _FakeApi(MinerUApiParser):
        def health(self):  # noqa: D102
            return {"parser_version": "1.3.12"}

        def parse(self, pdf_path, *, page_range=None):  # noqa: D102
            return "<!-- mineru fake -->\n"

    r = PdfRouter(s, miner_u_parser=_FakeApi(s))

    decisions = [
        PageRouteDecision(0, "text", "A", "text page", 1.0, "fast_text", {}),
        PageRouteDecision(1, "visual_or_scan", "C", "scan", 0.9, "mineru", {}),
        PageRouteDecision(2, "text", "A", "text page", 1.0, "fast_text", {}),
    ]
    ranges = aggregate_complex_ranges(
        decisions, total_pages=3, expand_pages=1, range_id_seed="t")
    assert len(ranges) == 1
    assert list(ranges[0].page_idxs) == [0, 1, 2]  # 边界扩展把 0、2 也纳入

    dispatched: list = []

    def fake_dispatch_tier_a(pdf, ds):
        dispatched.append([d.page_idx for d in ds])
        return []

    r._dispatch_tier_a = fake_dispatch_tier_a  # type: ignore[method-assign]
    r._miner_u_router.parse_ranges = lambda **kw: [  # type: ignore[method-assign]
        ParsedPage(page_idx=0, markdown="<!-- mineru fake -->\n",
                   parser="mineru", route=None)
    ]

    parsed = r._dispatch_all(src, decisions, ranges)
    # 页 0/2 被范围覆盖，快路径不再收到它们 → 不重复解析
    assert dispatched == [[]], "边界扩展页不得再走快路径，否则同一页内容重复"
    # 范围产物 route 被补填，manifest 可溯源
    assert parsed and parsed[0].route is not None
    assert parsed[0].route.tier == "C"
    assert "tier_c_range" in parsed[0].route.page_label
    assert "0,1,2" in parsed[0].route.reason


# ── 前置区跳过（pdf_skip_front_matter，用户决策 2026-09-05）──────


def test_is_body_page_heuristics():
    """正文页判定：章标题行 + 成句标点 + 非目录纲要/前言引用。"""
    from public_kb.ingestion.normalizers.pdf.pdf_router import _is_body_page

    # 正文页：第X章 标题 + 成句内容（≥3 句）
    assert _is_body_page(
        "第一章 总则\n第一条 为了规范招标投标活动，根据本法制定。\n"
        "第二条 本法适用于境内招标投标活动。\n第三条 国家鼓励电子招标投标。\n"
    )
    # 条级正文页（第X条 + 成句）
    assert _is_body_page(
        "第八条【招标人】招标人是依照本法规定提出招标项目、进行招标的法人。\n"
        "招标人应当有进行招标项目的相应资金。\n资金来源已经落实。\n"
    )
    # 前置区：无 第X 标题（封面/CIP/版权）
    assert not _is_body_page("中华人民共和国招标投标法律法规全书\n法律出版社\n2023年\n")
    # 前言正文引用章节（无空白 + 长句延续）
    assert not _is_body_page("第一章对政府采购进行系统的阐述，重点是政府采购方式与程序。\n")
    # 目录纲要页（大量短条目、无成句标点）
    toc = "第一章 招标投标基础知识\n第一节 招标投标概述\n第二节 招标投标的基本原则\n" \
          "第三节 招标投标的主体和客体\n一、招标投标的起源与发展\n二、对概念的理解\n" \
          "三、招标投标的主要特点\n四、招标投标的作用\n"
    assert not _is_body_page(toc)
    # 带页码的目录页
    assert not _is_body_page("第一章 综合 …… 3\n第二章 招标 …… 15\n第一章 总则 …… 28\n")


@pytest.mark.skipif(
    not pytest.importorskip("pymupdf", reason="pymupdf not installed"),
    reason="pymupdf required",
)
def test_mark_front_matter_skip_marks_front_pages(tmp_path):
    """真实 PDF：封面/序言/目录页 → tier=S；正文页保持原 tier。"""
    import pymupdf

    from public_kb.ingestion.normalizers.pdf.pdf_legal_page_classifier import (
        PageRouteDecision,
    )

    src = tmp_path / "fm.pdf"
    doc = pymupdf.open()
    try:
        pages_text = [
            "中华人民共和国招标投标法律法规全书\n法律出版社\n2023年\n",      # 0 封面
            "序言\n本书旨在解读招标投标法律实务。\n",                          # 1 序言
            "第一章 招标投标基础知识\n第一节 概述\n第二节 原则\n第三节 主体\n",  # 2 目录纲要
            "第一章 总则\n第一条 为了规范招标投标活动，根据本法制定。\n"       # 3 正文
            "第二条 本法适用于境内招标投标活动。\n第三条 国家鼓励电子招标投标。\n",
        ]
        for text in pages_text:
            page = doc.new_page(width=400, height=600)
            page.insert_text((40, 60), text, fontname="china-s")
        doc.save(str(src))
    finally:
        doc.close()

    s = Settings()
    s.pdf_tiered_routing_enabled = True
    s.pdf_skip_front_matter = True
    r = PdfRouter(s)

    decisions = [
        PageRouteDecision(i, "text", "A", "text", 1.0, "fast_text", {})
        for i in range(4)
    ]
    out = r._mark_front_matter_skip(decisions, src)
    assert [d.tier for d in out] == ["S", "S", "S", "A"]
    assert all(d.parser == "skip" for d in out[:3])
    assert out[3].page_idx == 3  # 正文页不动

    # 开关关闭 → 不跳过
    s.pdf_skip_front_matter = False
    r2 = PdfRouter(s)
    assert r2._mark_front_matter_skip(decisions, src) == decisions


@pytest.mark.skipif(
    not pytest.importorskip("pymupdf", reason="pymupdf not installed"),
    reason="pymupdf required",
)
def test_mark_front_matter_skip_no_boundary_returns_unchanged(tmp_path):
    """找不到正文起点（如纯扫描件无文本层）→ 原样返回（A3-5 兜底）。"""
    import pymupdf

    from public_kb.ingestion.normalizers.pdf.pdf_legal_page_classifier import (
        PageRouteDecision,
    )

    src = tmp_path / "noscan.pdf"
    doc = pymupdf.open()
    try:
        # 空文本层（模拟纯扫描件）
        doc.new_page(width=400, height=600)
        doc.save(str(src))
    finally:
        doc.close()

    s = Settings()
    s.pdf_tiered_routing_enabled = True
    s.pdf_skip_front_matter = True
    r = PdfRouter(s)
    decisions = [PageRouteDecision(0, "visual_or_scan", "C", "scan", 0.9, "mineru", {})]
    assert r._mark_front_matter_skip(decisions, src) == decisions
