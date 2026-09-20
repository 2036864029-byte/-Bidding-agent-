# 功能：法规书结构化快路径文本抽取（三档路由 T2A）。
"""
Fast-path structured text extraction for legal PDFs (T2A).

输入为鸭子类型页面对象（生产环境是 PyMuPDF `Page`，单测用 fake 页面），
不强制依赖 pymupdf。职责（对齐三档路由计划 §6 T2A）：
  1. 从 `get_text("dict")` 提取行级文本 + 字号/加粗/居中特征；
  2. 剔除独立页码；
  3. 按页面位置剔除页眉/页脚（法律标题即使位于页眉区也保留，避免误删章节）；
  4. 单栏按 (y, x) 稳定排序；
  5. 依据字号 + 居中 + 法规标题模式生成 Markdown 标题。
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

# 独立页码：如 "12"
_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")

# 行首页码前缀（页眉中页码逐页变化，剥离后才能识别同一条页眉）：
#   "78 一、招标投标" → "一、招标投标"。数字后跟列表符号（.．、）时是
#   列表编号不是页码（"1.一般规定17"），不剥离。
_HEADER_PAGENUM_PREFIX_RE = re.compile(r"^\d{1,4}(?![.．、])\s*")
# 行尾页码（侧标/书签：当前章节名 + 印刷页码逐页变化）：
#   "1.一般规定17" → "1.一般规定"。仅当页码紧跟 CJK/闭合括号时才剥离
#   （"GB 50430-2019" 数字前是 ASCII 字母/连字符 → 不动）。
_HEADER_PAGENUM_SUFFIX_RE = re.compile(r"(?<=[一-鿿）】」』》])\d{1,4}\s*$")
# 句末标点（含引号/括号闭合）：以这些结尾的行视为完整句
_SENTENCE_END_RE = re.compile(r"[。；！？…）」』\"']$")
# 含汉字（CJK 统一表意文字块）
_HAS_CJK_RE = re.compile("[一-鿿]")

# 法规标题模式（含级别映射）
_LEGAL_HEADING_PATTERNS = (
    (re.compile(r"^第[一二三四五六七八九十百千\d]+[编章]"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+节"), 3),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+[条款项]"), 4),
)

# 中文列表项：一、 / 二、 等
_CN_LIST_RE = re.compile(r"^[一二三四五六七八九十]+、")
# 括号列表项：（一）/（二）等
_CN_PAREN_LIST_RE = re.compile(r"^（[一二三四五六七八九十]+）")
# 数字编号（1200 问体例）：55. / 55、
_NUM_ITEM_RE = re.compile(r"^\d{1,4}[.、]")
# 答案起始行：答：/ 答
_ANSWER_RE = re.compile(r"^答\s*[:：]?")


@dataclass(frozen=True)
class TextLine:
    """一行文本及其版面特征。"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    centered: bool


def iter_lines(page: Any) -> List[TextLine]:
    """从页面 dict 提取行级文本特征（合并 span）。"""
    d = page.get_text("dict") or {}
    width = float(d.get("width") or 0)
    lines: List[TextLine] = []
    for block in d.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(str(s.get("text", "")) for s in spans)
            if not text.strip():
                continue
            sizes = [float(s.get("size", 0) or 0) for s in spans]
            size = max(sizes) if sizes else 0.0
            bold = any(int(s.get("flags", 0)) & 16 for s in spans)
            bbox = line.get("bbox") or (0, 0, 0, 0)
            x0, y0, x1, y1 = (float(v) for v in bbox[:4])
            center_x = (x0 + x1) / 2.0
            centered = bool(width) and abs(center_x - width / 2.0) / max(width, 1.0) < 0.06
            lines.append(TextLine(
                text=text.strip(), x0=x0, y0=y0, x1=x1, y1=y1,
                size=size, bold=bold, centered=centered,
            ))
    return lines


def remove_page_numbers(lines: Sequence[TextLine]) -> List[TextLine]:
    """剔除独立页码行。"""
    return [ln for ln in lines if not _PAGE_NUMBER_RE.match(ln.text)]


