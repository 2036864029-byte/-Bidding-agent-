# 功能：检查全书 clean 指定行号上下文。用法: python diag_clean_line_ctx.py 2682 6725
import sys
from pathlib import Path

CLEAN = Path(
    r"DATA/raw_data/handoff_v3/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
RAW = Path(
    r"DATA/raw_data/handoff_v3/raw/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).raw.md"
)
lines = CLEAN.read_text(encoding="utf-8").split("\n")
raw_lines = RAW.read_text(encoding="utf-8").split("\n")
for arg in sys.argv[1:]:
    i = int(arg)
    print(f"=== clean L{i}")
    for j in range(max(0, i - 2), min(len(lines), i + 4)):
        mark = ">>" if j == i else "  "
        print(mark, repr(lines[j][:64]))
    # 在 raw 中找该行，看跨页上下文
    tgt = lines[i].strip()
    for k, l in enumerate(raw_lines):
        if l.strip() == tgt:
            print(f"    --- raw L{k} ---")
            for j in range(max(0, k - 3), min(len(raw_lines), k + 5)):
                mark = ">>" if j == k else "  "
                print(mark, repr(raw_lines[j][:64]))
            break
    print()
