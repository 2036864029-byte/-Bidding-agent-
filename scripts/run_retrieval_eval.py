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
from concurrent.futures import ThreadPoolExecutor
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


def _retrieve_expanded(question: str) -> list[str]:
    """Layer 1 统一轻量补检（kb_search_expanded，基线 top-11 + 每子句 top-5，max 20 块）。"""
    from public_kb.tools import kb_search_expanded

    return [c.content for c in kb_search_expanded(question).chunks]


# 门禁阈值（§6 评测门禁，holdout 验证口径）
def run_layer_comparison(samples, top_k: int, workers: int = 8) -> None:
    """L0（kb_search top-k）vs L1（kb_search_expanded）正文口径对比 + 门禁判定。

    检索是纯 I/O（远程 embedding + rerank），用线程池并发（workers）。
    """
    from evals.datasets import MULTI_HOP, SINGLE_HOP

    multi = [s for s in samples if s.query_type == MULTI_HOP]
    single = [s for s in samples if s.query_type == SINGLE_HOP]
    l0_texts: dict[str, list[str]] = {}
    l1_texts: dict[str, list[str]] = {}
    started = time.monotonic()

    def _one(s):
        q = s.question
        try:
            l0 = _retrieve(q, top_k)[1]
        except Exception as e:  # noqa: BLE001
            l0 = []
            print(f"[warn] L0 检索失败: {e}")
        try:
            l1 = _retrieve_expanded(q)
        except Exception as e:  # noqa: BLE001
            l1 = []
            print(f"[warn] L1 检索失败: {e}")
        return q, l0, l1

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, (q, l0, l1) in enumerate(pool.map(_one, samples), 1):
            l0_texts[q], l1_texts[q] = l0, l1
            if i % 20 == 0 or i == len(samples):
                print(f"  ...{i}/{len(samples)} "
                      f"({time.monotonic()-started:.0f}s)", flush=True)
    elapsed = time.monotonic() - started

    def _m(subset, texts, k):
        """指标名去掉 @k 后缀（L0/L1 窗口不同，键须归一化才能对比）。"""
        if not subset:
            return {}
        return {name.split("@")[0]: v
                for name, v in evaluate_retrieval_text(subset, texts, k=k).items()}

    l0_all, l1_all = _m(samples, l0_texts, top_k), _m(samples, l1_texts, 20)
    l0_multi, l1_multi = _m(multi, l0_texts, top_k), _m(multi, l1_texts, 20)
    l0_single, l1_single = _m(single, l0_texts, top_k), _m(single, l1_texts, 20)

    print(f"\n=== L0(top-{top_k}) vs L1(补检~17.7块) 正文口径 ===")
    print(f"样本 {len(samples)}（multi={len(multi)} single={len(single)}），耗时 {elapsed:.0f}s\n")
    for label, l0, l1 in (("全量", l0_all, l1_all), ("multi", l0_multi, l1_multi),
                          ("single", l0_single, l1_single)):
        if not l0:
            continue
        print(f"[{label}] {'指标':<18}{'L0':>9}{'L1':>9}{'Δ':>10}")
        for name in sorted(l0):
            v0 = l0.get(name, float("nan"))
            v1 = l1.get(name, float("nan"))
            print(f"      {name:<18}{v0:>9.4f}{v1:>9.4f}{v1-v0:>+10.4f}")
        print()
    print("=== 门禁（multi 口径）===")
    # 绝对目标按 dev_100 基线定（§6）；holdout 分布不同（L0 基线即低于 0.84），
    # 故 MRR 用「L1 不降于 L0」的相对判据，而非绝对阈值。

    def _v(d, k):
        x = d.get(k)
        return float("nan") if x is None else x

    gates = [
        ("multi 双段全中", _v(l1_multi, "multi_hop_recall"), 0.60, "abs"),
        ("multi recall", _v(l1_multi, "recall"), 0.78, "abs"),
        ("multi MRR 不降于 L0", _v(l1_multi, "mrr"), _v(l0_multi, "mrr"), "nondegrade"),
    ]
    gate_out = {}
    for name, val, thr, kind in gates:
        ok = bool(val == val and thr == thr and val >= thr)
        gate_out[name] = {
            "l1": None if val != val else round(val, 4),
            "target": None if thr != thr else round(thr, 4),
            "kind": kind, "passed": ok,
        }
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<20} L1={val:.4f}  基准={thr:.4f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"retrieval_layer_gate_{stamp}.json"
    out.write_text(json.dumps({
        "dataset": "holdout", "samples": len(samples), "top_k": top_k,
        "elapsed_seconds": round(elapsed, 1),
        "l0": {"all": l0_all, "multi": l0_multi, "single": l0_single},
        "l1": {"all": l1_all, "multi": l1_multi, "single": l1_single},
        "gates": gate_out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="检索层确定性指标评测")
    parser.add_argument("--dataset", default="dev_100.jsonl", help="金标集文件名")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    parser.add_argument("--compare-layers", action="store_true",
                        help="L0(kb_search) vs L1(kb_search_expanded) 对比 + 门禁判定")
    parser.add_argument("--workers", type=int, default=8,
                        help="--compare-layers 的检索并发（纯 I/O，默认 8）")
    args = parser.parse_args()

    dataset_path = DATA_DIR / args.dataset
    samples = load_jsonl(dataset_path)
    if args.limit > 0:
        samples = samples[: args.limit]
    print(f"金标集: {dataset_path.name}  {len(samples)} 条 {distribution(samples)}")

    if args.compare_layers:
        run_layer_comparison(samples, args.top_k, workers=args.workers)
        return

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
        "note": "正文口径为主（容忍父子块粒度）；ID 口径已由 E1-5 重映射恢复（2026-09-11），两者收敛",
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
