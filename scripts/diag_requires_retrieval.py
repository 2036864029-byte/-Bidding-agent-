# 功能：校验「不可猜」跨源题（诊断）— 证明它真的需要检索、且 agent 看得到。
"""诊断脚本：校验 `file_kb_grounded` 类的两条前提（不参与生产）。

**为什么必须校验**：如果"法规侧关键事实"模型本来就知道（或文件里就有、或 agent
的观察摘要看不到），那么 A/B 两条路径都会答对 → 测不出"检索"的增量，
结论就成了"agent 无用"的假象（这正是首版 49 条集栽的跟头，见调研文档 §9.6）。

三条硬前提，逐条自动核对：
  ① **可检索**：`kb_search` 能召回含该 token 的块；
  ② **agent 看得到**：该 token 落在某召回块的**前 120 字**内
     （`_summarize_result` 给 kb_search 的观察预算是 120 字/块 —— 落在后面等于没给）；
  ③ **不可从文件猜**：该 token **不在** fixture 正文里
     （否则读文件就够，不需要检索）。

用法：
    python scripts/diag_requires_retrieval.py
    python scripts/diag_requires_retrieval.py --show-retrieved   # 打印实际召回的首块
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

GOLDEN = PROJECT_ROOT / "eval" / "file_golden_set.json"
FIXTURES = PROJECT_ROOT / "eval" / "fixtures"

#: agent 实际能看到的每块字符数（agent/nodes/deep_agent.py 的 kb_search 观察预算）
OBSERVATION_CHARS = 120


def main() -> None:
    ap = argparse.ArgumentParser(description="校验「不可猜」跨源题")
    ap.add_argument("--show-retrieved", action="store_true")
    args = ap.parse_args()

    from public_kb.tools import kb_search_as_tool
    from agent.nodes.deep_agent import _summarize_result

    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = [it for it in data["items"] if it.get("requires_retrieval")]
    print(f"file_kb_grounded 共 {len(cases)} 条\n")

    tool = kb_search_as_tool()
    bad = 0
    print(f"{'token':<22}{'①可检索':>8}{'②前120字':>10}{'③不在文件':>11}")
    print("-" * 54)
    for it in cases:
        tok = it["answer_contains_any"][0][0]
        out = tool.invoke({"question": it["q"], "top_k": 5, "summary": True})
        chunks = out.get("chunks") or []
        c1 = any(tok in (c.get("content") or "") for c in chunks)
        c2 = any(0 <= (c.get("content") or "").find(tok) < OBSERVATION_CHARS
                 for c in chunks)
        # ③ 文件里没有 → 必须靠检索
        blob = "".join((FIXTURES / f).read_text(encoding="utf-8")
                       for f in it.get("files") or [])
        c3 = tok not in blob
        # 顺带核 agent 实际看到的摘要里有没有（最终判据）
        seen = tok in _summarize_result(out, "kb_search_tool")
        mark = "" if (c1 and c2 and c3 and seen) else "  ← ❌ 需修"
        if mark:
            bad += 1
        print(f"{tok:<22}{str(c1):>8}{str(c2):>10}{str(c3):>11}{mark}")
        if args.show_retrieved:
            print(f"    Q: {it['q'][:70]}")
            print(f"    召回首块: {(chunks[0].get('content') if chunks else '')[:110]!r}")
            print(f"    agent 摘要含 token: {seen}")

    print(f"\n不合格 {bad} / {len(cases)}")
    if bad:
        print("❌ 不合格的条目在 A/B 里**测不出增量**（两条路径都会答对或都答不出）——"
              "换 token 或换种子条款，别留着污染结论。")
        sys.exit(1)
    print("✅ 全部通过：可检索 + agent 看得到 + 文件里没有（真的需要检索）")


if __name__ == "__main__":
    main()
