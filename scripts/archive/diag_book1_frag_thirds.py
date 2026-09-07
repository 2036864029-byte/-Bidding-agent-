# 功能：全书断裂样本分层抽样（按行号前/中/后三段）。
import re
from pathlib import Path

CLEAN = Path(
    r"DATA/raw_data/handoff_v3/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
lines = CLEAN.read_text(encoding="utf-8").split("\n")
body = [(i, l.strip()) for i, l in enumerate(lines)
        if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))]
LIST_RE = re.compile(r"^(（[0-9一二三四五六七八九十百]+）|\([0-9一二三四五六七八九十百]+\)|[0-9一二三四五六七八九十百]+[、.]|\d{1,3}[.．])")
AMEND_RE = re.compile(r"^\d{1,2}[.．]?\s*(19|20)\d{2}年|次会议|常委会|修订|修正|公布|废止")
END = "。！？…；："

def is_frag(s):
    if s.endswith(tuple(END)) or s.endswith(("）", ")", "》")):
        return False
    if LIST_RE.match(s) or AMEND_RE.search(s):
        return False
    return True

frags = [(i, s) for i, s in body if is_frag(s)]
n = len(body)
thirds = [frags[: len(frags) // 3], frags[len(frags) // 3: 2 * len(frags) // 3], frags[2 * len(frags) // 3:]]
for t, part in enumerate(thirds):
    print(f"=== third {t + 1} ({len(part)} frags) samples:")
    step = max(1, len(part) // 12)
    for i, s in part[::step][:12]:
        print(f"  L{i}", repr(s[:60]))
