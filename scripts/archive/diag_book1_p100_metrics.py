# 功能：p100 pilot 综合指标——短行占比/构成、标题占比、断裂率（修订沿革豁免后）。
import re
from collections import Counter
from pathlib import Path

CLEAN = Path(
    r"DATA/raw_data/handoff_v3_p100/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
md = CLEAN.read_text(encoding="utf-8")
lines = md.split("\n")
HRE = re.compile(r"^(#{1,6})\s+(.+)$")
body_idx = [
    i for i, l in enumerate(lines)
    if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
]
body = [lines[i].strip() for i in body_idx]
heads = [HRE.match(l.strip()).group(2) for l in lines if HRE.match(l.strip())]
print(f"body={len(body)} headings={len(heads)} head/body={len(heads)/len(body):.1%}")

shorts = [s for s in body if len(s) <= 30]
print(f"short<=30: {len(shorts)} ratio={len(shorts)/len(body):.1%}")

# 短行构成分类
LIST_RE = re.compile(r"^(（[0-9一二三四五六七八九十百]+）|\([0-9一二三四五六七八九十百]+\)|[0-9一二三四五六七八九十百]+[、.]|\d{1,3}[.．])")
AMEND_RE = re.compile(r"^\d{1,2}[.．]?\s*(19|20)\d{2}年|次会议|常委会|修订|修正|公布|废止")
TOC_RE = re.compile(r"目\s*录")
def classify(s):
    if LIST_RE.match(s):
        return "list_item"
    if AMEND_RE.search(s):
        return "amend_history"
    if TOC_RE.match(s):
        return "toc"
    return "other"

kinds = Counter(classify(s) for s in shorts)
print("short composition:", dict(kinds))
print("--- other samples (20) ---")
for s in [s for s in shorts if classify(s) == "other"][:20]:
    print(repr(s))

# 真断裂（不以句末收尾且非列表/沿革/目录类）
END = "。！？…；："
frags = [
    s for s in body
    if not s.endswith(tuple(END))
    and classify(s) not in ("list_item", "amend_history", "toc")
    and not s.endswith(("）", ")", "》"))
]
print(f"\ntrue fragments={len(frags)} ratio={len(frags)/len(body):.1%}")
