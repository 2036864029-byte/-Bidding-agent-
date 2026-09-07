# 功能：书1 结构性验收（只读，计划 §5）——法规名 H1 识别率 / 第一章零丢失 / 幻影标题=0。
"""diag_book1_v4_structural —— 书1 法规合集形态修复的验收指标（2026-09-07 计划 §5）。

输入：`DATA/raw_data/handoff_v3/clean/<书1>.assembled.md`（P1~P5 后的 v4 产物）。
可选 `--vs <备份目录>` 对照 v3 产物（backup_20260907_v3_book1）。

指标（计划 §5）：
  1. 法规名 → H1 识别率 ≥95%：`# ` 一级标题中"法规名形态"行（以 法/条例/办法/
     规定/细则/通知/纲要/规划… 收尾或 `中华人民共和国` 开头）占比；
  2. 每个法规开头的"第一章"标题零丢失：`## 第一章` 计数应 ≥ 法规数（~50）；
  3. mini 目录幻影标题 = 0：连续 ≥2 个 `## 第X章` 标题行（行间无正文）——P3
     剥离后不应再存在；另 v3 的 `<!-- inline-toc -->` 标记数应为 0；
  4. 章 = H2、条 = H4 分布正常（H1 法规名 / H2 章 / H4 条 三层骨架成立）；
  5. 幂等：clean_markdown(clean) == clean。
"""
import argparse
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from core.config.settings import Settings  # noqa: E402
from public_kb.ingestion.handoff import clean_markdown  # noqa: E402

_BOOK1 = ("中华人民共和国招标投标法律法规全书：含相关政策"
          " (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md")

_H1_RE = re.compile(r"^# (.*)$")
_H2_RE = re.compile(r"^## (.*)$")
_H4_RE = re.compile(r"^#### (.*)$")
_ANY_H_RE = re.compile(r"^(#{1,6})\s+(.+)$")

_LAW_TAIL_RE = re.compile(
    r"(法|条例|实施条例|办法|规定|细则|暂行规定|暂行办法|实施办法|实施意见"
    r"|通知|函|纲要|规划|规则|章程)$"
)
# 正文句段形态的 H1 误判信号（含句中逗号/句号/“引语”片段 → 不是法规名）
_MID_PUNCT_RE = re.compile(r"[，。；：、？]")


def is_law_name(text: str) -> bool:
    t = text.strip()
    if not (4 <= len(t) <= 40):
        return False
    return t.startswith("中华人民共和国") or bool(_LAW_TAIL_RE.search(t))


def phantom_blocks(lines) -> int:
    """连续 ≥2 个 `## 第X章/节` 标题行（行间无正文）的块数（幻影 mini 目录残留）。"""
    blocks = 0
    run = 0
    for ln in lines:
        s = ln.strip()
        m = _H2_RE.match(s)
        if m and re.match(r"^第[一二三四五六七八九十百千\d]+[章节]", m.group(1)):
            run += 1
        else:
            if run >= 2:
                blocks += 1
            run = 0
    if run >= 2:
        blocks += 1
    return blocks


def measure(md: str) -> dict:
    lines = md.split("\n")
    h1 = [_H1_RE.match(l).group(1).strip() for l in lines if _H1_RE.match(l)]
    h1_law = [t for t in h1 if is_law_name(t)]
    ch1 = sum(1 for l in lines if _H2_RE.match(l) and _H2_RE.match(l).group(1).startswith("第一章"))
    dist = {"h1": len(h1), "h2": 0, "h3": 0, "h4": 0, "h5": 0, "h6": 0}
    for l in lines:
        m = _ANY_H_RE.match(l)
        if m:
            dist[f"h{len(m.group(1))}"] += 1
    return {
        "h1": h1, "h1_law": h1_law,
        "h1_law_ratio": len(h1_law) / len(h1) if h1 else 0.0,
        "h1_fp": [t for t in h1 if _MID_PUNCT_RE.search(t)],
        "ch1": ch1,
        "phantom_blocks": phantom_blocks(lines),
        "inline_toc_marks": sum(1 for l in lines if "inline-toc" in l),
        "dist": dist,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vs", default="", help="v3 备份目录（对比输出）")
    args = ap.parse_args()

    clean_dir = _PROJECT_ROOT / "DATA" / "raw_data" / "handoff_v3" / "clean"
    p = clean_dir / _BOOK1
    if not p.exists():
        print(f"✗ 未找到 v4 clean: {p}")
        return 1
    md = p.read_text(encoding="utf-8")
    m = measure(md)

    print("===== 书1 v4 结构性验收（计划 §5）=====")
    print(f"法规名 H1 识别: {len(m['h1_law'])}/{len(m['h1'])} "
          f"({m['h1_law_ratio']:.1%})  [目标 ≥95%]")
    for t in m["h1"]:
        mark = "✓" if is_law_name(t) else "⚠"
        print(f"  {mark} # {t[:44]}")
    print(f"'第一章'计数: {m['ch1']}  [目标 ≥~50 = 法规数]")
    print(f"幻影 mini 目录块（连续 ≥2 个 ## 第X章）: {m['phantom_blocks']}  [目标 0]")
    print(f"inline-toc 标记残留: {m['inline_toc_marks']}  [目标 0]")
    print(f"H1 误判（含句中逗号/句号，正文句段形态）: {len(m['h1_fp'])}")
    for t in m["h1_fp"]:
        print(f"    ✗ # {t[:44]}")
    print(f"标题分布: H1={m['dist']['h1']} H2={m['dist']['h2']} "
          f"H3={m['dist']['h3']} H4={m['dist']['h4']} H5={m['dist']['h5']} H6={m['dist']['h6']}")
    ok = (
        m["h1_law_ratio"] >= 0.95
        and m["ch1"] >= 30
        and m["phantom_blocks"] == 0
        and m["inline_toc_marks"] == 0
    )
    s = Settings()
    print(f"幂等: {'✓' if clean_markdown(clean_markdown(md, s), s) == clean_markdown(md, s) else '✗'}")

    if args.vs:
        vp = Path(args.vs) / _BOOK1
        if vp.exists():
            v = measure(vp.read_text(encoding="utf-8"))
            print(f"\n── v3 对照（{args.vs}）──")
            print(f"  法规名 H1: {len(v['h1_law'])}/{len(v['h1'])}  "
                  f"(v4: {len(m['h1_law'])}/{len(m['h1'])})")
            print(f"  '第一章'计数: {v['ch1']} → v4 {m['ch1']}  [+{m['ch1']-v['ch1']}]")
            print(f"  幻影块: {v['phantom_blocks']} → v4 {m['phantom_blocks']}")
            print(f"  inline-toc 标记: {v['inline_toc_marks']} → v4 {m['inline_toc_marks']}")

    print("\n== 验收:", "通过 ✓" if ok else "未通过 ✗（需人工核验上方指标）", "==")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
