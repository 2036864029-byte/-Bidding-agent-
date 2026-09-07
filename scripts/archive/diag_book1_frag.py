# 功能：pilot 断裂率精测——正文行不以句末标点收尾（真段落断裂）的占比与样本。
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
body = [
    l.strip() for l in lines
    if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
]
END = "。！？…；："
# 结构豁免：列表项/表单残留/表格/题注类
def is_fragment(s: str) -> bool:
    if not s:
        return False
    if s.endswith(tuple(END)):
        return False
    # 不以句末收尾且非封闭形态 → 可疑断裂
    if re.match(r"^(（[0-9一二三四五六七八九十百]+）|\([0-9一二三四五六七八九十百]+\))", s):
        return s[-1] not in END + "）)"  # 列表项通常句末收尾
    return True

frags = [l for l in body if is_fragment(l)]
print(f"body={len(body)} fragments={len(frags)} ratio={len(frags)/len(body):.1%}")
lens = Counter()
for l in frags:
    b = "≤10" if len(l) <= 10 else "11-20" if len(l) <= 20 else "21-30" if len(l) <= 30 else ">30"
    lens[b] += 1
print("fragment len buckets:", dict(lens))
print("--- fragment samples (25) ---")
for l in frags[:25]:
    print(repr(l))
