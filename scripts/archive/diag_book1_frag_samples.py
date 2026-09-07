# 功能：p100 真断裂样本清单（豁免列表项/沿革/目录后）。
import re
from pathlib import Path

CLEAN = Path(
    r"DATA/raw_data/handoff_v3_p100/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
lines = CLEAN.read_text(encoding="utf-8").split("\n")
body = [
    l.strip() for l in lines
    if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
]
LIST_RE = re.compile(r"^(（[0-9一二三四五六七八九十百]+）|\([0-9一二三四五六七八九十百]+\)|[0-9一二三四五六七八九十百]+[、.]|\d{1,3}[.．])")
AMEND_RE = re.compile(r"^\d{1,2}[.．]?\s*(19|20)\d{2}年|次会议|常委会|修订|修正|公布|废止")
END = "。！？…；："

def is_frag(s):
    if not s or s.endswith(tuple(END)) or s.endswith(("）", ")", "》")):
        return False
    if LIST_RE.match(s) or AMEND_RE.search(s):
        return False
    return True

frags = [s for s in body if is_frag(s)]
print(f"true fragments={len(frags)}")
for s in frags[:40]:
    print(repr(s))
