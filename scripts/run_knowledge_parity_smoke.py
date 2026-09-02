"""本地 KB 对拍回归脚本（替代丢失的 testset_knowledge.jsonl 106 题，作为 L3 守卫）。

对同一本地 Milvus 集合，逐题比较 旧链路(rag.query) vs 新工具链路(chain.answer_knowledge)
的结果结构（citations/sources/validation 计数与一致性），并把结果快照写入
test_report/。任一处结构性不一致即退出码非 0 —— 用作 B-5 等行为重构的前后回归护栏。

用法（需本机 Milvus + 已入库的 public_kb 集合 + SiliconFlow/DeepSeek 可达）：
    python scripts/run_knowledge_parity_smoke.py
    # 覆盖 host/port/limit：
    python scripts/run_knowledge_parity_smoke.py --host 127.0.0.1 --port 19531 --limit 8 --tag before_b5

说明：这是"同 KB 结构对拍"，不是领域质量评测；正式质量评测待 testset_knowledge.jsonl
找回或重建后再补。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 默认指向本机 Milvus（可在运行前用环境变量覆盖）
os.environ.setdefault("MILVUS_HOST", "127.0.0.1")
os.environ.setdefault("MILVUS_PORT", "19531")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "test_report"

# 脚本独立运行时确保项目根可导入（与 scripts/ 下其他 eval 脚本一致）
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_QUESTIONS = [
    "招标方式有哪几种？",
    "评标委员会由哪些人组成？",
    "投标保证金有什么规定？",
    "哪些情形应当否决投标？",
    "中标通知书发出后应在多长时间内签订合同？",
    "政府采购的主要采购方式有哪些？",
    "评标专家应当具备什么条件？",
    "投标文件在什么情形下会被拒绝接收？",
    "招标人和中标人签订合同的依据是什么？",
    "对评标结果有异议如何处理？",
    "串通投标的认定标准是什么？",
    "招标代理机构的作用是什么？",
]


def _run_legacy(question: str, rag) -> dict:
    return rag.query(question)


def _run_tool(question: str) -> dict:
    from agent.nodes.knowledge_qa.chain import answer_knowledge

    return answer_knowledge(question)


def _shape(result: dict) -> dict:
    return {
        "answer_len": len(result.get("answer") or ""),
        "n_sources": len(result.get("sources") or []),
        "n_citations": len(result.get("citations") or []),
        "all_passed": (result.get("citation_validation") or {}).get("all_passed"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tag", default="local")
    ap.add_argument("--no-report", action="store_true", help="不写快照文件，只打印")
    args = ap.parse_args()
    if args.host:
        os.environ["MILVUS_HOST"] = args.host
    if args.port:
        os.environ["MILVUS_PORT"] = args.port

    questions = DEFAULT_QUESTIONS[: args.limit] if args.limit else DEFAULT_QUESTIONS

    from public_kb import PublicKnowledgeRAG

    rag = PublicKnowledgeRAG()
    loaded = rag.load_existing()
    if not loaded:
        print("x 本地未加载到 public_kb 集合，请先入库（见 README/handoff 导入）")
        return 2
    print(f"legacy RAG ready（rows 见 Milvus）；对拍 {len(questions)} 题...\n")

    results = []
    mismatch = 0
    for i, q in enumerate(questions, 1):
        print("=" * 66)
        print(f"Q{i}: {q}")
        row = {"question": q}
        try:
            leg = _run_legacy(q, rag)
            row["legacy"] = _shape(leg)
        except Exception as e:  # noqa: BLE001
            row["legacy"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        try:
            tool = _run_tool(q)
            row["tool"] = _shape(tool)
        except Exception as e:  # noqa: BLE001
            row["tool"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}

        same = (
            "error" not in row["legacy"]
            and "error" not in row["tool"]
            and row["legacy"]["n_sources"] == row["tool"]["n_sources"]
            and row["legacy"]["n_citations"] == row["tool"]["n_citations"]
            and row["legacy"]["all_passed"] == row["tool"]["all_passed"]
            and (row["legacy"].get("answer_len") or 0) > 0
            and (row["tool"].get("answer_len") or 0) > 0
        )
        row["consistent"] = bool(same)
        if not same:
            mismatch += 1
        print(f"  legacy: {row.get('legacy')}")
        print(f"  tool  : {row.get('tool')}")
        print(f"  → {'一致' if same else '不一致 !'}")
        results.append(row)

    summary = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "milvus": f"{os.environ.get('MILVUS_HOST')}:{os.environ.get('MILVUS_PORT')}",
        "n": len(results),
        "consistent": len(results) - mismatch,
        "mismatch": mismatch,
    }
    print("\n" + "=" * 66)
    print(f"汇总: {summary}")

    if not args.no_report:
        OUT_DIR.mkdir(exist_ok=True)
        tag = f"{args.tag}_{int(time.time())}"
        snap = OUT_DIR / f"knowledge_local_parity_{tag}.json"
        snap.write_text(
            json.dumps({"summary": summary, "items": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"快照: {snap.relative_to(PROJECT_ROOT)}")

    return 1 if mismatch else 0


if __name__ == "__main__":
    sys.exit(main())
