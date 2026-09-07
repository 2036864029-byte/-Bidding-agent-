# 功能：book1 前100页试点诊断——G1 短行构成 / G2 标题构成 / 串栏抽检。
import re
from collections import Counter
from pathlib import Path

md = Path(
    r"DATA/raw_data/handoff_v3_p100/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
).read_text(encoding="utf-8")
lines = md.split("\n")
HRE = re.compile(r"^(#{1,6})\s+(.+)$")
body = [
    (i, l.strip()) for i, l in enumerate(lines)
    if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
]
heads = [m.group(2).strip() for l in lines if (m := HRE.match(l.strip()))]
print("body", len(body), "heads", len(heads), f"ratio {len(heads)/len(body):.1%}")

short = [(i, s) for i, s in body if len(s) <= 30]
print("short", len(short), f"ratio {len(short)/len(body):.1%}")
buckets = Counter()
for _, s in short:
    n = len(s)
    b = "1" if n == 1 else "2-10" if n <= 10 else "11-20" if n <= 20 else "21-30"
    buckets[b] += 1
print("short buckets:", dict(buckets))

# 短行模式分类（非标题正文）
def classify(s: str) -> str:
    if re.match(r"^(（[0-9一二三四五六七八九十百]+）|\([0-9一二三四五六七八九十百]+\)|[0-9一二三四五六七八九十百]+[、.)．]|[-•·])", s):
        return "list item"
    if re.match(r"^第[一二三四五六七八九十百千\d]+[条款项]", s):
        return "article ref"
    if re.match(r"^第[一二三四五六七八九十百千\d]+[编章节]", s):
        return "chapter/section"
    if re.search(r"[。！？；]$", s):
        return "sentence-end"
    if re.match(r"^[0-9]{1,4}$", s.strip()):
        return "page num"
    return "other"

cls = Counter(classify(s) for _, s in short)
print("short composition:", dict(cls.most_common()))
print("\n--- 'other' short line samples (20) ---")
for i, s in [x for x in short if classify(x[1]) == "other"][:20]:
    print(repr(s))
print("\n--- list-item samples (10) ---")
for i, s in [x for x in short if classify(x[1]) == "list item"][:10]:
    print(repr(s))

# 单字行上下文
print("\n=== single/2-char line contexts ===")
shown = 0
for i, s in short:
    if len(s) <= 2 and shown < 5:
        print(f"--- line {i} ---")
        for j in range(max(0, i - 2), min(len(lines), i + 2)):
            mark = ">>" if j == i else "  "
            print(mark, repr(lines[j][:60]))
        shown += 1

# 标题构成
ENDC = "。！？…；：”』」】）?？%"
unc = [h for h in heads if h and h[-1] not in ENDC and "【" not in h and "】" not in h
       and not re.match(r"^第[一二三四五六七八九十百千\d]+[条款项]$", h)]
hc = Counter()
for h in unc:
    if re.match(r"^第[一二三四五六七八九十百千\d]+[编章节部分]", h):
        hc["chapter/节"] += 1
    elif re.match(r"^(（[0-9一二三四五六七八九十百]+）|[0-9一二三四五六七八九十百]+、)", h):
        hc["list head"] += 1
    else:
        hc["other"] += 1
print("\nheadings total", len(heads), "unclosed", len(unc), dict(hc))
print("--- chapter headings (first 15) ---")
for h in [h for h in unc if re.match(r"^第[一二三四五六七八九十百千\d]+[编章节部分]", h)][:15]:
    print(repr(h))
print("--- level dist:", dict(Counter(len(m.group(1)) for l in lines if (m := HRE.match(l.strip())))))
