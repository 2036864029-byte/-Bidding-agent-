# 功能：全书断裂残差分类——表格/表单/标题类 vs 真正文切断。
import re
from pathlib import Path

CLEAN = Path(
    r"DATA/raw_data/handoff_v3/clean/"
    "中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).assembled.md"
)
lines = CLEAN.read_text(encoding="utf-8").split("\n")
body_idx = [(i, l.strip()) for i, l in enumerate(lines)
            if l.strip() and not l.lstrip().startswith(("#", "<", "|", "!", "-", ">", "*"))]
LIST_RE = re.compile(r"^(（[0-9一二三四五六七八九十百]+）|\([0-9一二三四五六七八九十百]+\)|[0-9一二三四五六七八九十百]+[、.]|\d{1,3}[.．])")
AMEND_RE = re.compile(r"^\d{1,2}[.．]?\s*(19|20)\d{2}年|次会议|常委会|修订|修正|公布|废止")
END = "。！？…；："

body = [s for _, s in body_idx]

def is_frag(s):
    if s.endswith(tuple(END)) or s.endswith(("）", ")", "》")):
        return False
    if LIST_RE.match(s) or AMEND_RE.search(s):
        return False
    return True

frags = [(i, s) for i, s in body_idx if is_frag(s)]

def neighbor_len(idx, k=1):
    """同文件相邻正文行的平均长度（上下文都是短行 → 表格/表单区）。"""
    pos = next(j for j, (i, _) in enumerate(body_idx) if i == idx)
    lo = max(0, pos - k)
    hi = min(len(body_idx), pos + k + 1)
    ns = [s for j, (i, s) in enumerate(body_idx) if lo <= j < hi and j != pos]
    return (sum(len(s) for s in ns) / len(ns)) if ns else 99

form, table, title_like, genuine = [], [], [], []
for i, s in frags:
    if "_" in s or "＿" in s or "：" == s[-1:] or re.search(r"[月日时][/_]?$", s):
        form.append((i, s))
    elif neighbor_len(i) <= 16:
        table.append((i, s))
    elif re.match(r"^(第[一二三四五六七八九十百千\d]+[条款]|附件|附表|关于|《)", s) or len(s) <= 8:
        title_like.append((i, s))
    else:
        genuine.append((i, s))

n = len(body)
print(f"body={n} frags={len(frags)} ({len(frags)/n:.1%})")
for name, part in (("form", form), ("table-context", table), ("title-like", title_like), ("genuine-cut", genuine)):
    print(f"  {name}: {len(part)} ({len(part)/n:.1%})")
print("\n--- genuine-cut samples (25) ---")
for i, s in genuine[:25]:
    print(f"  L{i}", repr(s[:60]))
