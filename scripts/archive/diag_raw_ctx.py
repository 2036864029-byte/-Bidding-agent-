# 功能：检查 raw 文件中关键文本跨页处的上下文。
import re
from pathlib import Path

RAW = Path(
    r"DATA/raw_data/handoff_v3_p100/raw/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).raw.md"
)
raw_lines = RAW.read_text(encoding="utf-8").split("\n")
for i, l in enumerate(raw_lines):
    if "个人利用职权进行前款违法行为" in l:
        for j in range(max(0, i - 2), min(len(raw_lines), i + 8)):
            mark = ">>" if j == i else "  "
            print(mark, repr(raw_lines[j][:70]))
        print("---")
