"""E5-3 端到端效果验证 — deep_agent 全链路 vs 单跳检索（段落召回）。

对多跳/复合问题跑 **真实 deep_agent 全链路**（plan → execute → synthesize → reflect，
DeepSeek + 真实 Milvus kb_search），并记录：

  - **段落召回**：把各步 kb_search 返回的块取并集，按正文口径算 recall@union /
    双段全中，与同一批问题的**单查询 top-5 基线**直接对比；
  - **分解行为**：plan 步数分布、每步 query、工具选择；
  - **成本**：LLM 调用次数（plan 1 + synthesize 1 + reflect 1）、检索次数、耗时。

⚠ 轨迹里的 observation 是**限长摘要**（400 字），不足以算召回；故本脚本用
**记录型 kb_search 工具**包一层，直接捕获每次检索的完整块（对 agent 行为零影响：
观察摘要本就只取前 120 字）。

用法：
    python scripts/run_deep_agent_recall_eval.py                     # dev_100 的 35 条多跳
    python scripts/run_deep_agent_recall_eval.py --dataset complex_20.jsonl
    python scripts/run_deep_agent_recall_eval.py --limit 10 --workers 4
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from core.config.settings import Settings  # noqa: E402
from core.llm.registry import create_llm_for_slot  # noqa: E402

DATA_DIR = ROOT / "DATA" / "eval_rag"
OUT_DIR = ROOT / "test_report"


class _CountingLLM:
    """LLM 代理：统计调用次数（E5-4 成本口径），其余行为原样透传。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0
        self._lock = threading.Lock()

    def _bump(self):
        with self._lock:
            self.calls += 1

    def invoke(self, *a, **kw):
        self._bump()
        return self._inner.invoke(*a, **kw)

    def stream(self, *a, **kw):
        self._bump()
        return self._inner.stream(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _recording_kb_tool(store: list, lock: threading.Lock):
    """构建 kb_search 工具：把每次检索的完整块正文追加到 store（线程安全）。

    ⚠ 强制 `summary=False`：deep_agent 的 `_TOOL_DEFAULTS` 传 summary=True（正文截断
    到 240 字，见 `public_kb/tools.py::_SUMMARY_MAX_CHARS`），截断后的正文不足以判
    段落命中。对 agent 行为无影响——观察摘要本就只取每块前 120 字。
    """
    from langchain_core.tools import tool

    from public_kb.tools import kb_search

    @tool
    def kb_search_tool(
        question: str,
        top_k: int = 5,
        offset: int = 0,
        summary: bool = False,
        include_fields: list | None = None,
    ) -> dict:
        """检索招投标公共知识库（法规/招标知识），返回三元组。"""
        result = kb_search(question, top_k=top_k, offset=offset,
                           summary=False, include_fields=include_fields)
        payload = result.model_dump(mode="json")
        with lock:
            store.extend(c.get("content", "") for c in payload.get("chunks", []))
        return payload

    return kb_search_tool


def _run_one(sample, llm_slot: str) -> dict:
    """跑一条：deep_agent 全链路（记录型 kb 工具）+ 单查询基线。"""
    from agent.nodes.deep_agent import run_deep_agent
    from price_engine.tools import price_query_as_tool
    from public_kb.tools import kb_search

    chunks: list[str] = []
    lock = threading.Lock()
    llm = _CountingLLM(create_llm_for_slot(llm_slot, Settings()))
    tools = [_recording_kb_tool(chunks, lock), price_query_as_tool()]

    started = time.monotonic()
    result = run_deep_agent(sample.question, llm=llm, tools=tools)
    latency_ms = int((time.monotonic() - started) * 1000)

    trace = result["trace"]
    plan = trace.get("plan", [])
    steps = trace.get("steps", [])

    baseline = kb_search(sample.question, top_k=5)
    baseline_texts = [c.content for c in baseline.chunks]

    with lock:
        agent_texts = list(chunks)

    return {
        "question": sample.question,
        "query_type": sample.query_type,
        "gold_texts": sample.ground_truth_text,
        "agent_texts": agent_texts,
        "baseline_texts": baseline_texts,
        "plan_steps": len(plan),
        "plan_queries": [s.get("query", "") for s in plan],
        "tools_used": [s.get("tool", "") for s in steps],
        "step_status": [s.get("status", "") for s in steps],
        "llm_calls": llm.calls,
        "latency_ms": latency_ms,
        "answer_chars": len(result["answer"]),
        "reflect_verdict": (trace.get("reflection") or {}).get("verdict", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="E5-3 deep_agent 端到端召回验证")
    parser.add_argument("--dataset", default="dev_100.jsonl")
    parser.add_argument("--query-type", default="multi_hop",
                        help="只跑该 query_type（complex 用 complex）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--llm-slot", default="deep_agent")
    args = parser.parse_args()

    from evals.datasets import distribution, load_jsonl
    from evals.metrics import evaluate_retrieval_text

    samples = [s for s in load_jsonl(DATA_DIR / args.dataset)
               if s.query_type == args.query_type]
    if args.limit > 0:
        samples = samples[: args.limit]
    print(f"金标集: {args.dataset}  {len(samples)} 条 {distribution(samples)}")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(lambda s: _run_one(s, args.llm_slot), samples))
    elapsed = time.monotonic() - started

    agent_texts = {r["question"]: r["agent_texts"] for r in rows}
    baseline_texts = {r["question"]: r["baseline_texts"] for r in rows}
    agent_metrics = evaluate_retrieval_text(samples, agent_texts, k=10_000)
    baseline_metrics = evaluate_retrieval_text(samples, baseline_texts, k=5)
    # 两套指标的 @k 后缀不同（agent=并集全量 / baseline=top5），按指标名对齐比较
    compare = {
        name.split("@")[0]: {
            "agent": agent_metrics[name],
            "baseline": baseline_metrics.get(f"{name.split('@')[0]}@5", 0.0),
        }
        for name in agent_metrics
    }

    step_dist = Counter(r["plan_steps"] for r in rows)
    llm_calls = sum(r["llm_calls"] for r in rows)
    report = {
        "dataset": args.dataset,
        "query_type": args.query_type,
        "samples": len(rows),
        "elapsed_seconds": round(elapsed, 1),
        "agent_metrics": agent_metrics,
        "baseline_metrics": baseline_metrics,
        "compare": compare,
        "plan_step_distribution": dict(sorted(step_dist.items())),
        "avg_plan_steps": round(sum(r["plan_steps"] for r in rows) / len(rows), 2),
        "avg_llm_calls": round(llm_calls / len(rows), 2),
        "avg_latency_ms": int(sum(r["latency_ms"] for r in rows) / len(rows)),
        "tool_distribution": dict(Counter(t for r in rows for t in r["tools_used"])),
        "reflect_verdicts": dict(Counter(r["reflect_verdict"] for r in rows)),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    detail_path = OUT_DIR / f"deep_agent_recall_{stamp}.jsonl"
    with detail_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    report_path = OUT_DIR / f"deep_agent_recall_{stamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("\n=== E5-3 段落召回：deep_agent 全链路 vs 单查询基线 ===")
    print("  （agent=各步检索块并集 / baseline=单查询 top-5）")
    for name, pair in compare.items():
        print(f"  {name:<22} agent={pair['agent']:.4f}   "
              f"baseline={pair['baseline']:.4f}   Δ={pair['agent'] - pair['baseline']:+.4f}")
    print(f"  平均 plan 步数         {report['avg_plan_steps']}  {report['plan_step_distribution']}")
    print(f"  平均 LLM 调用          {report['avg_llm_calls']}")
    print(f"  平均耗时               {report['avg_latency_ms']} ms")
    print(f"  工具分布               {report['tool_distribution']}")
    print(f"  反思结论               {report['reflect_verdicts']}")
    print(f"\n明细: {detail_path}")
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
