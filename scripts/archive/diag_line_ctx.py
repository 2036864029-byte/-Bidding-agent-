# 功能：检查指定 clean 行的上下文（判断是否真断裂）。
import sys
from pathlib import Path

CLEAN = Path(
    r"DATA/raw_data/handoff_v3_p100/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
targets = sys.argv[1:] or ["个人利用职权进行前款违法行为的，依照前款规定追究责任"]
lines = CLEAN.read_text(encoding="utf-8").split("\n")
for t in targets:
    hit = False
    for i, l in enumerate(lines):
        if l.strip() == t:
            hit = True
            print(f"=== line {i}")
            for j in range(max(0, i - 1), min(len(lines), i + 3)):
                mark = ">>" if j == i else "  "
                print(mark, repr(lines[j][:60]))
            break
    if not hit:
        print(f"=== not found: {t!r}")
