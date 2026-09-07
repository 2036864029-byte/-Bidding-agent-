# 功能：全书 clean 综合指标（短行占比/构成/真断裂率）。用法: python diag_book1_full_metrics.py [clean_path]
import re
import sys
from collections import Counter
from pathlib import Path

CLEAN = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"DATA/raw_data/handoff_v3/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
lines = CLEAN.read_text(encoding="utf-8").split("\n")
body = [
    l.strip() for l in lines
    if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
]
heads = [l.strip() for l in lines if re.match(r"^#{1,6}\s+", l.strip())]
print(f"file={CLEAN}")
print(f"body={len(body)} headings={len(heads)} head/body={len(heads)/len(body):.1%}")

LIST_RE = re.compile(r"^(（[0-9一二三四五六七八九十百]+）|\([0-9一二三四五六七八九十百]+\)|[0-9一二三四五六七八九十百]+[、.]|\d{1,3}[.．])")
AMEND_RE = re.compile(r"^\d{1,2}[.．]?\s*(19|20)\d{2}年|次会议|常委会|修订|修正|公布|废止")

def classify(s):
    if LIST_RE.match(s):
        return "list_item"
    if AMEND_RE.search(s):
        return "amend_history"
    return "other"

shorts = [s for s in body if len(s) <= 30]
kinds = Counter(classify(s) for s in shorts)
print(f"short<=30: {len(shorts)} ratio={len(shorts)/len(body):.1%} composition={dict(kinds)}")

END = "。！？…；："
frags = [
    s for s in body
    if not s.endswith(tuple(END))
    and classify(s) == "other"
    and not s.endswith(("）", ")", "》"))
]
print(f"true fragments={len(frags)} ratio={len(frags)/len(body):.1%}")
for s in frags[:15]:
    print(" ", repr(s))
