# 功能：Markdown 装配层（三档路由 T4）— 页序缝合、块排序、表格标签、标题规范化、manifest。
"""
Markdown assembler for tiered PDF routing (T4).

职责（对齐三档路由计划 §6 T4 与附录 B）：
  1. 页序缝合：按 page_idx 升序 join 各页产物，穿插页（文本/MinerU 交替）还原；
  2. 页内块排序：PageBlock 按 order_key（块顶 y）排序，表格块原子插入正确位置；
  3. 表格占位标签：<!-- table: page=N, id=..., bbox=... --> 仅排查/溯源，不进 chunk metadata；
  4. 标题级别规范化：编→# 章→## 节→### 条→#### 款→##### 项→######；
  5. 去重页眉页脚 + 目录点线行；
  6. manifest：每页 route/parser/置信度/警告，供质量回查。

契约（先定后实现，见附录 B.1）：
  - 页级主键 page_idx（0 基）；表格占位标签不进下游 chunk metadata；
  - 本地表格与 MinerU 表格统一为 `|` Markdown 原子块（下游 PdfStructure 已有保护）。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .pdf_legal_page_classifier import PageRouteDecision
from .pdf_structure import strip_toc_noise

# 法规标题类型 → Markdown 级别（T4 标题规范化）
_LEGAL_HEADING_LEVELS: Tuple[Tuple[re.Pattern, int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百千\d]+编"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+章"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+节"), 3),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+条"), 4),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+款"), 5),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+项"), 6),
)

# 页眉页脚去重豁免：法律标题行（markdown 形式 `## 第一章` 或裸形式）即使高频也不删。
# RC2 修复：旧式只认裸 `第X章` 开头，把 `## 第一章 总 则` 当页眉计数 ≥5 全删（书1 数据丢失）。
_LEGAL_HEADING_START_RE = re.compile(r"^(?:#{1,6}\s+)?第[一二三四五六七八九十百千\d]+[编章节条款项]")

# Markdown 标题行
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# 表格占位标签格式
_TABLE_TAG_RE = re.compile(
    r"^<!--\s*table:\s*page=(\d+),\s*id=([A-Za-z0-9_\-]+),\s*bbox=\(([^)]*)\)\s*-->$"
)

# ── MinerU 外来 markdown 标题归一（版面还原 P6，2026-09-06）────────
# MinerU 对标题一律打扁平 `#`（章/节/编号问项/题注同级，实测书3 1180+ 个 #、
# 章节与问项零层级）。本地页标题是版面锚定级（准确），clean v2 又跳过 A3-1
# 模式级重写（保护本地版面级）→ MinerU 外来 # 原样混入，同一语义两级并存。
# 本函数在 MinerU md 并入前调用（只在 MinerU 段生效、不碰本地段），按文本
# 模式把它的标题拉回与本地一致的口径。与 pdf_paragraph._LIST_SUBLEVEL_RE /
# _CHAPTER_LEVEL_RE / _CONTENT_LEVELS(A3-1) 同族同语义。

# 模式标题固定级（MinerU 扁平 # 的重新定级；含列表/问项细分）
_MRU_LEVELS: Tuple[Tuple[re.Pattern, int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百千\d]+编"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+部分"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+章"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+节"), 3),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+条"), 4),
    (re.compile(r"^[0-9一二三四五六七八九十百]+、"), 4),    # 一、/1、 → 4
    (re.compile(r"^（[0-9一二三四五六七八九十百]+）"), 5),   # （一） → 5
    (re.compile(r"^[0-9]{1,4}[.．]"), 6),                    # N. → 6（编号问项）
)
# 题注/版面件形态：非章节标题，剥 # 降回正文
_MRU_CAPTION_RE = re.compile(
    r"^(图|表|附表|附图|案例|例题|习题|模板)\s*[0-9０-９]"
    r"|^(图|表|附表|附图)(\s|$)"
    r"|^(参考文献|版权信息|案例索引|前\d+版)"
)
# 默认级（无模式内容章，如书3 手册体的"政府采购概述"——本地靠版面居中大字
# 定 H2；MinerU 无版面几何，按文档语义默认章级以保章节骨架）
_MRU_DEFAULT_LEVEL = 2


def normalize_mineru_md_heading_levels(markdown: str) -> str:
    """把 MinerU 外来 markdown 的标题级重映射到本地语义（只在 MinerU 段生效）。

    只改写标题行（``#{1,6} text``）；非标题内容与内部表格/引用原样保留。
    MinerU 标题的层级无版面依据，全部按文本模式重定：
      结构词 编/章/节/条 → 1/2/3/4；列表/编号 一、/（一）/N. → 4/5/6；
      图/表/案例/例题/封面题注 → 剥标题降正文；其余（无模式内容章）→ 默认 2。
    幂等：已是目标级的标题行不再动。
    """
    out: List[str] = []
    for line in markdown.split("\n"):
        m = _MD_HEADING_RE.match(line.strip())
        if not m:
            out.append(line)
            continue
        text = m.group(2).strip()
        if _MRU_CAPTION_RE.match(text):
            out.append(text)          # 题注剥 # → 正文（图/表/案例/封面）
            continue
        level = None
        for pat, lv in _MRU_LEVELS:
            if pat.match(text):
                level = lv
                break
        if level is None:
            level = _MRU_DEFAULT_LEVEL
        out.append("#" * level + " " + text)
    return "\n".join(out)


@dataclass(frozen=True)
class PageBlock:
    """页内原子块（图文表混排的最小装配单元）。

    order_key: 排序键（块顶 y 坐标），装配时按升序输出。
    kind: text / table / heading。
    content: 文本或 `|` Markdown 表格（表格为原子块，不被拆分）。
    bbox: (x0, y0, x1, y1) 溯源用，表格块必须有。
    """

    order_key: float
    kind: str
    content: str
    bbox: Tuple[float, ...] = ()


@dataclass(frozen=True)
class ParsedPage:
    """一页的解析产物（三档路由计划 §5.2 扩展）。

    page_idx: 0 基页号（页级主键，装配层据此还原顺序）。
    markdown: 该页的 Markdown（blocks 为空时使用）。
    parser: 实际解析器（fast_text / table_extractor / mineru）。
    route: 路由决定（含 tier/label/reason/confidence）。
    warnings: 非致命告警。
    blocks: 结构化块（可选；提供时优先于 markdown 做页内块排序）。
    """

    page_idx: int
    markdown: str = ""
    parser: str = ""
    route: Optional[PageRouteDecision] = None
    warnings: Tuple[str, ...] = ()
    blocks: Tuple[PageBlock, ...] = ()


# ── 表格占位标签 ─────────────────────────────────────────

def table_placeholder(page_idx: int, table_id: str, bbox: Sequence[float]) -> str:
    """生成表格占位标签（仅排查/溯源，不进 chunk metadata）。"""
    box = ",".join(str(round(float(v), 1)) for v in bbox)
    return f"<!-- table: page={page_idx + 1}, id={table_id}, bbox=({box}) -->"


def parse_table_placeholder(line: str) -> Optional[Dict]:
    """解析表格占位标签；非标签返回 None。"""
    m = _TABLE_TAG_RE.match(line.strip())
    if not m:
        return None
    page = int(m.group(1))
    tid = m.group(2)
    bbox = tuple(float(v.strip()) for v in m.group(3).split(",") if v.strip())
    return {"page": page, "id": tid, "bbox": bbox}


# ── 页内块排序 ───────────────────────────────────────────

def assemble_page_blocks(page_idx: int, blocks: Iterable[PageBlock]) -> str:
    """按 order_key 升序装配一页；表格块前插入占位标签。"""
    ordered = sorted(blocks, key=lambda b: b.order_key)
    parts: List[str] = []
    table_i = 0
    for block in ordered:
        if block.kind == "table":
            tid = f"t{page_idx}_{table_i}"
            parts.append(table_placeholder(page_idx, tid, block.bbox))
            table_i += 1
        parts.append(block.content)
    return "\n\n".join(parts)


# ── 页序缝合 ─────────────────────────────────────────────

def assemble_pages(
    pages: Iterable[ParsedPage],
    *,
    page_marker: bool = True,
    join_cross_page: bool = False,
) -> str:
    """按 page_idx 升序缝合全文档；穿插页（文本/MinerU 交替）按原页序还原。

    join_cross_page=True（P3 跨页续段，修 Q3 跨页断段）：前页最后一个正文行
    无句末收尾、且次页首个正文块非标题/非结构起始 → 两行合并（默认关闭，
    灰度契约）。
    """
    ordered = sorted(pages, key=lambda p: p.page_idx)
    mds: List[str] = [
        assemble_page_blocks(p.page_idx, p.blocks) if p.blocks else p.markdown
        for p in ordered
    ]
    if join_cross_page:
        for i in range(1, len(mds)):
            mds[i - 1], mds[i] = _join_page_boundary(mds[i - 1], mds[i])
    parts: List[str] = []
    for page, md in zip(ordered, mds):
        if page_marker:
            parts.append(f"<!-- page: {page.page_idx + 1} -->")
        parts.append(md)
    return "\n\n".join(parts)


# 跨页续段：行级判定（P3）
_TERMINAL_TAIL = "。！？…；”』」」）)】]"
_NON_BODY_LINE_RE = re.compile(r"^(#{1,6}\s|<!--|\||!\[|>|[-*]\s)")
_STRUCT_LINE_RE = re.compile(
    r"^(（\s*[0-9一二三四五六七八九十百]+\s*）|\([0-9一二三四五六七八九十百]+\)"
    r"|[0-9一二三四五六七八九十百]+[、.)．]|[-•·]|答\s*[:：]"
    r"|第[一二三四五六七八九十百千\d]+[编章节部分条款项])"
)


_PAGE_MARK_RE = re.compile(r"^<!--\s*page:\s*\d+\s*-->$")

# 页脚注行（①~⑳ 编号编者注，如"① 条文主旨为编者所加，下同。"）：位于正文
# 之后且以句末收尾，若当末正文行会阻断真实末行与次页首行的续段合并
# （书1 实测每页页底重复注释导致跨页条文大量断裂）。
_FOOTNOTE_LINE_RE = re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|^注\s*[:：]")


def _join_page_boundary(a: str, b: str) -> Tuple[str, str]:
    """相邻页边界续段合并；不可合并时原样返回。

    页标记注释行（<!-- page: N -->）与页脚注行（①… 编号注释）是页缘
    产物，扫描末正文行时跳过。
    """
    a_lines = a.rstrip("\n").split("\n")
    ai = len(a_lines) - 1
    while ai >= 0 and (
        not a_lines[ai].strip()
        or _PAGE_MARK_RE.match(a_lines[ai].strip())
        or _FOOTNOTE_LINE_RE.match(a_lines[ai].strip())
    ):
        ai -= 1
    if ai < 0:
        return a, b
    b_lines = b.split("\n")
    bi = 0
    while bi < len(b_lines) and (
        not b_lines[bi].strip() or _PAGE_MARK_RE.match(b_lines[bi].strip())
    ):
        bi += 1
    if bi >= len(b_lines):
        return a, b
    left = a_lines[ai].strip()
    right = b_lines[bi].strip()
    if _NON_BODY_LINE_RE.match(left) or _NON_BODY_LINE_RE.match(right):
        return a, b
    if len(left) < 8 or left[-1] in _TERMINAL_TAIL:
        return a, b
    if len(right) < 2 or _STRUCT_LINE_RE.match(right):
        return a, b
    a_lines[ai] = a_lines[ai].rstrip() + right
    del b_lines[bi]  # 只移除被并入的行；页标记等前导行保留
    return "\n".join(a_lines), "\n".join(b_lines)


# ── 标题级别规范化 ───────────────────────────────────────

def normalize_heading_levels(markdown: str) -> str:
    """把法规标题（编/章/节/条/款/项）统一映射到规范级别；非法规标题保留原样。"""
    out: List[str] = []
    for line in markdown.split("\n"):
        m = _MD_HEADING_RE.match(line.strip())
        if m:
            text = m.group(2).strip()
            for pattern, level in _LEGAL_HEADING_LEVELS:
                if pattern.match(text):
                    out.append("#" * level + " " + text)
                    break
            else:
                out.append(line)  # 非法规标题不改
        else:
            out.append(line)
    return "\n".join(out)


# ── 页眉页脚 / 目录去重 ──────────────────────────────────

def dedup_repeated_lines(
    markdown: str,
    *,
    min_count: int = 5,
    max_len: int = 80,
) -> str:
    """按行频次去重页眉/页脚；法律标题行（第X章/条等）即使高频也保留。"""
    lines = markdown.split("\n")
    stripped = [line.strip() for line in lines]
    counter = Counter(
        s for s in stripped
        if s and len(s) < max_len and not _LEGAL_HEADING_START_RE.match(s)
    )
    repeated = {s for s, count in counter.items() if count >= min_count}
    if not repeated:
        return markdown
    return "\n".join(line for line in lines if line.strip() not in repeated)


def clean_assembled_document(markdown: str) -> str:
    """装配后清理：去目录点线行 + 去重复页眉页脚 + 标题级别规范化。"""
    markdown = strip_toc_noise(markdown)
    markdown = dedup_repeated_lines(markdown)
    return normalize_heading_levels(markdown)


# ── manifest ─────────────────────────────────────────────

@dataclass(frozen=True)
class ManifestRecord:
    """单页路由/解析记录。"""

    page: int
    tier: str
    label: str
    parser: str
    planned_parser: str
    reason: str
    confidence: float
    warnings: Tuple[str, ...] = ()


def build_manifest(
    pages: Iterable[ParsedPage],
    *,
    parser_version: str = "",
) -> Dict:
    """生成路由质量报告：逐页记录 + tier/parser 分布摘要。"""
    ordered = sorted(pages, key=lambda p: p.page_idx)
    records: List[ManifestRecord] = []
    tier_count: Dict[str, int] = {}
    parser_count: Dict[str, int] = {}
    for page in ordered:
        route = page.route
        tier = route.tier if route else ""
        label = route.page_label if route else ""
        planned = route.parser if route else ""
        actual = page.parser or planned
        records.append(ManifestRecord(
            page=page.page_idx + 1,
            tier=tier,
            label=label,
            parser=actual,
            planned_parser=planned,
            reason=route.reason if route else "",
            confidence=route.confidence if route else 0.0,
            warnings=page.warnings,
        ))
        tier_count[tier] = tier_count.get(tier, 0) + 1
        parser_count[actual] = parser_count.get(actual, 0) + 1

    return {
        "parser_version": parser_version,
        "pages": [
            {
                "page": r.page,
                "tier": r.tier,
                "label": r.label,
                "parser": r.parser,
                "planned_parser": r.planned_parser,
                "reason": r.reason,
                "confidence": r.confidence,
                "warnings": list(r.warnings),
            }
            for r in records
        ],
        "summary": {
            "total_pages": len(records),
            "tier_distribution": tier_count,
            "parser_distribution": parser_count,
        },
    }
