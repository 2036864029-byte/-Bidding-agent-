# 功能：P1 版面段落装配——三判据段落合并 + 版面锚定标题判定（版面还原计划 2026-09-06）。
"""pdf_paragraph —— 解析层段落化：把 fast_text 的逐视觉行流装配成
"一个自然段 = 一行" 的逻辑段落 + 版面锚定标题。

目标数据形态（计划 §1）：折行是渲染行为不是数据行为——正文段落内零硬换行，
标题不再被印刷行宽截断。所有判定基于版面证据（坐标/字号/加粗），
文本模式只作辅助，泛化护栏：阈值全部相对实测基线（body_size/line_gap/页宽）。

段落三判据（任一命中 = 新段起点；prev 为上一行）：
  1. 首行缩进：x0 > left_edge + 1.5*body_size；
  2. 段尾排满：prev 行以句末标点收尾且未排到右缘（x1 < right_edge - 2*body_size）；
  3. 垂直间隙：y0 间距 > 1.5*line_gap（典型行距取正 y0 差的下四分位）。

标题判定版面优先（修 Q2 H4 洪水）：size ≥ 1.15*body_size 或
（加粗 + 短行 + 非句式收尾）→ 标题；**模式命中但字号≈正文且不加粗 → 正文**
（`第三十六条第一款判定…` 不再误入标题）。标题同风格短碎片（跨行截断）
吸收合并（修 Q3 标题截断）。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .pdf_fast_text import TextLine

# 句末标点（段落收尾证据）
_SENT_ENDERS = "。！？…；"
# 标题不应以这些字符收尾（句式/引导式收尾 → 更像正文）
_HEAD_BAD_TAIL = _SENT_ENDERS + "，、："

# 结构起始行（列表项/问答/条文起始，正文流中应另起一段）
_STRUCT_START_RE = re.compile(
    r"^(（\s*[0-9一二三四五六七八九十百]+\s*）|\([0-9一二三四五六七八九十百]+\)"
    r"|[0-9一二三四五六七八九十百]+[、.)．]|[-•·]|答\s*[:：]"
    r"|第[一二三四五六七八九十百千\d]+[编章节部分条款项])"
)

# 章/节/编 → markdown 级（版面大字号 + 结构词 → 固定级）
_CHAPTER_LEVEL_RE: tuple = (
    (re.compile(r"^第[一二三四五六七八九十百千\d]+编"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+章"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+节"), 3),
)

# 结构模式兜底（OCR 件无字号/加粗证据时的标题识别，守卫防正文误判）：
#   编/章/节：标记后须有空白（"第四章 开标"）或整行只有标记（"第四章"）——
#   正文引用（"第四章规定…"）标记后直接接汉字 → 不判；
#   条：须带【题】（法规全书体）；正文引用行（含句读）不判；
#   列表（一、/（一）/N.）：≤30 字且无句读。
_STRUCT_TITLE_RE: tuple = (
    (re.compile(r"^(第[一二三四五六七八九十百千\d]+编)(\s|$|【)"), 1),
    (re.compile(r"^(第[一二三四五六七八九十百千\d]+部分)(\s|$|【)"), 1),
    (re.compile(r"^(第[一二三四五六七八九十百千\d]+章)(\s|$|【)"), 2),
    (re.compile(r"^(第[一二三四五六七八九十百千\d]+节)(\s|$|【)"), 3),
)
_ARTICLE_TITLE_RE = re.compile(r"^第[一二三四五六七八九十百千\d]+[条款项]【")
_LIST_HEAD_RE = re.compile(
    r"^(（[0-9一二三四五六七八九十百]+）|[0-9一二三四五六七八九十百]+、"
    r"|[0-9]{1,3}[.．])"
)
# 列表形态标题的层级细分（与 A3-1 模式级语义对齐：一、→4 /（一）→5 / 1.→6；
# 版面证据只判"是不是标题"，文本模式判"是哪一级"）。
_LIST_SUBLEVEL_RE: tuple = (
    (re.compile(r"^[0-9]{1,3}[.．]"), 6),
    (re.compile(r"^（[0-9一二三四五六七八九十百]+）"), 5),
    (re.compile(r"^[0-9一二三四五六七八九十百]+、"), 4),
)
_PUNCT_IN_TITLE = "。？！，；："

# 结构起始行允许断段的上一行收尾符（句末 + 冒号/括号/引号闭合——列表项
# 前的引导句"下列情形："、条文标题"【题】"之后）。
_PARA_BREAK_TAIL = "。！？…；：】）”」』"

# 标题吸收循环的守卫：下一行是新的结构标题/目录条目（第X编/章/节/部分开头）
# → 不吸收。修目录页泄漏（"第一章 总 则" + "第二章 招标" 相邻两条目被合并）。
_NEXT_STRUCT_HEAD_RE = re.compile(r"^第[一二三四五六七八九十百千\d]+[编章节部分]")

# ── P2 法规名 → H1（书1 法规合集，RC1，2026-09-07）───────────
# 法规名（非加粗非居中、无 `第X` 模式）在多层字号页上落入模式兜底 → None 变正文。
# 本规则用内容形态 + 版面证据（间隙隔离/居中/粗字号）替代 size_ok 门禁：
# 形态 = `中华人民共和国` 开头，或尾部命中法规体关键词（书名号内引用不命中——
# `《中华人民共和国招标投标法》` 以 `》` 收尾）。
_REGULATION_NAME_HEAD_RE = re.compile(r"^中华人民共和国")
_REGULATION_TAIL_RE = re.compile(
    r"(法|条例|办法|规定|细则|暂行规定|暂行办法|实施办法|实施意见|通知|函|纲要|规划)$"
)
# 图/表题注不得当法规名（"表3 投诉处理办法"）
_REGULATION_CAPTION_RE = re.compile(
    r"^(图|表|附表|附图|案例|例题|习题|模板)\s*[0-9０-９]"
)
# 句中句读（正文句段形态，截断行/跨行正文首行）：法规名/文件标题不含量词、句读
# ——"中华人民共和国境内外的法人、自然人…"（、）、"…记录，由执法"（，）实测
# 被当法规名误判 H1 的正文片段都有此信号。
_MID_SENT_PUNCT_RE = re.compile(r"[，。；：、？]")
# 沿革行降级（用户定 D1=B）：编号行 + 年份 + 通过/公布/发布/施行/修正 语义 →
# 一律正文，不判 H6（现状：书1 中段法沿革行的数字项被列表模式拍成 H6 垃圾标题）。
# 年份+语义双约束使其不会误伤普通数字列表（"1.项目概况"）。"发布"= 沿革行
# 常见动词（书1 政策文件"…国家发展和改革委员会发布"实测），与 通过/公布 同类。
_YANGE_LINE_RE = re.compile(
    r"^(?:[0-9一二三四五六七八九十百]+[、.)．]\s*)?"
    r".*\d{4}\s*年.*(?:通过|公布|发布|施行|修正)"
)


@dataclass(frozen=True)
class ColumnMetrics:
    """一页（或一栏）文本流的版面基线。"""

    left_edge: float    # 正文左缘（x0 低分位）
    right_edge: float   # 正文右缘（x1 最大值）
    line_gap: float     # 典型行距（栏内正 y0 差的下四分位）
    body_size: float    # 正文字号（字号众数，并列取小）
    size_spread: float = 0.0  # 正文字号离散度（p90-p10，OCR 噪声大时禁用字号判定）


@dataclass
class AssembledBlock:
    """装配产物：heading（带级）/ paragraph（一段一行）/ raw（逐行保留）。"""

    kind: str
    text: str
    level: int = 0


def _percentile(sorted_vals: Sequence[float], ratio: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(ratio * (len(sorted_vals) - 1))
    return float(sorted_vals[idx])


def compute_column_metrics(lines: Sequence[TextLine]) -> ColumnMetrics:
    """从行流实测版面基线（无任何硬编码页面参数）。

    line_gap 按栏聚类计算：双栏页左右栏同一高度的行在全局 y 排序下互相
    穿插，产生 ~1pt 假行距，会让垂直间隙判据每行触发断段（书1 实测）。
    """
    valid = [ln for ln in lines if ln.text.strip()]
    if not valid:
        return ColumnMetrics(0.0, 0.0, 0.0, 10.5)
    xs0 = sorted(ln.x0 for ln in valid)
    xs1 = sorted(ln.x1 for ln in valid)
    sizes = [ln.size for ln in valid if ln.size > 0]
    # 正文字号 = 众数（并列取小）：相对判定需要"最多行共享"的字号作锚，
    # 避免标题密集页把标题字号当正文基线。
    body_size = 10.5
    if sizes:
        counts = Counter(round(s, 2) for s in sizes)
        best = max(counts.values())
        body_size = min(s for s, c in counts.items() if c == best)
    left_edge = _percentile(xs0, 0.2)
    right_edge = float(xs1[-1])
    # 正文字号离散度：只统计接近正文的字号（剔除页码/书眉小字号极值）。
    # OCR 件正文行字号连续抖动（书1 实测 p90-p10 ≈ 12%），无离散层级可言；
    # born-digital 件正文同字号（离散度≈0）→ 字号判定可信。
    band = [s for s in sizes if body_size * 0.75 <= s <= body_size * 1.5]
    size_spread = 0.0
    if len(band) >= 5:
        band.sort()
        size_spread = _percentile(band, 0.9) - _percentile(band, 0.1)
    # 按栏聚类收集栏内相邻行距（y0 升序、同栏内）。
    # 三个污染源（书1 实测）：① 同印刷行表元/碎片 y 差近零（0.1~1pt）；
    # ② 表行距（小字号行距，< 正文行距但 > 行内碎片）；③ x0 簇被过渡行
    # 桥接（居中标题跨栏连成单簇）→ 表行距与正文行距混入同一样本池。
    # 对策：差值 < 正文字号者不计（同印刷行与小字号表行都不是正文行距），
    # 取中位数（p25 会被 <25% 的密集小区拖垮；正文行距占样本多数时中位数
    # 稳定）。仍按簇统计并以 ≥5 样本簇的最大值为准（最干净的栏给出真行距）。
    clusters = _cluster_lines_by_x0(valid, right_edge - left_edge)
    all_gaps: List[float] = []
    cluster_medians: List[float] = []
    for cluster in clusters:
        ys = sorted(ln.y0 for ln in cluster)
        gs = [b - a for a, b in zip(ys, ys[1:]) if b - a >= body_size]
        all_gaps.extend(gs)
        if len(gs) >= 5:
            cluster_medians.append(_percentile(sorted(gs), 0.50))
    if cluster_medians:
        line_gap = max(cluster_medians)
    elif all_gaps:
        line_gap = _percentile(sorted(all_gaps), 0.50)
    else:
        line_gap = body_size * 1.5
    return ColumnMetrics(left_edge, right_edge, line_gap, body_size, size_spread)


def is_paragraph_start(
    line: TextLine, prev: Optional[TextLine], m: ColumnMetrics,
    *, col_left: Optional[float] = None,
) -> bool:
    """三判据：首行缩进（按本栏左缘）/ 段尾排满（句末收尾才有效）/ 垂直间隙。

    col_left：该行所属栏的左缘（双栏阅读序下右栏行的 x0 远大于全页左缘，
    须按本栏左缘判缩进，否则右栏每行都被误判"缩进"断段）；缺省用全页左缘。
    """
    if prev is None:
        return True
    left = m.left_edge if col_left is None else col_left
    if line.x0 > left + 1.5 * m.body_size:
        return True
    if (
        prev.text.rstrip()[-1:] in _SENT_ENDERS
        and prev.x1 < m.right_edge - 2 * m.body_size
    ):
        return True
    if line.y0 - prev.y0 > 1.5 * m.line_gap:
        return True
    return False


def _cluster_lines_by_x0(
    lines: Sequence[TextLine], span: float
) -> List[List[TextLine]]:
    """按 x0 间隙聚类行（栏间隙远大于段首缩进：> 0.3×版心宽 → 分栏）。"""
    if not lines:
        return []
    thr = 0.3 * span if span > 0 else float("inf")
    ordered = sorted(lines, key=lambda ln: ln.x0)
    clusters: List[List[TextLine]] = [[ordered[0]]]
    prev_x = ordered[0].x0
    for ln in ordered[1:]:
        if ln.x0 - prev_x > thr:
            clusters.append([ln])
        else:
            clusters[-1].append(ln)
        prev_x = ln.x0
    return clusters


def _cluster_left_edges(lines: Sequence[TextLine], m: ColumnMetrics) -> List[float]:
    """x0 聚类 → 每行所属栏的左缘。

    栏间隙远大于段首缩进（> 0.3×版心宽）→ 聚成不同栏；栏内缩进/浮动
    不分裂。单栏页只有一簇 = 全页左缘。
    """
    clusters = _cluster_lines_by_x0(lines, m.right_edge - m.left_edge)
    edge_of: dict = {}
    for cluster in clusters:
        col_min = min(ln.x0 for ln in cluster)
        for ln in cluster:
            edge_of[ln.x0] = col_min
    return [edge_of[ln.x0] for ln in lines]


def _cluster_right_edges(
    lines: Sequence[TextLine], m: ColumnMetrics
) -> List[Tuple[float, bool]]:
    """x0 聚类（栏内共享左缘）→ 每行 (本栏右缘, 右缘是否确立)。

    右缘 = 栏内最大 x1。短标题的 x1 天然远离正文右缘，若按 x1 聚类会被
    独立成"栏"、右缘变成它自己 → 守卫误杀一切短标题；故必须按 x0 聚栏。
    可靠性门：至少 2 行到达栏右缘 1 字号内（右缘由正文排满行确立）才
    返回 supported=True；单行页/右缘由本行自立的退化栏 → False（守卫
    禁用，置信不足回退旧行为）。
    """
    if not lines:
        return []
    clusters = _cluster_lines_by_x0(lines, m.right_edge - m.left_edge)
    info: dict = {}
    for cluster in clusters:
        col_max = max(ln.x1 for ln in cluster)
        near = sum(1 for ln in cluster if ln.x1 >= col_max - m.body_size)
        for ln in cluster:
            info[ln.x0] = (col_max, near >= 2)
    return [info[ln.x0] for ln in lines]


def _gap_isolated(lines: Sequence[TextLine], i: int, m: ColumnMetrics) -> bool:
    """行 i 与上下邻行均被 ≥1.5×line_gap 的空隙隔开（法规名 H1 的版面证据）。

    页首/页尾行缺一侧邻行 → 不算隔离（由 居中/字号 证据兜底，宁漏勿错）。
    """
    n = len(lines)
    if i <= 0 or i >= n - 1:
        return False
    return (
        lines[i].y0 - lines[i - 1].y0 > 1.5 * m.line_gap
        and lines[i + 1].y0 - lines[i].y0 > 1.5 * m.line_gap
    )


def heading_level_layout(
    line: TextLine, m: ColumnMetrics, *, col_right: Optional[float] = None,
    gap_isolated: Optional[bool] = None,
) -> Optional[int]:
    """版面锚定标题级；非标题返回 None。

    两级判定：
      1. 版面证据（优先）：字号属离散高层级（≥1.10×正文）或加粗短行
         （≤40 字、非句式收尾）→ 标题；章/节/编结构词 → 固定级；
      2. 结构模式兜底：OCR 件字号噪声 ±8%、加粗缺失（书1 实测章标题
         9.97 与正文 9.0~10.5 完全重叠），版面证据不存在时按结构词识别，
         守卫（空白/【题】/无句读）防正文引用误判。

    col_right：本栏右缘（排满守卫用）；None = 右缘未确立/外部调用 → 守卫禁用。
    排满守卫（书2 实测）：折行正文首行（列表项 `（2）…` 恰 ≤30 字、
    可见部分无句读）x1 顶到本栏右缘——真实标题不可能排满版心，
    版面证据优先于文本模式。

    gap_isolated：本行与上下邻行被 ≥1.5×line_gap 空隙隔开（法规名 H1 的
    版面证据，P2 由调用方传入）；None = 未计算/外部调用 → 该证据禁用。
    """
    text = line.text.strip()
    if not text or len(text) > 60:
        return None
    # P2 沿革行降级（D1=B）：含年份 + 通过/公布/施行/修正 语义 → 一律正文。
    # 必须在版面分支之前：中段法沿革行加粗编号项现被拍成 H6。
    if _YANGE_LINE_RE.match(text):
        return None
    full_width = (
        col_right is not None
        and col_right - m.left_edge > 0
        and line.x1 >= col_right - 1.5 * m.body_size
    )
    # 版面字号判定须页级可靠性门：正文字号离散度 ≤ 6% 才存在可用层级
    # （OCR 件 12% 连续抖动 → 禁用，退到结构模式）。
    size_ok = m.size_spread <= 0.06 * m.body_size
    big = (
        size_ok
        and line.size >= m.body_size * 1.10
    )
    bold_short = (
        line.bold
        and len(text) <= 40
        and not text.endswith(tuple(_HEAD_BAD_TAIL))
        and not full_width
    )
    # 手册体问项（书3：1200 个 `N.xxx？`）：加粗 + 编号开头 + 非排满 → 标题候选，
    # 不受句末符（？/。…）veto——疑问标题天然以 ？ 结尾，编号编码"是不是项/哪级"，
    # 版面（加粗/独立短行/非排满）证"是不是独立标题行"。无编号的加粗句尾行
    # （如"…应当遵循…原则。"）仍是正文（_HEAD_BAD_TAIL 守卫保留）。
    numbered_bold = (
        line.bold
        and len(text) <= 60
        and not full_width
        and bool(_LIST_HEAD_RE.match(text))
    )
    if big or bold_short or numbered_bold:
        for pat, lv in _CHAPTER_LEVEL_RE:
            if pat.match(text):
                return lv
        # 列表模式细分先于居中大字定级：编号编码了层级、字号只证"是标题"。
        # 居中大字的编号行（书3 手册体问项 "65.xxx？"）应落编号级而非 H1/H2。
        # 2026-09-06 书1 干跑 42 页次零波及（书1 居中大字行=编/章/节，已被
        # _CHAPTER_LEVEL_RE 先接走；编号款项行=正文字号，不进此分支）。
        lv = _list_pattern_level(text)
        if lv is not None:
            return lv
        if line.centered and line.size >= m.body_size * 1.2:
            return 1 if line.size >= m.body_size * 1.4 else 2
        if line.bold:
            return 4
        return 3 if line.size >= m.body_size * 1.3 else 4
    if full_width and _LIST_HEAD_RE.match(text):
        # 列表模式命中 + 排满本栏版心 → 折行正文首行，不判标题
        # （`（N）`/`一、`/`N.` 兜底路径的版面守卫）。
        return None
    level = _pattern_level(text)
    if level is not None:
        return level
    return _regulation_name_level(
        line, m, col_right=col_right, gap_isolated=gap_isolated
    )


def _regulation_name_level(
    line: TextLine, m: ColumnMetrics,
    *, col_right: Optional[float] = None, gap_isolated: Optional[bool] = None,
) -> Optional[int]:
    """P2 法规名 → H1（RC1：法规名被当正文）。全部条件满足才命中。

    内容形态（`中华人民共和国` 开头 / 尾部法规体关键词）+ 版面证据（任一）
    替代 size_ok 门禁（多层字号页字号离散度大 → 门禁整页禁用字号证据）。
    排除：句末标点收尾、排满版心、结构/列表前缀（走既有模式路径）、
    图/表题注。级别固定 1（H1）。
    """
    text = line.text.strip()
    if not (4 <= len(text) <= 40):
        return None
    if not (
        _REGULATION_NAME_HEAD_RE.match(text)
        or _REGULATION_TAIL_RE.search(text)
    ):
        return None
    if text.endswith(tuple(_HEAD_BAD_TAIL)):
        return None
    if _REGULATION_CAPTION_RE.match(text):
        return None
    if _MID_SENT_PUNCT_RE.search(text):
        # 句中句读 → 正文句段/截断行，不判法规名（书1 实测正文片段误判）
        return None
    if _STRUCT_START_RE.match(text) or _LIST_HEAD_RE.match(text):
        return None
    full_width = (
        col_right is not None
        and col_right - m.left_edge > 0
        and line.x1 >= col_right - 1.5 * m.body_size
    )
    if full_width:
        return None
    if (
        gap_isolated is True
        or line.centered
        or line.size >= m.body_size * 1.05
    ):
        return 1
    return None


def _list_pattern_level(text: str) -> Optional[int]:
    """列表形态标题的层级细分（仅匹配前缀，不设长度/句读守卫——那是
    "是不是标题"的判定，调用方已各自把关）。"""
    for pat, lv in _LIST_SUBLEVEL_RE:
        if pat.match(text):
            return lv
    return None


def _pattern_level(text: str) -> Optional[int]:
    """结构模式兜底（版面证据不可用/不足时的标题识别）。"""
    for pat, lv in _STRUCT_TITLE_RE:
        if pat.match(text):
            return lv
    if _ARTICLE_TITLE_RE.match(text):
        return 4
    if (
        _LIST_HEAD_RE.match(text)
        and len(text) <= 30
        and not any(ch in text for ch in _PUNCT_IN_TITLE)
    ):
        return _list_pattern_level(text)
    return None


def _same_style(a: TextLine, b: TextLine, m: ColumnMetrics) -> bool:
    return abs(a.size - b.size) <= 0.2 * m.body_size and a.bold == b.bold


def _is_raw_line(
    line: TextLine, m: ColumnMetrics, prev: Optional[TextLine] = None,
    *, col_right: Optional[float] = None,
) -> bool:
    """逐行保留（P1 不猜结构）：表格行 / 下划线填空行 / 居中短行（图题表题）。

    题注判据须宽度佐证：整行铺满版心的正文行几何上必然"居中"（大字号书
    尤其如此），只有显著短于版心（< 0.7×版心宽）的居中短行才是题注；
    且须与上一行有留白或上一行句末收尾（段落行末碎片紧贴正文行）。
    """
    text = line.text.strip()
    if not text:
        return False
    if text.startswith("|"):
        return True
    if "_" in text or "＿" in text:
        return True
    col_width = m.right_edge - m.left_edge
    if (
        line.centered
        and len(text) <= 30
        and col_width > 0
        and (line.x1 - line.x0) < 0.7 * col_width
        and heading_level_layout(line, m, col_right=col_right) is None
    ):
        # 题注与上一行之间有留白，或上一行以句末收尾（图/表位于句间）。
        # 紧贴"无句末收尾正文行"的居中短行是段落行末碎片——右栏行首短行
        # 几何中心恰落页中 ±6%（OCR 书实测"决所有投标。"）。
        if prev is None:
            return True
        gap_isolated = line.y0 - prev.y0 > 1.5 * m.line_gap
        prev_complete = prev.text.rstrip()[-1:] in _SENT_ENDERS
        return gap_isolated or prev_complete
    return False


def _join_cjk(parts: Sequence[str]) -> str:
    """行拼接：CJK 直接相连；ASCII 边界补空格（GB 50430-2019 类）。"""
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and (
            (out[-1].isascii() and out[-1].isalnum())
            or (p[0].isascii() and p[0].isalnum())
        ):
            out += " "
        out += p
    return out


def assemble_paragraphs(lines: Sequence[TextLine]) -> List[AssembledBlock]:
    """逐视觉行流 → 逻辑块列表（heading/paragraph/raw）。

    raw 行（表格/填空/图题）逐行保留，不并入段落也不互相合并。
    """
    valid = [ln for ln in lines if ln.text.strip()]
    if not valid:
        return []
    m = compute_column_metrics(valid)
    col_lefts = _cluster_left_edges(valid, m)
    col_right_eff = [
        edge if supported else None
        for edge, supported in _cluster_right_edges(valid, m)
    ]
    blocks: List[AssembledBlock] = []
    i = 0
    n = len(valid)
    while i < n:
        ln = valid[i]
        if _is_raw_line(
            ln, m, prev=valid[i - 1] if i > 0 else None,
            col_right=col_right_eff[i],
        ):
            blocks.append(AssembledBlock("raw", ln.text.strip()))
            i += 1
            continue
        level = heading_level_layout(
            ln, m, col_right=col_right_eff[i],
            gap_isolated=_gap_isolated(valid, i, m),
        )
        rest = ""
        start_i = i + 1   # 段落流起始行 index（默认从 ln 的下一行起）
        if level is not None:
            merged = ln.text.strip()
            # 法规体行内条文：`第X条【题】正文` 同印一行（主流形态）→ 】后是
            # 条文正文，切出接续段落流；视觉行截断时正文残部（"在中华人"）
            # 会粘进标题，clean 层 A3-2 再切只能落成孤立短行（书1 实测）。
            if _ARTICLE_TITLE_RE.match(merged) and "】" in merged:
                cut = merged.index("】") + 1
                rest = merged[cut:]
                merged = merged[:cut]
                blocks.append(AssembledBlock("heading", merged, level))
                if not rest:
                    i += 1
                    continue
                start_i = i + 1
                prev = ln
            else:
                # 标题跨行截断：吸收同风格短碎片（≤6 字）；
                # 【题】跨印刷行（】在次行）：吸收至 】 并切分标题/正文余部。
                j = i + 1
                absorbed = 0
                while j < n and absorbed < 2:
                    nxt = valid[j]
                    if not _same_style(nxt, ln, m):
                        break
                    frag = nxt.text.strip()
                    if not frag or len(merged) > 45:
                        break
                    if _NEXT_STRUCT_HEAD_RE.match(frag):
                        break  # 下一结构标题/目录条目，不吸收
                    if len(frag) <= 6:
                        merged += frag
                        absorbed += 1
                        j += 1
                        continue
                    if "【" in merged and "】" not in merged[merged.index("【"):]:
                        merged += frag
                        j += 1
                        if "】" in frag:
                            cut = merged.index("】") + 1
                            rest = merged[cut:]
                            merged = merged[:cut]
                        break
                    break
                blocks.append(AssembledBlock("heading", merged, level))
                i = j
                if not rest:
                    continue
                # 正文余部（【题】之后的条文正文）接续为段落流
                start_i = i
                prev = valid[i - 1]
        else:
            prev = ln
        i = start_i
        parts = [rest] if level is not None else [ln.text.strip()]
        while i < n:
            nxt = valid[i]
            if _is_raw_line(nxt, m, prev=prev, col_right=col_right_eff[i]):
                break
            if heading_level_layout(
                nxt, m, col_right=col_right_eff[i],
                gap_isolated=_gap_isolated(valid, i, m),
            ) is not None:
                break
            # 结构起始行（列表/条文）只在上一行句末/引闭收尾时才断段——
            # 上一行无句读收尾时"一、"是跨行词的一部分（"统一、"）。
            if _STRUCT_START_RE.match(nxt.text.strip()) and (
                prev.text.rstrip()[-1:] in _PARA_BREAK_TAIL
            ):
                break
            if is_paragraph_start(nxt, prev, m, col_left=col_lefts[i]):
                break
            parts.append(nxt.text.strip())
            prev = nxt
            i += 1
        blocks.append(AssembledBlock("paragraph", _join_cjk(parts)))
    return blocks


def paragraph_markdown(lines: Sequence[TextLine]) -> str:
    """行流 → 段落化 markdown（一段一行；空行分隔块）。"""
    blocks = assemble_paragraphs(lines)
    out: List[str] = []
    for b in blocks:
        if b.kind == "heading":
            out.append(f"{'#' * min(b.level, 6)} {b.text}")
        else:
            out.append(b.text)
    return "\n\n".join(out)
