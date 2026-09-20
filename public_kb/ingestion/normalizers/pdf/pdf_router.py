# 功能：PDF 三档路由顶层编排器。
"""
PDF 三档路由顶层编排器（三档路由计划 §3 总链路）。

主流程：
  PDF
   → profile(T1) → classify(T1) → 路由决定
   → Tier A: fast_text(T2A)  / Tier B: 预留（fallback 到 Tier C）/ Tier C: MinerU(T3)
   → T4 Markdown 装配 + manifest

行为对齐：
  - 总开关 pdf_tiered_routing_enabled 关闭 → 回退旧的 MinerUParser 全量链路（M1 行为）；
  - Tier A 用 ThreadPoolExecutor 并行（计划 §2.4 允许 / §10 每个 worker 独立开 PDF）；
  - Tier B 当前未实现：fallback 到 Tier C（本批有框表≈0，按计划 §11 处理）；
  - Tier C 失败默认 fail-fast；pdf_tiered_allow_partial=true 时跳过并写 warning。

对外接口 parse(pdf_path) → str（raw markdown），与 MinerUParser.parse 兼容，
下游 TextCleaner → SemanticChunker 不变。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# PyMuPDF 在调用处 lazy import（_profile_and_classify / _total_pages /
# _dispatch_tier_a worker 内），便于测试环境即使没装 pymupdf 也能 import 模块。
from core.config.settings import Settings

from ....services.mineru_api_parser import MinerUApiParser
from ....services.mineru_parser import MinerUParser
from .pdf_complex_range import (
    ComplexRange,
    aggregate_complex_ranges,
)
from .pdf_legal_page_classifier import (
    LegalPageClassifier,
    PageRouteDecision,
)
from .pdf_markdown_assembler import (
    ParsedPage,
    assemble_pages,
    build_manifest,
    clean_assembled_document,
    dedup_repeated_lines,
)
from .pdf_mineru_router import MinerURouter

logger = logging.getLogger(__name__)

_TIER_A = "A"
_TIER_B = "B"
_TIER_C = "C"
_TIER_SKIP = "S"  # 前置区页跳过（封面/CIP/序言/目录，用户决策 2026-09-05）

# 前置区识别（正文起点判定）：页文本含「第X编/章/节/条」标题行（行首 + 空白 +
# 短标题或裸标题，排除前言正文的句子引用如"第一章对……"）+ 成句标点（排除目录
# 纲要页）+ 非页码密集（排除带页码的目录）。三本书实测：全书 p14 / 白如银 p15 /
# 1200问 p7 为正文起点。
# 编/章/节/部分：标题即行尾（可选括号/短标题），排除前言正文的句子引用如
# "第一章对……"；条/款/项：行首即可（正文常在同行，如"第八条【招标人】正文"）。
_FRONT_HEADING_RE = re.compile(
    r"^第[一二三四五六七八九十百千\d]+[编部分章节]"
    r"(?:[（【][^）】]{0,20}[）】]|\s+\S.{0,20})?$"
    r"|^第[一二三四五六七八九十百千\d]+[条款项]"
)
_FRONT_PAGENO_RE = re.compile(r"\s\d{1,4}$")
_FRONT_SENT_MIN = 3     # 正文页成句标点（。！？）下限——目录纲要页几乎无成句
_FRONT_PAGENO_MAX = 8   # 页尾裸页码行上限——目录条目页超出


def _is_body_page(text: str) -> bool:
    """页文本是否为正文页（前置区跳过用）。False = 仍是前置区/目录/纲要。"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not any(
        _FRONT_HEADING_RE.match(ln) and len(ln) <= 40 for ln in lines
    ):
        return False
    sentences = sum(
        ln.count("。") + ln.count("！") + ln.count("？") for ln in lines
    )
    pageno_lines = sum(1 for ln in lines if _FRONT_PAGENO_RE.search(ln))
    return sentences >= _FRONT_SENT_MIN and pageno_lines < _FRONT_PAGENO_MAX


# ── 顶层入口 ───────────────────────────────────────────────


