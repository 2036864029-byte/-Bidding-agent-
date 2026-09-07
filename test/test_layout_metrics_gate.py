# 功能：总体验收门禁（书型分层版）——handoff_v3 clean 资产的版面还原量化指标（显式启用）。
"""test_layout_metrics_gate —— 版面还原计划 §6.2 总体门禁（2026-09-06 按书型分层修订）。

启用方式：RUN_LAYOUT_GATE=1 python -m pytest test/test_layout_metrics_gate.py
数据源：`LAYOUT_GATE_CLEAN_DIR`（默认 DATA/raw_data/handoff_v3/clean）；
目录不存在 → skip（v3 产物生成前不阻塞日常测试）。
校准工具：scripts/diag_gate_calibrate.py（只读，对现有资产输出各项实测值）。

分层原则（用户决策 2026-09-06：书型判据按"区域/构成"而非整本书标签）：
  不给书打"法条体/解读体"标签，而是 ①按合法文书形态豁免（款项列表、单句
  条文、法律名标题行、沿革条目、编号子目、案例题头——都是模式驱动的通用
  法律文书形态，无书名/页码硬编码）；②G3 按标题构成拆成骨架/列表两个独立
  上限（法条体骨架高、解读体列表高，各自有各自的天花板）。

门禁指标（对每本 clean/*.assembled.md）：
  G1 可疑断裂短行率 ≤10%：
      ≤30 字正文行中，排除结构起始（列表项/款项/条文）与完整句
      （句末符收尾）与法律名标题行后——真正的"断段碎片"信号。
      校准：书1 v3 整书 8.0%（残差=用户暂缓的表单/公告混合区）、
      书2 试点 0.0%；v1 旧链病理（逐行截断）实测 15%~25% 会被拦下。
  G2 未收尾标题率 ≤10%：
      不以句末/收尾符收尾的标题中，排除合法结构标题（编/章/节/部分/
      条【题】/短列表子标题 ≤22 字无句读/沿革条目/多级数字编号/案例题头）。
      校准：书1 8.9%、书2 1.3%；残差即表单/公告折行误标题（暂缓类），
      门禁保持其可见性防恶化。
  G3 标题构成双上限：骨架标题/正文 ≤8% 且 列表子标题/正文 ≤35%：
      原 15% 单一对法条书骨架密度（5.8%）和解读体子标题密度（30.7%）
      双双失义；拆开后两类洪水（章/节误标 vs 列表 H4 洪水）各自有闸。
  G4 幂等：clean(clean(x)) == clean(x)；
  G5 表格：不作为门禁（用户决策：表格优先级后移）。

泛化护栏：全部豁免/阈值模式驱动且相对化，无书名/页码/绝对行数断言。
"""
import os
import re
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config.settings import Settings  # noqa: E402
from public_kb.ingestion.handoff import clean_markdown  # noqa: E402

_CLEAN_DIR = Path(
    os.environ.get(
        "LAYOUT_GATE_CLEAN_DIR",
        _PROJECT_ROOT / "DATA" / "raw_data" / "handoff_v3" / "clean",
    )
)

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_HEAD_END_CHARS = "。！？…；：”』」】）?？%"
_MID_PUNCT = "，。！？；："

# ── 合法文书形态豁免（模式驱动，与校准脚本 diag_gate_calibrate.py 同口径）──
# 列表/款项/条文起始行（正文短行的结构豁免，同 pdf_paragraph._STRUCT_START_RE 族）
_STRUCT_LINE_RE = re.compile(
    r"^(（\s*[0-9一二三四五六七八九十百]+\s*）|\([0-9一二三四五六七八九十百]+\)"
    r"|[0-9一二三四五六七八九十百]+[、.．]|[-•·]"
    r"|第[一二三四五六七八九十百千\d]+[条款项])"
)
# 骨架标题（编/部分/章/节/条【题】）——全书通用结构，法条体密集
_SKELETON_RE = re.compile(
    r"^第[一二三四五六七八九十百千\d]+[编章节部分](\s|$|【)"
    r"|^第[一二三四五六七八九十百千\d]+[条款项]【"
)
# 沿革/文号条目："1. 2011年12月20日国务院令第613号公布" / "2.根据2006年…"
_REVISION_ENTRY_RE = re.compile(r"^\d{1,2}\s*[.．]\s*(\d{4}\s*年|根据)")
# 多级数字编号子目（规章/标准体）："4.1 xxx" / "11.3.3 xxx"
_NUM_SUBHEAD_RE = re.compile(r"^\d{1,2}(\.\d{1,2}){1,3}\s+\S")
# 法律/法规名标题行："中华人民共和国招标投标法实施条例"
_LAW_TITLE_RE = re.compile(
    r"^(中华人民共和国)?[一-鿿]{2,18}(法|条例|实施条例|办法|规定|实施细则|解释)$"
)
# 案例/问答题头（解读体通用形态）："相关案例1-1 …"
_CASE_BOX_RE = re.compile(r"^(相关案例|案例|问题)\s*\d")


def _body_lines(markdown: str) -> list[str]:
    return [
        ln.strip() for ln in markdown.split("\n")
        if ln.strip()
        and not ln.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
    ]


