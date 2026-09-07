# 功能：Markdown 结构归一（Phase A A3）— 标题级归一 / 标题内联拆分 / 外部 md 治理。
"""
A3 结构归一（纯文本级，供 clean_markdown 调用）：

  A3-1 normalize_heading_levels_full
        模式标题（编/章/节/条/款/项/部分）固定级；列表项（一、→4、（一）→5、
        1.→4）内容级；其余非模式标题取 min(最近锚级+1, 6)。全文按序单遍重写，
        修复 MinerU 扁平 # 与 fast_text 深度 # 混排导致的层级倒挂/压平。

  A3-2 split_inline_articles
        标题行内联正文拆分：`第X条【标题】正文` / `第X条（标题） 正文` /
        `第X条 正文` → 标题行 + 独立正文行（防止正文随标题被 SemanticChunker
        丢弃——302 处「标题吞正文」根因）。

  A3-3 normalize_external_md
        外部 markdown（cleaned_v1）治理：
          ① 引用误读标题校正——`## 第X条 第X款/的规定…` 长行是正文里的法条
             交叉引用被误判为标题，剥离 # 并入正文（457 处）；
          ② `#### （一）/一、` 列表项降级为 `- （一）` 正文列表（7295 处）；
          ③ `> 发布/来源` 元信息行剥离（349/349 文件）。

  A3-5 mark_front_matter / mark_toc_page
        书籍前置页（封面/CIP/前言/目录，首个 `第X编/章/节/条` 之前）标记或
        按配置剥离；目录页 `## 目 录` 标题标注为目录（不参与章节语义）。
        「不入 chunk」延后 Phase B——标记供切分链路消费。

  A4 repair_structure
        结构校验闭环：单遍扫描 + 定点修复 + 有界迭代（≤3 轮），残留修复
        A3 各归一函数遗漏的违规行，报告供 A5 保真验收脚本对照。
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

# ── A3-1 标题级映射 ───────────────────────────────────────
# 模式标题（固定级）+ 列表项（内容级）。与 fast_text._heading_level /
# pdf_markdown_assembler._LEGAL_HEADING_LEVELS 的既有约定一致。
_CONTENT_LEVELS: Tuple[Tuple[re.Pattern, int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百千\d]+编"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+部分"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+章"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+节"), 3),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+条"), 4),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+款"), 5),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+项"), 6),
    (re.compile(r"^[一二三四五六七八九十]+、"), 4),   # 一、 → 4
    (re.compile(r"^（[一二三四五六七八九十]+）"), 5),  # （一） → 5
    (re.compile(r"^\d{1,4}[.、]"), 4),                # 1. / 1、 → 4
)

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def _content_level(text: str) -> Tuple[Optional[int], bool]:
    """返回 (目标级, 是否结构锚)；非标题内容返回 (None, False)。"""
    for pat, level in _CONTENT_LEVELS:
        if pat.match(text):
            return level, True
    return None, False


def normalize_heading_levels_full(markdown: str) -> str:
    """A3-1：全文标题级归一（先收集、按序重写）。

    规则：
      - 模式/列表标题 → 固定级（不受来源 ``#`` 数量影响，修复白如银 ``# 节 >
        ``## 章`` 倒挂、MinerU 扁平 ``#`` 与 fast_text 深度 ``#`` 混排）；
      - 其余非模式标题（如 ``政府采购概述``）→ ``min(最近锚级 + 1, 6)``，
        锚 = 最近出现的模式/列表标题；无锚（前置页）→ 保持原级。

    不做分块、不改非标题内容。
    """
    out: List[str] = []
    last_anchor: Optional[int] = None
    for line in markdown.split("\n"):
        m = _MD_HEADING_RE.match(line)
        if not m:
            out.append(line)
            continue
        text = m.group(2).strip()
        level, is_anchor = _content_level(text)
        if is_anchor:
            last_anchor = level
        elif last_anchor is not None:
            level = min(last_anchor + 1, 6)
        else:
            level = len(m.group(1))  # 无锚（前置页）→ 保持原级
        out.append("#" * level + " " + text)
    return "\n".join(out)


# ── A3-2 标题行内联正文拆分 ───────────────────────────────
# 必须「标题 + 正文」两段齐全才拆；已拆出的纯标题行（行尾就是括号标题）不再匹配
# ——修复单正则回溯导致 `(.+)` 把行尾【标题】当正文二次拆分、吞掉标题的非幂等缺陷。
_BRACKET_INLINE_RE = re.compile(
    r"^(#{1,6})\s+(第[一二三四五六七八九十百千\d]+[条款项])"
    r"(\s*[（【][^】）]*[】）])"   # 括号标题（必选）：【…】或（…）（含前置空白）
    r"\s*(.+)$"                    # 正文（必选，与标题间可零空白）
)
_PLAIN_INLINE_RE = re.compile(
    r"^(#{1,6})\s+(第[一二三四五六七八九十百千\d]+[条款项])"
    r"\s+(?!\s*[（【])(.+)$"       # 无括号标题：正文须非空且不以括号开头
)


def split_inline_articles(markdown: str) -> str:
    """A3-2：把标题行内联正文拆为标题行 + 独立正文行。

    覆盖三种写法：``第X条【标题】正文``、``第X条  （标题） 正文``、``第X条 正文``。
    拆分后正文不再随标题行被 SemanticChunker 丢弃。幂等：已拆出的标题行不再拆。
    """
    out: List[str] = []
    for line in markdown.split("\n"):
        m = _BRACKET_INLINE_RE.match(line.strip())
        if m:
            # 分组：1=#, 2=条号, 3=括号标题, 4=正文
            out.append(f"{m.group(1)} {m.group(2)}{m.group(3)}".rstrip())
            out.append(m.group(4).strip())
            continue
        m = _PLAIN_INLINE_RE.match(line.strip())
        if m:
            # 分组：1=#, 2=条号, 3=正文
            out.append(f"{m.group(1)} {m.group(2)}")
            out.append(m.group(3).strip())
            continue
        out.append(line)
    return "\n".join(out)


# ── A3-3 外部 markdown（cleaned_v1）治理 ──────────────────
# ① 引用误读：`## 第X条 第X款/的规定…` 长行是正文中的法条交叉引用被误判为标题
_CITATION_MISREAD_RE = re.compile(
    r"^(#{1,6})\s+第[一二三四五六七八九十百千\d]+条"
    r"(\s+第[一二三四五六七八九十百千\d]+[款项]"
    r"|(?:的|之)?规定"
    r"|参照|依照|按照|适用|根据)"
)
_CITATION_MIN_LEN = 30  # 短标题即使匹配引用特征也不降级（避免误伤真实条标题）

# ② `#### （一）/一、` 列表项降级为正文列表
_H4_LIST_RE = re.compile(r"^####\s+([（【（(][一二三四五六七八九十\d]+[）)】]|[一二三四五六七八九十]+、)")

# ③ `> 发布/来源…` 元信息行
_META_BLOCKQUOTE_RE = re.compile(r"^>\s*(发布|来源|文号|施行|效力|时效)[：:]")


def _demote_citation_headings(markdown: str) -> str:
    """引用误读标题校正：长行的 `## 第X条 第X款/的规定…` 剥离 # 并入正文。"""
    out: List[str] = []
    for line in markdown.split("\n"):
        if len(line) > _CITATION_MIN_LEN and _CITATION_MISREAD_RE.match(line):
            out.append(_MD_HEADING_RE.sub(r"\2", line.strip()))
        else:
            out.append(line)
    return "\n".join(out)


