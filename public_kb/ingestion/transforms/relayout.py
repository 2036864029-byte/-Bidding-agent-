# 功能：Phase A 后置排版重构（R1/R2 兜底）——标题语义修复（书路径 clean 链最后一环）。
"""
relayout_pipeline —— clean 链最后一环（纯文本、逐行、幂等）。

背景：解析层（pdf_paragraph）已产出"一段一行 + 版面锚定标题"，R4（标题续行补全）
与 R5（段内句末粘合 + 44 字整栏折行）已由它取代（折行属渲染行为，不烧进数据）。
灰度开关 pdf_layout_v2 验收后删除旧路径（2026-09-07），本模块只保留两条兜底：

  R1 目录点线残留被 H4 化（``#### 1.一般规定 3``）         → 整行剔除（原位补空行）
  R2 法条引用被误提为标题（``#### 第X条…的规定，…"``）      → 去掉 # 并入正文

幂等约定：R1/R2 删/降后不再命中。全链满足 clean(clean(x)) == clean(x)。

仅用于书路径（external md 治理路径不调用）。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# 句末折行断点（R1 判别短标题无句读用）
_SENT_ENDERS = "。！？…；"


def _split_heading(line: str) -> Optional[Tuple[str, str]]:
    """返回 (hashes, text)；非标题行返回 None。"""
    m = _MD_HEADING_RE.match(line.strip())
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def _is_blank(line: str) -> bool:
    return not line.strip()


# ── R1 目录点线残留标题剔除 ────────────────────────────────
# "1.一般规定 3" / "3.招标程序 47" / "5.领域规范 121"——行尾页码且极短、无句读。
_TOC_LEADER_RE = re.compile(r"^\d{1,4}\s*[.、．]\s*\S.{0,28}?\s+\d{1,4}$")


def _is_toc_leader_heading(text: str) -> bool:
    if len(text) > 40 or any(ch in text for ch in _SENT_ENDERS + "，"):
        return False
    return bool(_TOC_LEADER_RE.match(text))


def drop_toc_leader_headings(markdown: str) -> str:
    """R1：剔除被 H4 化的目录点线残标题（``1.一般规定 3``），原位补空行防止两段正文粘连。"""
    lines = markdown.split("\n")
    out: List[str] = []
    for idx, line in enumerate(lines):
        h = _split_heading(line)
        if h and _is_toc_leader_heading(h[1]):
            # 前一行非空 → 已输出内容需以空行结尾；后行非空则不吞并
            if out and not _is_blank(out[-1]):
                out.append("")
            continue  # 丢弃该标题行；是否在末尾补空行由下一行处理
        if not out and not line.strip():
            continue  # 收敛前导空行
        out.append(line)
    # 去除因剔除而在中部连排的多余空行（最多保留 1 个连续空行）
    collapsed: List[str] = []
    prev_blank = False
    for line in out:
        b = _is_blank(line)
        if b and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = b
    return "\n".join(collapsed)


# ── R2 引用误作标题降级（internal 路径补 A3-3 缺口）────────
# 引用长标题特征：无【】主旨、超长、含法条引用语，或以句号/引号收尾。
_CITATION_HINT = re.compile(
    r"(的规定|参照|依照|根据|按照|适用|是指|认定|应当|情形|所称)"
)


def _is_citation_heading(text: str) -> bool:
    """internal 引用误读标题判定：第X条开头、无【】主旨、偏长且呈正文句子形态。

    白如银式：``第七百九十一条第二款、第三款的规定，认定无效。”`` /
    ``第四十六条和第四十八条的规定。据此，…不是该设``（超 20 字即判）。
    全书条文标题经 A3-2 拆分后均带【】或短于 20 字，不会命中。
    """
    if len(text) <= 20:
        return False
    if "【" in text or "】" in text:
        return False
    if not re.match(r"^第[一二三四五六七八九十百千\d]+条", text):
        return False
    # 正文句式信号：句号收尾/句中出现、法条引用语、条款项连写
    if "。" in text:
        return True
    if re.search(r"第[一二三四五六七八九十百千\d]+[条款项]", text):
        return True
    return bool(_CITATION_HINT.search(text))


def demote_citation_headings(markdown: str) -> str:
    """R2：把被误提为标题的法条引用/正文句去掉 #，交正文。"""
    out: List[str] = []
    for line in markdown.split("\n"):
        h = _split_heading(line)
        if h and _is_citation_heading(h[1]):
            out.append(h[1])
        else:
            out.append(line)
    return "\n".join(out)


# ── 总入口 ─────────────────────────────────────────────────


def relayout_pipeline(markdown: str) -> str:
    """R1→R2 依序执行（书路径，A4 之后调用）。

    R3（卷首书名/法名标题保留）需与 mark_front_matter 协同，在 handoff.clean_markdown 内处理。
    R4/R5 已于 2026-09-07 随 pdf_layout_v2 灰度开关删除——解析层段落化取代。

    收敛到不动点：两轮内必然稳定（R1/R2 删/降后不再命中），满足
    clean(clean(x)) == clean(x)（缓存路径不漂移）。
    """
    text = drop_toc_leader_headings(markdown)
    text = demote_citation_headings(text)
    return text