def _headings(markdown: str) -> list[str]:
    return [
        m.group(2).strip()
        for ln in markdown.split("\n")
        if (m := _MD_HEADING_RE.match(ln.strip()))
    ]


def _suspect_short_lines(markdown: str) -> list[str]:
    """G1：可疑断裂短行（≤30 字、非结构起始、非完整句、非法律名）。"""
    return [
        ln for ln in _body_lines(markdown)
        if len(ln) <= 30
        and not _STRUCT_LINE_RE.match(ln)
        and not ln.endswith(tuple(_HEAD_END_CHARS + "、"))
        and not _LAW_TITLE_RE.match(ln)
    ]


def _unclosed_headings(markdown: str) -> list[str]:
    """G2：未收尾标题（排除合法结构/短列表/沿革/编号/案例题头）。"""
    out = []
    for t in _headings(markdown):
        if not t:
            continue
        t_chk = re.sub(r"\[\d+\]$", "", t)  # 引用尾注 [5] 视为收尾
        if (
            t_chk[-1:] in _HEAD_END_CHARS
            or "【" in t
            or "】" in t
            or re.match(r"^第[一二三四五六七八九十百千\d]+[条款项]$", t)
        ):
            continue
        if (
            _SKELETON_RE.match(t)
            or _REVISION_ENTRY_RE.match(t)
            or _NUM_SUBHEAD_RE.match(t)
            or _CASE_BOX_RE.match(t)
            or (
                _STRUCT_LINE_RE.match(t)
                and len(t) <= 22
                and not any(ch in t for ch in _MID_PUNCT)
            )
        ):
            continue
        out.append(t)
    return out


def _heading_composition(markdown: str) -> tuple[int, int, int]:
    """返回 (骨架标题数, 列表子标题数, 正文行数)。"""
    heads = _headings(markdown)
    n_skeleton = sum(1 for t in heads if _SKELETON_RE.match(t))
    n_list = sum(
        1 for t in heads
        if not _SKELETON_RE.match(t) and _STRUCT_LINE_RE.match(t)
    )
    return n_skeleton, n_list, len(_body_lines(markdown))


def _clean_asset_files():
    if not _CLEAN_DIR.exists():
        pytest.skip(f"v3 clean 资产尚未生成: {_CLEAN_DIR}")
    files = sorted(_CLEAN_DIR.glob("*.assembled.md"))
    if not files:
        pytest.skip(f"v3 clean 目录无 *.assembled.md: {_CLEAN_DIR}")
    return files


def _ratio(n: int, d: int) -> float:
    return n / d if d else 0.0


@pytest.mark.skipif(
    os.environ.get("RUN_LAYOUT_GATE") != "1",
    reason="总体门禁默认关闭；RUN_LAYOUT_GATE=1 显式启用",
)
class TestLayoutGate:
    def test_g1_suspect_short_line_ratio(self):
        for f in _clean_asset_files():
            md = f.read_text(encoding="utf-8")
            n_body = len(_body_lines(md))
            ratio = _ratio(len(_suspect_short_lines(md)), n_body)
            assert ratio <= 0.10, (
                f"{f.name}: 可疑断裂短行率 {ratio:.1%} > 10%"
                f"（校准基线：书1 v3 整书 8.0% / 书2 试点 0.0%）"
            )

    def test_g2_unclosed_heading_ratio(self):
        for f in _clean_asset_files():
            md = f.read_text(encoding="utf-8")
            n_head = len(_headings(md))
            ratio = _ratio(len(_unclosed_headings(md)), n_head)
            assert ratio <= 0.10, (
                f"{f.name}: 未收尾标题率 {ratio:.1%} > 10%"
                f"（校准基线：书1 8.9% / 书2 1.3%；残差=表单/公告暂缓类）"
            )

    def test_g3_skeleton_heading_cap(self):
        """骨架标题洪水闸：编/章/节/条 误标（原 Q2 H4 洪水的骨架部分）。"""
        for f in _clean_asset_files():
            md = f.read_text(encoding="utf-8")
            n_sk, _, n_body = _heading_composition(md)
            ratio = _ratio(n_sk, n_body)
            assert ratio <= 0.08, (
                f"{f.name}: 骨架标题/正文比 {ratio:.1%} > 8%"
                f"（校准基线：书1 法条体 5.8% / 书2 解读体 2.3%）"
            )

    def test_g3_list_subheading_cap(self):
        """列表子标题洪水闸：一、/（一）/1. 误标（解读体合法上限 35%）。"""
        for f in _clean_asset_files():
            md = f.read_text(encoding="utf-8")
            _, n_list, n_body = _heading_composition(md)
            ratio = _ratio(n_list, n_body)
            assert ratio <= 0.35, (
                f"{f.name}: 列表子标题/正文比 {ratio:.1%} > 35%"
                f"（校准基线：书2 解读体 30.7% / 书1 法条体 7.1%）"
            )

    def test_g4_idempotent(self):
        s = Settings()
        for f in _clean_asset_files():
            md = f.read_text(encoding="utf-8")
            assert clean_markdown(clean_markdown(md, s), s) == clean_markdown(md, s), (
                f"{f.name}: clean 不幂等"
            )