def _demote_h4_lists(markdown: str) -> str:
    """`#### （一）/一、` 列表项降级为 `- （一）` 正文列表（不再当标题）。"""
    out: List[str] = []
    for line in markdown.split("\n"):
        m = _H4_LIST_RE.match(line)
        if m:
            out.append("- " + line.strip().lstrip("#").strip())
        else:
            out.append(line)
    return "\n".join(out)


def _strip_meta_blockquotes(markdown: str) -> str:
    """剥离 `> 发布/来源/文号…` 元信息行。"""
    return "\n".join(
        line for line in markdown.split("\n")
        if not _META_BLOCKQUOTE_RE.match(line.strip())
    )


def normalize_external_md(markdown: str) -> str:
    """A3-3：外部 markdown（cleaned_v1）治理。

    顺序：先做引用误读校正（必须早于 A3-2 拆分，否则交叉引用会被拆成虚假的
    ``第X条`` 标题），再降级 ``####`` 列表项、剥离元信息行。
    """
    markdown = _demote_citation_headings(markdown)
    markdown = _demote_h4_lists(markdown)
    markdown = _strip_meta_blockquotes(markdown)
    return markdown


# ── A3-5 前置页/目录标记 ─────────────────────────────────
# 结构锚：`第X编/部分/章/节/条/款/项` 标题行（书籍正文起点）。此前（封面/CIP/
# 前言/目录）为前置页。
_ANCHOR_HEADING_RE = re.compile(
    r"^#{1,6}\s+第[一二三四五六七八九十百千\d]+[编部分章节条款项]"
)
# 正文形态信号（前置剥离误伤守卫）：编号问项/列表行/答 行。手册体（1200问）
# 常以内容章开篇（"政府采购概述"+ 连续问项），首个 `第X编/章/节` 锚远在后面；
# 前置页（封面/CIP/序言/目录）绝无这些形态——锚前块含它们 = 剥离会吞真实正文。
_FRONT_BODY_SIGNAL_RE = re.compile(
    r"^(（\s*[0-9一二三四五六七八九十百]+\s*）|\([0-9一二三四五六七八九十百]+\)|"
    r"[0-9一二三四五六七八九十百]+[、.)．]|[-•·]|答\s*[:：])"
)
_TOC_HEADING_RE = re.compile(r"^#{1,6}\s+目\s*(录|次)\s*$")
_FRONT_START = "<!-- frontmatter:start -->"
_FRONT_END = "<!-- frontmatter:end -->"
_TOC_MARK = "<!-- toc: 目录 -->"