class PdfRouter:
    """PDF 三档路由顶层编排器。

    关闭开关时（默认）回退到 MinerUParser（M1 全量行为），保持现有数据契约
    `parser.parse(pdf_path) -> str`。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        miner_u_parser: Optional[MinerUApiParser] = None,
    ) -> None:
        """构造 PDF 三档路由编排器。

        Args:
            settings: 全局配置。
            miner_u_parser: 可选注入的 MinerUApiParser（默认根据 settings 构造）；
                主要用于测试时注入 fake。生产环境不传，走默认路径。
        """
        self._settings = settings
        self._enabled = settings.pdf_tiered_routing_enabled

        # 总开关关闭时只保留 fallback
        self._fallback_parser: Optional[MinerUParser] = None
        # 三档路径用到的组件
        self._classifier: Optional[LegalPageClassifier] = None
        self._miner_u_router: Optional[MinerURouter] = None

        if self._enabled:
            self._classifier = LegalPageClassifier(
                min_text_chars=settings.pdf_tiered_min_text_chars,
                image_area_ratio=settings.pdf_tiered_image_area_ratio,
                two_col_confidence=settings.pdf_tiered_two_col_confidence,
                img_min_text_chars=settings.pdf_tiered_img_min_text_chars,
                # table_min_lines 暂沿用分类器默认值；T2B 表格骨架启用时再传入
            )
            miner_u_api = miner_u_parser or MinerUApiParser(settings)
            self._miner_u_router = MinerURouter(settings, parser=miner_u_api)
        else:
            self._fallback_parser = MinerUParser(settings)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 对外主接口 ─────────────────────────────────────────

    def parse(self, pdf_path: Path | str) -> str:
        """解析 PDF，返回组装后的 raw markdown 字符串。

        关闭开关时走 MinerUParser 全量；开启时走三档路由。
        """
        if not self._enabled:
            assert self._fallback_parser is not None
            return self._fallback_parser.parse(pdf_path)

        assert self._classifier is not None
        assert self._miner_u_router is not None

        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

        # 1) 整本 PDF 画像 + 分类
        t0 = time.time()
        decisions = self._profile_and_classify(pdf_path)
        # 1.5) Tier B 暂未实现 → 升级到 Tier C，由 MinerU 统一兜底
        decisions = [
            replace(
                d,
                tier=_TIER_C,
                reason=f"tier_b_unimplemented_fallback_to_c: {d.reason}",
                parser="mineru",
            )
            if d.tier == _TIER_B
            else d
            for d in decisions
        ]
        # 1.6) 前置区页跳过（封面/CIP/序言/目录；pdf_skip_front_matter=true）
        decisions = self._mark_front_matter_skip(decisions, pdf_path)
        # 1.7) 页数上限（试点/分批重处理）：只解析前 N 页（0 = 全量）
        page_limit = int(
            getattr(self._settings, "pdf_parse_page_limit", 0) or 0
        )
        if page_limit > 0:
            decisions = [d for d in decisions if d.page_idx < page_limit]
        skipped_pages = [
            d.page_idx + 1 for d in decisions if d.tier == _TIER_SKIP
        ]
        # 2) Tier C 聚合 + 边界扩展（上限生效时边界扩展钳制在前 N 页内）
        total_pages = self._total_pages(pdf_path)
        if page_limit > 0:
            total_pages = min(total_pages, page_limit)
        ranges = aggregate_complex_ranges(
            decisions,
            total_pages=total_pages,
            expand_pages=self._settings.pdf_tiered_expand_boundary_pages,
            range_id_seed=pdf_path.stem,
        )
        # 3) 各档并行解析
        parsed_pages = self._dispatch_all(pdf_path, decisions, ranges)
        # 4) T4 装配（跨页续段合并——解析层段落化后唯一路径）
        assembled = assemble_pages(parsed_pages, join_cross_page=True)
        cleaned = clean_assembled_document(dedup_repeated_lines(assembled))
        elapsed = time.time() - t0
        # 5) manifest
        manifest = build_manifest(
            parsed_pages, parser_version=self._probe_parser_version()
        )
        manifest["elapsed_sec"] = round(elapsed, 3)
        manifest["routing"] = {
            "enabled": True,
            "ranges": [asdict(r) for r in ranges],
            "skipped_front_matter_pages": skipped_pages,
            "page_limit": page_limit if page_limit > 0 else None,
        }
        self._write_manifest(pdf_path, manifest)
        logger.info(
            "PDF 三档路由完成: %s tier_a=%d tier_b=%d tier_c_ranges=%d "
            "front_matter_skipped=%d elapsed=%.2fs",
            pdf_path.name,
            sum(1 for d in decisions if d.tier == _TIER_A),
            sum(1 for d in decisions if d.tier == _TIER_B),
            len(ranges),
            len(skipped_pages),
            elapsed,
        )
        return cleaned

    # ── 内部步骤 ───────────────────────────────────────────

    def _profile_and_classify(self, pdf_path: Path) -> List[PageRouteDecision]:
        """整本 PDF → 每页 PageRouteDecision（页面画像多线程并行，按页序汇合）。

        画像大头是 get_text("dict") 等 MuPDF C 调用（会释放 GIL），多线程可拿到
        真实并行度；每个 worker 独立按路径打开 PDF 处理一段连续页（Page 对象
        不可跨线程共享），最后按 page_idx 升序汇合保证确定性。
        """
        # 延迟导入避免循环 + 仅在开关开启时强依赖
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import pymupdf

        from .pdf_page_profile import build_page_profile

        assert self._classifier is not None
        bg_ratio = self._settings.pdf_tiered_background_img_ratio
        workers = max(
            1, min(self._settings.pdf_tiered_profile_max_workers,
                   (os.cpu_count() or 1))
        )

        doc = pymupdf.open(str(pdf_path))
        try:
            total = doc.page_count
        finally:
            doc.close()

        step = max(1, (total + workers - 1) // workers)

        def _profile_pages(page_idxs: Sequence[int]) -> List:
            d = pymupdf.open(str(pdf_path))
            try:
                out = []
                for idx in page_idxs:
                    out.append(build_page_profile(
                        d.load_page(idx), idx, background_img_ratio=bg_ratio,
                    ))
                return out
            finally:
                d.close()

        profiles: List[Any] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_profile_pages, list(range(start, min(start + step, total))))
                for start in range(0, total, step)
            ]
            for fut in as_completed(futures):
                profiles.extend(fut.result())
        profiles.sort(key=lambda p: p.page_idx)  # 保序确定性
        return [self._classifier.classify(p) for p in profiles]

    @staticmethod
    def _total_pages(pdf_path: Path) -> int:
        import pymupdf
        doc = pymupdf.open(str(pdf_path))
        try:
            return doc.page_count
        finally:
            doc.close()

    def _mark_front_matter_skip(
        self,
        decisions: Sequence[PageRouteDecision],
        pdf_path: Path,
    ) -> Sequence[PageRouteDecision]:
        """前置区页跳过（pdf_skip_front_matter=true 时）。

        正文起点 = 首个"正文页"（含 第X编/章/节/条 标题行 + 成句标点 + 非页码
        密集）。起点之前的页（封面/CIP/序言/目录）标 tier=SKIP，不参与 Tier A/C
        解析、不产出 markdown——书名由 doc_name 承载。识别不到正文起点 → 原样
        返回（由 clean_markdown 的 A3-5 剥离兜底）。
        """
        if not self._settings.pdf_skip_front_matter:
            return decisions
        import pymupdf

        doc = pymupdf.open(str(pdf_path))
        try:
            boundary = None
            for d in decisions:
                if _is_body_page(doc.load_page(d.page_idx).get_text("text") or ""):
                    boundary = d.page_idx
                    break
        finally:
            doc.close()
        if boundary in (None, 0):
            return decisions
        return [
            replace(
                d,
                tier=_TIER_SKIP,
                parser="skip",
                reason="front_matter_skip",
            )
            if d.page_idx < boundary
            else d
            for d in decisions
        ]

    def _dispatch_all(
        self,
        pdf_path: Path,
        decisions: Sequence[PageRouteDecision],
        ranges: Sequence[ComplexRange],
    ) -> List[ParsedPage]:
        """Tier A / Tier B / Tier C 分别派发，合并为 ParsedPage 列表。

        Tier B 已在上游升级为 Tier C（fallback），此处不再单独处理。
        """
        # Tier C：远程 MinerU 解析
        assert self._miner_u_router is not None
        try:
            tier_c_results = self._miner_u_router.parse_ranges(
                source_pdf=pdf_path,
                ranges=ranges,
                parser_version=self._probe_parser_version(),
            )
        except RuntimeError as exc:
            if self._settings.pdf_tiered_allow_partial:
                logger.warning(
                    "MinerU 不可达且 pdf_tiered_allow_partial=true，跳过 Tier C 页: %s",
                    exc,
                )
                tier_c_results = []
            else:
                raise

        # MinerU 外来 md 标题归一：并入前把扁平 # 拉回本地语义级
        # （编号问项→6、章/节→2/3、图/表/案例题注→正文、内容章→默认 2）。
        # 只在 MinerU 段生效，不覆盖本地版面锚定级（clean v2 已跳过 A3-1 全量重写）。
        from dataclasses import replace

        from .pdf_markdown_assembler import normalize_mineru_md_heading_levels

        tier_c_results = [
            replace(_p, markdown=normalize_mineru_md_heading_levels(_p.markdown))
            if _p.parser == "mineru" else _p
            for _p in tier_c_results
        ]

        # Tier A：并行快路径。被 Tier C 范围覆盖的页（含边界扩展页）统一使用
        # MinerU 产物，避免同一页同时出现快路径和 MinerU 内容（计划 §6 T3）。
        tier_c_page_set = {idx for r in ranges for idx in r.page_idxs}
        tier_a_pages = [
            d for d in decisions
            if d.tier == _TIER_A and d.page_idx not in tier_c_page_set
        ]
        tier_a_results = self._dispatch_tier_a(pdf_path, tier_a_pages)

        # 合并
        parsed: List[ParsedPage] = []
        for d, md, warnings in tier_a_results:
            parsed.append(
                ParsedPage(
                    page_idx=d.page_idx,
                    markdown=md,
                    parser=d.parser,
                    route=d,
                    warnings=tuple(warnings),
                )
            )
        # Tier C 范围产物：补填路由信息（MinerURouter 返回 route=None，
        # 由编排器在汇总时填入，保证 manifest 每页可溯源）。
        range_by_start = {r.start_idx: r for r in ranges}
        for tp in tier_c_results:
            r = range_by_start.get(tp.page_idx)
            if r is not None and tp.route is None:
                tp = replace(
                    tp,
                    route=PageRouteDecision(
                        page_idx=tp.page_idx,
                        page_label="tier_c_range",
                        tier=_TIER_C,
                        reason="tier_c_range pages="
                              + ",".join(str(i) for i in r.page_idxs),
                        confidence=1.0,
                        parser="mineru",
                        features={},
                    ),
                )
            parsed.append(tp)
        # 按 page_idx 升序
        parsed.sort(key=lambda p: p.page_idx)
        return parsed

    def _dispatch_tier_a(
        self,
        pdf_path: Path,
        decisions: Sequence[PageRouteDecision],
    ) -> List[Tuple[PageRouteDecision, str, List[str]]]:
        """Tier A：并行快路径（每 worker 只打开一次 PDF，处理一批页）。"""
        if not decisions:
            return []

        max_workers = max(1, self._settings.pdf_tiered_fast_max_workers)
        # 每 worker 处理连续一批页，文档只打开一次，避免逐页重复 open
        chunk_size = max(1, (len(decisions) + max_workers - 1) // max_workers)
        chunks = [
            decisions[i:i + chunk_size]
            for i in range(0, len(decisions), chunk_size)
        ]

        # A1-1：文档级页眉候选集（跨页重复）——先预扫全部 Tier A 页的行做判断，
        # 再把候选集传给各 worker，同一文档统一口径（避免逐 worker 各自为政）。
        # 预扫失败则退化为"不去页眉"（header_candidates 为空时 remove_header_lines
        # 不删任何行），不影响主流程。
        from .pdf_fast_text import detect_header_candidates, iter_lines
        header_candidates: Set[str] = set()
        try:
            import pymupdf
            doc = pymupdf.open(str(pdf_path))
            try:
                page_lines: List[Any] = []
                page_heights: List[float] = []
                for d in decisions:
                    page = doc.load_page(d.page_idx)
                    page_lines.append(iter_lines(page))
                    page_heights.append(float(page.rect.height or 0))
                header_candidates = detect_header_candidates(
                    page_lines,
                    page_heights=page_heights,
                    min_pages=self._settings.pdf_header_repeat_min,
                    min_repeat=self._settings.pdf_header_repeat_plain,
                    max_len=self._settings.pdf_header_max_len,
                )
            finally:
                doc.close()
        except Exception:  # noqa: BLE001
            header_candidates = set()

        def worker(items: Sequence[PageRouteDecision]):
            import pymupdf

            from .pdf_fast_text import (
                remove_header_lines,
                remove_page_numbers,
                sort_single_column,
            )
            from .pdf_page_profile import build_page_profile
            from .pdf_paragraph import paragraph_markdown
            from .pdf_two_column_reflow import (
                needs_two_column_defense,
                reflow_two_columns_v2,
            )

            doc = pymupdf.open(str(pdf_path))  # 每线程独立 Document
            try:
                results: List[Tuple[PageRouteDecision, str, List[str]]] = []
                for d in items:
                    warnings: List[str] = []
                    try:
                        page = doc.load_page(d.page_idx)
                        # 段落化 + 版面锚定标题 + 串栏防御（灰度开关 pdf_layout_v2
                        # 删除后唯一路径）。单栏页 paragraph_markdown 直出；two_col
                        # 标记页或防御信号页走 reflow_two_columns_v2（通栏行 y 归位）
                        # 后段落化。
                        lines = remove_page_numbers(iter_lines(page))
                        lines = remove_header_lines(
                            lines, header_candidates,
                            page_height=float(page.rect.height or 0),
                        )
                        page_width = float(page.rect.width or 0)
                        if d.page_label == "two_col_text" or (
                            needs_two_column_defense(
                                lines, page_width=page_width
                            )
                        ):
                            profile = build_page_profile(
                                page,
                                d.page_idx,
                                background_img_ratio=(
                                    self._settings.pdf_tiered_background_img_ratio
                                ),
                            )
                            split_x = (
                                profile.two_col_split_x or page_width / 2.0
                            )
                            ordered = reflow_two_columns_v2(
                                lines,
                                split_x=split_x,
                                page_width=page_width,
                            )
                        else:
                            ordered = sort_single_column(lines)
                        md = paragraph_markdown(ordered)
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"fast_text_failed: {exc}")
                        md = ""
                    results.append((d, md, warnings))
                return results
            finally:
                doc.close()

        out: List[Tuple[PageRouteDecision, str, List[str]]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(worker, c) for c in chunks]
            for fut in as_completed(futures):
                out.extend(fut.result())
        # 按页序汇合（确定性）
        out.sort(key=lambda item: item[0].page_idx)
        return out

    def _probe_parser_version(self) -> str:
        """探测 MinerU 服务端的 parser_version；失败时回退「上次已知版本」（P5）。

        回退很关键：缓存键含版本号，服务不可达时若退化为空串，本地缓存会
        全部 miss → 转 HTTP → 无 allow_partial 时整本解析失败。回退后离线
        重跑可命中本地缓存。假 parser（测试注入）无该方法时退回 health()。
        """
        assert self._miner_u_router is not None
        parser = self._miner_u_router.parser
        resolver = getattr(parser, "resolve_parser_version", None)
        try:
            if callable(resolver):
                return str(resolver() or "")
            return str(parser.health().get("parser_version") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _write_manifest(self, pdf_path: Path, manifest: Dict[str, Any]) -> None:
        out_dir = Path(self._settings.pdf_tiered_manifest_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{pdf_path.stem}.manifest.json"
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, out_path)
        logger.info("manifest 已落盘: %s", out_path)
