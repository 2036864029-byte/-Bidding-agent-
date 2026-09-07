# 功能：门禁分层校准（只读）——书1 v3 / 书2 试点在新口径下的 G1~G3 实测值。
"""diag_gate_calibrate —— 按"结构排除 + 按内容分层"新口径计算门禁指标：
  G1 短行：原始 vs 排除结构短行（列表项/款项/条文起始）后
  G2 未收尾标题：原始 vs 豁免合法结构标题（编/章/节/部分 + 短列表子标题）后
  G3 标题/正文比：按 结构标题/列表子标题 拆分
输出每本的实测数字，供分层阈值定档。
"""
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
# 结构/列表行起始（与 pdf_paragraph._STRUCT_START_RE 同族，门禁侧独立维护）
_LIST_ITEM_RE = re.compile(
    r"^(（\s*[0-9一二三四五六七八九十百]+\s*）|\([0-9一二三四五六七八九十百]+\)"
    r"|[0-9一二三四五六七八九十百]+[、.．]|[-•·])"
)
_SKELETON_RE = re.compile(
    r"^第[一二三四五六七八九十百千\d]+[编章节部分](\s|$|【)|"
    r"^第[一二三四五六七八九十百千\d]+[条款项]【"
)
_ARTICLE_BODY_RE = re.compile(r"^第[一二三四五六七八九十百千\d]+[条款项]")
_HEAD_END_CHARS = "。！？…；：”』」】）?？%"
_MID_PUNCT = "，。！？；："
# 沿革/文号条目（法规修订历史合法形态）："1. 2011年12月20日国务院令第613号公布"
_REVISION_ENTRY_RE = re.compile(r"^\d{1,2}\s*[.．]\s*(\d{4}\s*年|根据)")
# 法律/法规名标题行："中华人民共和国招标投标法实施条例"（法规汇编体合法形态）
_LAW_TITLE_RE = re.compile(r"^(中华人民共和国)?[一-鿿]{2,18}(法|条例|实施条例|办法|规定|实施细则|解释)$")
# 多级数字编号子目（规章标准体）："4.1 xxx" / "11.3.3 xxx"
_NUM_SUBHEAD_RE = re.compile(r"^\d{1,2}(\.\d{1,2}){1,3}\s+\S")


def _body_lines(md):
    return [
        ln.strip() for ln in md.split("\n")
        if ln.strip() and not ln.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
    ]


def _headings(md):
    return [
        m.group(2).strip() for ln in md.split("\n")
        if (m := _MD_HEADING_RE.match(ln.strip()))
    ]


def analyze(label, md):
    body = _body_lines(md)
    heads = _headings(md)
    n_body = len(body)

    # G1：短行，原始 vs 排除结构起始行 vs 再排除完整句（句末符收尾）
    short_all = [ln for ln in body if len(ln) <= 30]
    short_struct = [ln for ln in short_all if _LIST_ITEM_RE.match(ln) or _ARTICLE_BODY_RE.match(ln)]
    short_pure = [ln for ln in short_all if ln not in short_struct]
    short_suspect = [
        ln for ln in short_pure
        if not ln.endswith(tuple(_HEAD_END_CHARS + "、"))
        and not _LAW_TITLE_RE.match(ln)
    ]

    # G2：未收尾，原始 vs 豁免合法结构标题
    unclosed_raw, unclosed_pure = [], []
    for t in heads:
        if not t:
            continue
        t_chk = re.sub(r"\[\d+\]$", "", t)  # 引用尾注 [5] 视为收尾
        base_unclosed = (
            t_chk[-1:] not in _HEAD_END_CHARS and "【" not in t and "】" not in t
            and not re.match(r"^第[一二三四五六七八九十百千\d]+[条款项]$", t)
        )
        legit = (
            bool(_SKELETON_RE.match(t))
            or bool(_REVISION_ENTRY_RE.match(t))
            or bool(_NUM_SUBHEAD_RE.match(t))
            or (
                bool(_LIST_ITEM_RE.match(t))
                and len(t) <= 22
                and not any(ch in t for ch in _MID_PUNCT)
            )
        )
        if base_unclosed:
            unclosed_raw.append(t)
            if not legit:
                unclosed_pure.append(t)

    # G3：标题/正文比拆分
    n_skeleton = sum(1 for t in heads if _SKELETON_RE.match(t))
    n_list_head = sum(
        1 for t in heads if _LIST_ITEM_RE.match(t) and not _SKELETON_RE.match(t)
    )
    n_other = len(heads) - n_skeleton - n_list_head

    print(f"== {label} ==")
    print(f"  正文行 {n_body}  标题 {len(heads)}（骨架 {n_skeleton} / 列表子标题 {n_list_head} / 其他 {n_other}）")
    print(f"  G1 短行: 原始 {len(short_all)/n_body:.1%}  排除结构后 {len(short_pure)/n_body:.1%}"
          f"  可疑断裂(再豁法律名) {len(short_suspect)/n_body:.1%}")
    if len(short_suspect):
        for s in short_suspect[:6]:
            print(f"      可疑: {s[:36]}")
    print(f"  G2 未收尾: 原始 {len(unclosed_raw)/max(len(heads),1):.1%}  豁免后 {len(unclosed_pure)/max(len(heads),1):.1%}"
          f"  再豁沿革/编号 {sum(1 for t in unclosed_pure if not _REVISION_ENTRY_RE.match(t) and not _NUM_SUBHEAD_RE.match(t))/max(len(heads),1):.1%}")
    for s in unclosed_pure:
        if not _REVISION_ENTRY_RE.match(s) and not _NUM_SUBHEAD_RE.match(s):
            print(f"      残差: {s[:40]}")
    print(f"  G3 标题/正文: 总 {len(heads)/n_body:.1%}  骨架 {n_skeleton/n_body:.1%}  列表 {n_list_head/n_body:.1%}")


_V3 = _PROJECT_ROOT / "DATA" / "raw_data" / "handoff_v3" / "clean"
for f in sorted(_V3.glob("*.assembled.md")):
    analyze(f"书1 v3 整书: {f.name[:20]}", f.read_text(encoding="utf-8"))

_V2P = _PROJECT_ROOT / "DATA" / "raw_data" / "pilot_book2_v2" / "v2_layout.md"
if _V2P.exists():
    analyze("书2 试点 v2（前50页连续）", _V2P.read_text(encoding="utf-8"))
