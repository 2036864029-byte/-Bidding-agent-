# 功能：book1 v3 深诊断——短行上下文 / 截断标题上下文。
import re
from collections import Counter
from pathlib import Path

CLEAN = Path(
    r"DATA/raw_data/handoff_v3/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
md = CLEAN.read_text(encoding="utf-8")
lines = md.split("\n")

# ── 1. 短行长度分布 ──
HRE = re.compile(r"^(#{1,6})\s+(.+)$")
body_idx = [
    i for i, l in enumerate(lines)
    if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))
]
shorts = [(i, lines[i].strip()) for i in body_idx if len(lines[i].strip()) <= 30]
dist = Counter(len(s) for _, s in shorts)
print("short len dist:", dict(sorted(dist.items())))

# ── 2. 单字行上下文（前2后2行）──
print("\n=== single-char line contexts ===")
shown = 0
for i, s in shorts:
    if len(s) == 1 and shown < 6:
        print(f"--- around line {i} ---")
        for j in range(max(0, i - 3), min(len(lines), i + 3)):
            mark = ">>" if j == i else "  "
            print(mark, repr(lines[j][:60]))
        shown += 1

# ── 3. ≤5 字行上下文采样 ──
print("\n=== short (2-5 char) line contexts ===")
shown = 0
for i, s in shorts:
    if 2 <= len(s) <= 5 and shown < 6:
        print(f"--- around line {i} ---")
        for j in range(max(0, i - 3), min(len(lines), i + 3)):
            mark = ">>" if j == i else "  "
            print(mark, repr(lines[j][:60]))
        shown += 1

# ── 4. 30 字内短行的整体构成（按长度桶）──
buckets = Counter()
for _, s in shorts:
    n = len(s)
    b = "1" if n == 1 else "2-10" if n <= 10 else "11-20" if n <= 20 else "21-30"
    buckets[b] += 1
print("\nshort buckets:", dict(buckets))

# ── 5. 截断 list-head 标题上下文 ──
print("\n=== truncated list-head heading context ===")
shown = 0
for i, l in enumerate(lines):
    m = HRE.match(l.strip())
    if m and re.match(r"^(（[0-9一二三四五六七八九十百]+）|[0-9一二三四五六七八九十百]+、)", m.group(2)) and shown < 3 and len(m.group(2)) > 15:
        print(f"--- heading at line {i}: {m.group(2)!r}")
        for j in range(i + 1, min(len(lines), i + 4)):
            print("   next:", repr(lines[j][:60]))
        shown += 1