def mark_front_matter(markdown: str, *, skip: bool = False) -> str:
    """A3-5：识别书籍前置页（首个 `第X编/章/节/条` 结构标题之前），标记或剥离。

    - ``skip=False``（默认）：用 ``<!-- frontmatter:start/end -->`` 包裹标记，
      Phase B 消费标记决定是否入 chunk（「不入 chunk」延后 Phase B）；
    - ``skip=True``（``pdf_skip_front_matter``）：直接剥离前置页块。

    书籍路径专用（external 文档的标题/元信息是正文一部分，不做前置页处理）。
    无结构锚、前置块为空或已带标记 → 原样返回（幂等）。
    """
    if _FRONT_START in markdown or _FRONT_END in markdown:
        return markdown
    lines = markdown.split("\n")
    boundary = next(
        (i for i, line in enumerate(lines) if _ANCHOR_HEADING_RE.match(line.strip())),
        None,
    )
    if boundary is None:
        return markdown
    front = lines[:boundary]
    while front and not front[-1].strip():
        front.pop()
    while front and not front[0].strip():
        front.pop(0)
    if not front:
        return markdown
    if skip and _front_is_manual_body(front):
        # 误伤守卫：锚前块是手册体正文（编号问项 + 答 行），非前置页——首个
        # `第X` 锚远在真实开篇章之后，锚定模型在此不适用。剥离或标记都会吞/标
        # 真实正文，故原样返回（真实封面/CIP 已由路由层 tier S 跳过）。
        return markdown
    if skip:
        return "\n".join(lines[boundary:]).lstrip("\n")
    return "\n".join([_FRONT_START, *front, _FRONT_END, *lines[boundary:]])


