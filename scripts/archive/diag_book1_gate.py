# 功能：handoff_v3 book1 门禁诊断——G1 短行构成 / G2 未收尾标题构成（临时脚本）。
import re
from collections import Counter
from pathlib import Path

md = Path(
    r"DATA/raw_data/handoff_v3/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
).read_text(encoding="utf-8")
lines = md.split("\n")
HRE = re.compile(r"^(#{1,6})\s+(.+)$")
body = [
    l.strip() for l in lines
    if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
]
heads = [m.group(2).strip() for l in lines if (m := HRE.match(l.strip()))]
short = [l for l in body if len(l) <= 30]
print("body", len(body), "short", len(short), f"ratio {len(short)/len(body):.1%}")
print("heads", len(heads))
print("head level dist", dict(Counter(len(m.group(1)) for l in lines if (m := HRE.match(l.strip())))))
print("--- shortest 30 body lines ---")
for l in sorted(short, key=len)[:30]:
    print(repr(l))
print("--- sample headings (first 30) ---")
for h in heads[:30]:
    print(repr(h))
ENDC = "。！？…；：”』」】）?？%"
unc = [
    h for h in heads
    if h and h[-1] not in ENDC and "【" not in h and "】" not in h
    and not re.match(r"^第[一二三四五六七八九十百千\d]+[条款项]$", h)
]
print("unclosed", len(unc), f"ratio {len(unc)/len(heads):.1%}")
pat = Counter()
for h in unc:
    if re.match(r"^第[一二三四五六七八九十百千\d]+[条款项]", h):
        pat["第X条..."] += 1
    elif re.match(r"^(（[0-9一二三四五六七八九十百]+）|[0-9一二三四五六七八九十百]+、|[0-9]{1,3}[.．])", h):
        pat["list head"] += 1
    elif re.match(r"^第[一二三四五六七八九十百千\d]+[编章节]", h):
        pat["第X章/节/编"] += 1
    else:
        pat["other"] += 1
print("unclosed composition", dict(pat))
print("--- unclosed '第X条...' samples ---")
for h in [h for h in unc if re.match(r"^第[一二三四五六七八九十百千\d]+[条款项]", h)][:25]:
    print(repr(h))
print("--- unclosed list-head samples ---")
for h in [h for h in unc if re.match(r"^(（[0-9一二三四五六七八九十百]+）|[0-9一二三四五六七八九十百]+、)", h)][:15]:
    print(repr(h))
print("--- unclosed other samples ---")
for h in [h for h in unc if not re.match(r"^(（[0-9一二三四五六七八九十百]+）|[0-9一二三四五六七八九十百]+、|第)", h)][:15]:
    print(repr(h))