def normalize_header_text(text: str) -> str:
    """页眉候选归一化：去行首/行尾页码与空白（``78 一、招标投标`` →
    ``一、招标投标``；``1.一般规定17`` → ``1.一般规定``）。"""
    t = _HEADER_PAGENUM_PREFIX_RE.sub("", text.strip())
    t = _HEADER_PAGENUM_SUFFIX_RE.sub("", t)
    return t.strip()


def detect_header_candidates(
    pages: Sequence[Sequence[TextLine]],
    *,
    page_heights: Optional[Sequence[float]] = None,
    min_pages: int = 2,
    min_repeat: int = 5,
    max_len: int = 25,
    top_ratio: float = 0.15,
    bottom_ratio: float = 0.10,
) -> Set[str]:
    """全文档预扫：跨页重复且位于页顶/页底的短行 → 页眉候选集（通用规则）。

    页眉的两个本质特征 = **跨页重复出现** + **位于页面顶部/底部固定区**
    （running head）。二者都要，缺一不可：
      - 只查重复不查位置 → 误伤跨页重复的正文短语（实测：`(一)技术复杂…`
        这类评标标准常用语在全书跨 7 页重复）；
      - 只查位置不查重复 → 旧 top_ratio 硬切，99% 单栏页正文首行被误删。
    正文首行即使落在顶部区，也不跨页重复 → 双重确认下必然存活。

    信号：
      - Signal A（页码变化，强）：行首带页码，剥页码后文本在 ``>= min_pages``
        个页的页顶/页底区出现、且出现过 ``>= 2`` 个不同页码 → 候选。
        页眉页码逐页变化（``78 一、招标投标`` / ``288 一、招标投标``）。
      - Signal B（无页码稳定重复，弱）：文本在 ``>= min_repeat`` 个页的页顶/
        页底区出现 → 候选。口径与 cleaner/assembler 一致（默认 5）。

    豁免：法律标题（第X编/章/节/条/款/项）、纯数字、以句末标点结尾。

    Args:
        pages: 每页的行列表（page_idx 顺序）。
        page_heights: 每页页高（可选）。提供时启用位置佐证；为 None 时退回
            纯重复判断（单文件/无高度场景，过滤较弱）。
        min_pages: Signal A 的最少页顶/页底出现页数。
        min_repeat: Signal B 的最少页顶/页底出现页数。
        max_len: 归一化后的最大长度（字符）。

    Returns:
        归一化后的页眉候选文本集合。
    """
    def _in_zone(y0: float, height: float) -> bool:
        if not height:
            return True
        return y0 < height * top_ratio or y0 > height * (1 - bottom_ratio)

    text_to_pages: Dict[str, Set[int]] = defaultdict(set)
    text_to_zone_pages: Dict[str, Set[int]] = defaultdict(set)
    text_to_pagenums: Dict[str, Set[str]] = defaultdict(set)
    for page_idx, lines in enumerate(pages):
        height = page_heights[page_idx] if page_heights else 0.0
        for ln in lines:
            raw = ln.text.strip()
            m = _HEADER_PAGENUM_PREFIX_RE.match(raw)
            pagenum = m.group(0).strip() if m else ""
            t = _HEADER_PAGENUM_PREFIX_RE.sub("", raw).strip()
            ms = _HEADER_PAGENUM_SUFFIX_RE.search(t)
            if ms:
                if not pagenum:
                    pagenum = ms.group(0).strip()
                t = _HEADER_PAGENUM_SUFFIX_RE.sub("", t).strip()
            if not t or len(t) > max_len:
                continue
            if not _HAS_CJK_RE.search(t):
                continue
            if _is_legal_heading(t):
                continue
            if _SENTENCE_END_RE.search(t):
                continue
            text_to_pages[t].add(page_idx)
            if _in_zone(ln.y0, height):
                text_to_zone_pages[t].add(page_idx)
            if pagenum:
                text_to_pagenums[t].add(pagenum)

    candidates: Set[str] = set()
    for t in text_to_pages:
        n_zone = len(text_to_zone_pages.get(t, ()))
        n_pagenums = len(text_to_pagenums.get(t, ()))
        if n_zone >= min_pages and n_pagenums >= 2:
            candidates.add(t)  # Signal A：页码变化页眉（须在页顶/页底区）
        elif n_zone >= min_repeat and n_pagenums == 0:
            candidates.add(t)  # Signal B：无页码稳定重复页眉（须在页顶/页底区）
    return candidates