def _front_is_manual_body(front: List[str]) -> bool:
    """前置块是手册体正文（而非封面/CIP/目录）？

    判据 = 出现 Q&A 形态：`答：` 解答行，或编号行/问句以 ？/吗/呢 收尾。
    法名标题下的沿革行（"1. 1999年8月30日通过"）不是问句、也无答行 → 不算。
    封面/前言/目录不含问句与答行 → 不算。
    """
    for l in front:
        s = l.strip().lstrip("#").strip()
        if not s:
            continue
        if re.match(r"^答\s*[:：]", s):
            return True
        if _FRONT_BODY_SIGNAL_RE.match(s) and s.rstrip().endswith(("？", "?", "吗", "呢")):
            return True
    return False


def _mark_toc_page(markdown: str) -> Tuple[str, int]:
    """目录页 `## 目 录` 标题 → `<!-- toc: 目录 -->`（不参与章节语义）。

    目录条目（点线行）由 ``strip_toc_noise`` 剔除；此处只处理标题本身。
    幂等：已是 toc 注释的不再标记。
    """
    out: List[str] = []
    count = 0
    for line in markdown.split("\n"):
        if _TOC_HEADING_RE.match(line):
            out.append(_TOC_MARK)
            count += 1
        else:
            out.append(line)
    return "\n".join(out), count


def mark_toc_page(markdown: str) -> str:
    """A3-5：目录页标题标注为目录（公开入口）。"""
    text, _ = _mark_toc_page(markdown)
    return text


# ── P3-2 mini 目录块识别（版面还原计划 2026-09-06）────────

_INLINE_TOC_HEAD_RE = re.compile(
    r"^(#{1,6})\s+(第[一二三四五六七八九十百千\d]+[编章节部分].{0,17})$"
)


# 合法级联判定：编/部分→章→节 层级严格递降且各出现一次（正文开篇正常形态）
_CASCADE_LEVELS = (("编", 1), ("部分", 1), ("章", 2), ("节", 3))


def _is_legal_heading_cascade(run: List[str]) -> bool:
    """连续标题行是「编→章→节」合法结构级联（如法条正文开篇）而非 mini 目录。

    目录条目必然同级重复（章、章…）或层级回跳；严格递降且不重复的
    ≤3 行级联只可能是正文结构。"""
    levels: List[int] = []
    for line in run:
        m = _INLINE_TOC_HEAD_RE.match(line.strip())
        if not m:
            return False
        text = m.group(2)
        for kw, lv in _CASCADE_LEVELS:
            if kw in text:
                levels.append(lv)
                break
        else:
            return False
    return levels == sorted(levels) and len(set(levels)) == len(levels)


def _is_content_line(line: str) -> bool:
    """内容行？非空、非结构标记（页标记/表格标签/inline-toc 标记）。

    P3 保留规则的"run 后跟内容"判定：标题行（如真实第一章后的
    `#### 第一条【立法目的】`）也算内容——书1 真实第一章后接的是条标题
    而非正文，只跟正文才保留会把它整段误剥（2026-09-07 实测定稿）。
    """
    s = line.strip()
    if not s:
        return False
    return not s.startswith("<!--")


_INLINE_TOC_MARK_RE = re.compile(r"^<!--\s*inline-toc:(start|end)\s*-->$")


def strip_inline_toc(markdown: str) -> str:
    """P3 mini 目录剥离（2026-09-07，用户决策：直接剥离，不保留标记）。

    连续 ≥2 个 ≤20 字章/节标题行（行间无正文）是 mini 目录块：
      - 块后跟内容（正文或条标题等非结构标记行）→ 末行是真实标题
        （书1 每法 mini 目录后紧跟真实"第一章 总 则"，P1 去重修复后存活，
        其后接 `#### 第一条【立法目的】` 或正文）→ 保留末行为标题，
        仅剥离 run 内其余行；
      - 否则（纯目录页：后跟页标记/表格标签/文档尾）→ 整段剥离。
    沿用 `_is_legal_heading_cascade` 守卫（编→章→节递降级联是正文结构）。
    剥离后不再产出 inline-toc 标记；旧标记行也被清除。幂等。
    """
    lines = markdown.split("\n")
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        m = _INLINE_TOC_HEAD_RE.match(stripped)
        if m and len(m.group(2).strip()) <= 20:
            run: List[str] = []
            j = i
            while j < n:
                s = lines[j].strip()
                if not s:
                    j += 1
                    continue
                mm = _INLINE_TOC_HEAD_RE.match(s)
                if mm and len(mm.group(2).strip()) <= 20:
                    run.append(lines[j])
                    j += 1
                else:
                    break
            if len(run) >= 2 and not _is_legal_heading_cascade(run):
                k = j
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and _is_content_line(lines[k]):
                    # run 后跟内容 → 末行是真实标题，保留；其余行剥离
                    out.append(run[-1])
                    out.append("")
                # 否则（纯目录页）整段剥离
                i = j
                continue
        if _INLINE_TOC_MARK_RE.match(stripped):
            i += 1  # 清除旧 inline-toc 标记
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


