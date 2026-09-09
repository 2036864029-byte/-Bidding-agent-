"""确定性子句拆分检索诊断（E5 决策备选③ 的证据）— 零 LLM 成本。

E5-3 用 deep_agent（LLM 规划）做分解，每次多花 3 次 LLM 调用。本诊断验证一个更便宜的
备选：**不调 LLM，只按标点/连词把问题切成子句，各自检索后取并集**——若召回增益接近
LLM 分解，则「分解下沉到共享检索层」可以完全绕开路由判定的准确率问题。

对照三组（同一批题、同一 top_k）：
  A 单查询基线      kb_search(原问题)
  B 子句拆分        kb_search(每个子句) → 并集
  C LLM 分解（可选）deep_agent 的 planner 拆子问题 → 并集（--with-llm）

用法：
    python scripts/diag_clause_split_recall.py                       # dev_100 的 35 条多跳
    python scripts/diag_clause_split_recall.py --dataset complex_20.jsonl --query-type complex
    python scripts/diag_clause_split_recall.py --with-llm            # 加跑 LLM 分解对照
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

DATA_DIR = ROOT / "DATA" / "eval_rag"
OUT_DIR = ROOT / "test_report"

# 子句切分：句末标点 + 并列连词（「以及/并且」是列举连词，切开会得到碎片，不切）
_SPLIT_RE = re.compile(r"[？?。；;]|同时|另外|此外|，并(?=说明|指出|分析)")
# 子句前的口语引导词
_LEAD_RE = re.compile(r"^(请问|那么|此时|其中)[，,]?\s*")
# 噪声子句（纯指令/修饰，检索价值低）
_NOISE_RE = re.compile(r"^(请|并)?(结合|根据|依据|按照|说明|指出|分析|阐述|回答|简要)")
_MIN_CLAUSE_CHARS = 8
_MAX_SUB_QUERIES = 4


def split_clauses(question: str) -> list[str]:
    """确定性切子句：句末标点/并列连词切分 → 去引导词/噪声 → 限长限量。"""
    parts = [p.strip(" ，,、") for p in _SPLIT_RE.split(question)]
    out: list[str] = []
    for p in parts:
        p = _LEAD_RE.sub("", p)
        if len(p) < _MIN_CLAUSE_CHARS:
            continue
        if _NOISE_RE.match(p):
            continue
        out.append(p)
        if len(out) >= _MAX_SUB_QUERIES:
            break
    return out


def _retrieve(queries: list[str], top_k: int) -> list[str]:
    """多查询检索 → 按 chunk_id 去重后返回正文列表（保持首次出现顺序）。"""
    from public_kb.tools import kb_search

    seen: set = set()
    texts: list[str] = []
    for q in queries:
        try:
            result = kb_search(q, top_k=top_k)
        except Exception as e:  # noqa: BLE001  单查询失败不中断
            print(f"    [warn] 检索失败: {e}")
            continue
        for c in result.chunks:
            key = c.chunk_id if c.chunk_id is not None else c.content[:80]
            if key in seen:
                continue
            seen.add(key)
            texts.append(c.content)
    return texts


def main() -> None:
    parser = argparse.ArgumentParser(description="确定性子句拆分检索诊断")
    parser.add_argument("--dataset", default="dev_100.jsonl")
    parser.add_argument("--query-type", default="multi_hop")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--with-llm", action="store_true",
                        help="加跑 LLM 分解（deep_agent planner）对照")
    args = parser.parse_args()

    from evals.datasets import distribution, load_jsonl
    from evals.metrics import evaluate_retrieval_text

    samples = [s for s in load_jsonl(DATA_DIR / args.dataset)
               if s.query_type == args.query_type]
    if args.limit > 0:
        samples = samples[: args.limit]
    print(f"金标集: {args.dataset}  {len(samples)} 条 {distribution(samples)}")

    llm = None
    if args.with_llm:
        from core.config.settings import Settings
        from core.llm.registry import create_llm_for_slot
        llm = create_llm_for_slot("deep_agent", Settings())

    def _one(sample) -> dict:
        clauses = split_clauses(sample.question)
        base = _retrieve([sample.question], args.top_k)
        split = _retrieve(clauses or [sample.question], args.top_k)
        llm_queries: list[str] = []
        if llm is not None:
            from agent.nodes.deep_agent import _plan_question
            plan = _plan_question(sample.question, llm, ["kb_search_tool"])
            llm_queries = [st.query for st in plan]
        llm_texts = _retrieve(llm_queries, args.top_k) if llm_queries else []
        return {"question": sample.question, "clauses": clauses,
                "llm_queries": llm_queries, "baseline": base,
                "split": split, "llm": llm_texts}

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(_one, samples))
    elapsed = time.monotonic() - started

    k = 10_000  # 并集口径：取全部返回块
    baseline = evaluate_retrieval_text(samples, {r["question"]: r["baseline"] for r in rows}, k=5)
    split = evaluate_retrieval_text(samples, {r["question"]: r["split"] for r in rows}, k=k)
    # 各组的 @k 后缀不同（baseline=top5 / 并集=全量），按指标名对齐
    metrics = {
        "baseline(单查询 top5)": {n.split("@")[0]: v for n, v in baseline.items()},
        "clause_split(并集)": {n.split("@")[0]: v for n, v in split.items()},
    }
    if args.with_llm:
        llm_metrics = evaluate_retrieval_text(
            samples, {r["question"]: r["llm"] for r in rows}, k=k)
        metrics["llm_plan(并集)"] = {n.split("@")[0]: v for n, v in llm_metrics.items()}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    detail = OUT_DIR / f"clause_split_{stamp}.jsonl"
    with detail.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n=== 分解方式对比（{args.dataset} / {args.query_type} / top_k={args.top_k}）===")
    names = list(next(iter(metrics.values())))
    print(f"  {'指标':<16}" + "".join(f"{n:>24}" for n in metrics))
    for name in names:
        print(f"  {name:<16}" + "".join(f"{metrics[m][name]:>24.4f}" for m in metrics))
    avg_clauses = sum(len(r["clauses"]) for r in rows) / len(rows)
    print(f"\n  平均子句数 {avg_clauses:.1f}（上限 {_MAX_SUB_QUERIES}）  耗时 {elapsed:.1f}s")
    print(f"明细: {detail}")


if __name__ == "__main__":
    main()
