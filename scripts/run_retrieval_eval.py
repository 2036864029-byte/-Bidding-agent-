"""检索层确定性指标评测（E2-1）— 零 LLM 成本。

对金标集逐题跑 `kb_search`（真实 Milvus + reranker），产出**两套口径**：

  - **正文口径**（主）：检索块正文是否覆盖金标段落——金标集的行级 `chunk_id`
    相对重建后的集合已过期（见 evals/metrics/retrieval.py 模块说明），故以正文
    匹配为准；语义上即"是否召回了能作答的段落"。
  - **ID 口径**（辅）：`ground_truth_chunk_ids` 与 `chunk_id` 直接比对——待 E1
    完成金标 id 重映射后才应恢复为权威口径。

两套都写入报告，便于观察 id 过期程度与后续重映射的收敛。

用法：
    python scripts/run_retrieval_eval.py                    # dev_100，top-5
    python scripts/run_retrieval_eval.py --limit 20         # 冒烟
    python scripts/run_retrieval_eval.py --dataset holdout_200.jsonl --top-k 10
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.datasets import distribution, load_jsonl  # noqa: E402
from evals.metrics import (  # noqa: E402
    evaluate_retrieval,
    evaluate_retrieval_text,
    missing_gold_text,
)

DATA_DIR = PROJECT_ROOT / "DATA" / "eval_rag"
OUT_DIR = PROJECT_ROOT / "test_report"


def _retrieve(question: str, top_k: int) -> tuple[list[int], list[str]]:
    from public_kb.tools import kb_search

    result = kb_search(question, top_k=top_k)
    ids = [c.chunk_id for c in result.chunks if c.chunk_id is not None]
    contents = [c.content for c in result.chunks]
    return ids, contents


def main() -> None:
    parser = argparse.ArgumentParser(description="检索层确定性指标评测")
    parser.add_argument("--dataset", default="dev_100.jsonl", help="金标集文件名")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    args = parser.parse_args()

    dataset_path = DATA_DIR / args.dataset
    samples = load_jsonl(dataset_path)
    if args.limit > 0:
        samples = samples[: args.limit]
    print(f"金标集: {dataset_path.name}  {len(samples)} 条 {distribution(samples)}")

    retrieved: dict[str, list[int]] = {}
    retrieved_texts: dict[str, list[str]] = {}
    per_sample: list[dict] = []
    started = time.monotonic()
    for i, s in enumerate(samples, 1):
        try:
            ids, contents = _retrieve(s.question, args.top_k)
        except Exception as e:  # noqa: BLE001  单题失败不中断整批
            ids, contents = [], []
            print(f"[warn] 第 {i} 题检索失败: {e}")
        retrieved[s.question] = ids
        retrieved_texts[s.question] = contents
        per_sample.append(
            {
                "question": s.question,
                "query_type": s.query_type,
                "ground_truth_chunk_ids": s.ground_truth_chunk_ids,
                "retrieved_chunk_ids": ids,
            }
        )
        if i % 10 == 0:
            print(f"  ...{i}/{len(samples)}")
    elapsed = time.monotonic() - started

    text_metrics = evaluate_retrieval_text(samples, retrieved_texts, k=args.top_k)
    id_metrics = evaluate_retrieval(samples, retrieved, k=args.top_k)
    misses = missing_gold_text(samples, retrieved_texts, k=args.top_k)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    detail_path = OUT_DIR / f"retrieval_eval_{stamp}.jsonl"
    with detail_path.open("w", encoding="utf-8") as fh:
        for row in per_sample:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "dataset": dataset_path.name,
        "samples": len(samples),
        "top_k": args.top_k,
        "elapsed_seconds": round(elapsed, 1),
        "text_metrics": text_metrics,
        "id_metrics": id_metrics,
        "miss_count": len(misses),
        "miss_examples": [q for q, _ in misses[:10]],
        "note": "正文口径为主；ID 口径待 E1 金标 id 重映射后恢复为权威",
    }
    report_path = OUT_DIR / f"retrieval_eval_{stamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("\n=== 检索层指标（确定性，零 LLM）===")
    print("  [正文口径 · 主]")
    for name, value in text_metrics.items():
        print(f"    {name:<22} {value:.4f}")
    print("  [ID 口径 · 辅，金标 id 已过期]")
    for name, value in id_metrics.items():
        print(f"    {name:<22} {value:.4f}")
    print(f"  未召回样本            {len(misses)}/{len(samples)}")
    print(f"  耗时                  {elapsed:.1f}s")
    print(f"\n明细: {detail_path}")
    print(f"报告: {report_path}")


if __name__ == "__main__":
    main()