# ── A4 结构校验闭环 ──────────────────────────────────────
# 四条文本级规则，各自返回 (修复后文本, 修复数)。只做定点修复（不重跑整个
# cleaner），逐行比较输入输出计数。


def _repair_heading_levels(markdown: str) -> Tuple[str, int]:
    """① 标题层级：模式/列表标题固定级（编=1…项=6），残留错误级修正。

    与 A3-1 ``normalize_heading_levels_full`` 口径一致；外部路径的
    ``#### （一）`` 列表项已在 A3-3 降级为 ``- （一）``，此处只处理残留。
    """
    out: List[str] = []
    count = 0
    for line in markdown.split("\n"):
        m = _MD_HEADING_RE.match(line)
        if not m:
            out.append(line)
            continue
        text = m.group(2).strip()
        level, is_anchor = _content_level(text)
        if is_anchor and m.group(1) != "#" * level:
            out.append("#" * level + " " + text)
            count += 1
        else:
            out.append(line)
    return "\n".join(out), count


def _repair_inline_split(markdown: str) -> Tuple[str, int]:
    """② 标题内联正文：`第X条【…】正文` 后必须换行（每条拆出行数 +1）。"""
    out_lines = split_inline_articles(markdown).split("\n")
    return "\n".join(out_lines), len(out_lines) - len(markdown.split("\n"))


def _repair_citation_misread(markdown: str) -> Tuple[str, int]:
    """③ 引用误读：`第X条 第X款/的规定…` 且行长 >30 → 去标题标记并入正文。"""
    out = _demote_citation_headings(markdown)
    count = sum(
        1 for a, b in zip(markdown.split("\n"), out.split("\n")) if a != b
    )
    return out, count


# 执行顺序：先修层级（①）→ 去引用误读（③，须先于内联拆分，否则交叉引用会被
# 拆成虚假 `第X条` 标题）→ 拆内联正文（②）→ 标目录（④）。
_REPAIR_RULES: Dict[str, Callable[[str], Tuple[str, int]]] = {
    "heading_level": _repair_heading_levels,
    "citation_misread": _repair_citation_misread,
    "inline_split": _repair_inline_split,
    "toc_mark": _mark_toc_page,
}


def repair_structure(
    markdown: str, *, max_rounds: int = 3, rewrite_levels: bool = True
) -> Tuple[str, Dict[str, int]]:
    """A4 结构校验闭环：单遍扫描 + 定点修复 + 有界迭代（≤ ``max_rounds`` 轮）。

    对 A3 归一后文本做残留违规修复（不重跑 cleaner，防误伤正文）。报告
    ``{规则名: 累计修复数, "rounds": 实跑轮数}`` 供 A5 保真验收脚本对照——
    任一规则仍有计数 = 有残留未收敛，需人工核验。

    rewrite_levels=False（版面还原 v2）：跳过①层级重写——解析层版面锚定级
    为准，模式固定级不得覆盖。
    """
    text = markdown
    report: Dict[str, int] = {name: 0 for name in _REPAIR_RULES}
    rounds = 0
    for _ in range(max_rounds):
        rounds += 1
        total = 0
        for name, fn in _REPAIR_RULES.items():
            if name == "heading_level" and not rewrite_levels:
                continue
            text, n = fn(text)
            report[name] += n
            total += n
        if total == 0:
            break
    report["rounds"] = rounds
    return text, report
