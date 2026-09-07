# 功能：诊断 pilot 中跨页/跨栏未合并的切断段。
import re
from pathlib import Path

CLEAN = Path(
    r"DATA/raw_data/handoff_v3_p100/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
RAW = Path(
    r"DATA/raw_data/handoff_v3_p100/raw/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).raw.md"
)
lines = CLEAN.read_text(encoding="utf-8").split("\n")

targets = [
    "评标委员会经评审，认为所有投标都不符合招标文件要求的，可以否",
    "招标文件要求中标人提交履约保证金的，中标",
    "审计人员依法执行职务，受法",
    "在确定中标人前，招标人不得与投标",
]
for t in targets:
    for i, l in enumerate(lines):
        if l.strip() == t:
            print(f"=== clean line {i}: {t!r}")
            for j in range(i - 1, min(len(lines), i + 4)):
                mark = ">>" if j == i else "  "
                print(mark, repr(lines[j][:50]))
            break

# raw 对应段（含页标记）
raw_lines = RAW.read_text(encoding="utf-8").split("\n")
for t in targets:
    for i, l in enumerate(raw_lines):
        if l.strip() == t:
            print(f"=== raw line {i}: {t!r}")
            for j in range(max(0, i - 2), min(len(raw_lines), i + 5)):
                mark = ">>" if j == i else "  "
                print(mark, repr(raw_lines[j][:50]))
            break