def remove_header_lines(
    lines: Sequence[TextLine],
    header_candidates: Optional[Sequence[str]] = None,
    *,
    page_height: float = 0.0,
    top_ratio: float = 0.15,
    bottom_ratio: float = 0.10,
) -> List[TextLine]:
    """按文档级页眉候选集剔除页眉行；不做按位置的硬切。

    header_candidates 为空/None 时不移除任何行（无上下文时宁可不删，避免
    误删正文首行）——抽取层不再依赖固定的顶部/底部比例区。

    page_height > 0 时启用位置佐证：候选文本只有落在页顶/页底区才删除，
    版心内同文本行是真实标题/正文（如侧标"1.一般规定17"与节标题
    "1.一般规定"同文）——双重确认与 detect_header_candidates 口径一致。
    """
    cand = set(header_candidates or ())
    if not cand:
        return list(lines)

    def _in_zone(ln: TextLine) -> bool:
        if not page_height:
            return True  # 无页高（legacy 口径）：仅按文本匹配
        return (
            ln.y0 < page_height * top_ratio
            or ln.y0 > page_height * (1 - bottom_ratio)
        )

    return [
        ln for ln in lines
        if normalize_header_text(ln.text) not in cand or not _in_zone(ln)
    ]


def sort_single_column(lines: Sequence[TextLine]) -> List[TextLine]:
    """单栏阅读顺序：按 y 升序，同 y 按 x 升序。"""
    return sorted(lines, key=lambda ln: (ln.y0, ln.x0))


def _is_legal_heading(text: str) -> bool:
    for pattern, _ in _LEGAL_HEADING_PATTERNS:
        if pattern.match(text):
            return True
    return False


def _heading_level(text: str, size: float, body_size: float, centered: bool) -> Optional[int]:
    """返回 Markdown 标题级别（1-6），非标题返回 None。"""
    for pattern, level in _LEGAL_HEADING_PATTERNS:
        if pattern.match(text):
            return level
    if _CN_LIST_RE.match(text):
        return 4
    if _CN_PAREN_LIST_RE.match(text):
        return 5
    if _NUM_ITEM_RE.match(text):
        return 4
    if body_size > 0:
        if centered and size >= body_size * 1.2:
            return 2
        if size >= body_size * 1.15:
            return 3
    return None


def generate_markdown(lines: Sequence[TextLine]) -> str:
    """把行列表转为轻量 Markdown（标题行加 #，正文行原样保留）。"""
    sizes = [ln.size for ln in lines if ln.size > 0]
    body_size = statistics.median(sizes) if sizes else 0.0

    out: List[str] = []
    for ln in lines:
        text = ln.text
        level = _heading_level(text, ln.size, body_size, ln.centered)
        if level is not None:
            out.append(f"{'#' * level} {text}")
        else:
            out.append(text)
    return "\n".join(out)


def extract_page_markdown(
    page: Any,
    page_idx: int,
    *,
    header_candidates: Optional[Sequence[str]] = None,
) -> str:
    """快路径单页抽取：提取 → 去页码 → 去页眉（文档级候选）→ 排序 → 生成 Markdown。

    页眉识别交给文档级 ``detect_header_candidates``（跨页重复，A1-1）；不再按
    页面位置硬切（旧 top_ratio 会误删单栏正文首行）。
    """
    lines = iter_lines(page)
    lines = remove_page_numbers(lines)
    lines = remove_header_lines(lines, header_candidates)
    lines = sort_single_column(lines)
    return generate_markdown(lines)
