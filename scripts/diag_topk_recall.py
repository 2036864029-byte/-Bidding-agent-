"""top_k 上限对照诊断（E5 决策备选①的证据）— 零 LLM 成本。

**要回答的问题**：分解（deep_agent / 子句拆分）带来的召回增益，是不是仅仅来自
「一次检索给了更多块」？如果是，那就不需要路由、不需要分解——把 `retrieval_top_k`
调大即可。

**为什么之前没答上**：§9「多跳归因实验 1」记的是「top_k 5→10→20 结果一字不变」，
但那是**调用侧** `kb_search(top_k=...)` 的参数；检索器内部还有一道硬上限
`Settings.retrieval_top_k`（默认 5，`retriever.py` 按它截断）——所以那次实验其实
被内部上限吃掉了，结论不成立。本脚本直接改**内部上限**再测。

用法：
    python scripts/diag_topk_recall.py                      # dev_100 的 35 条多跳，top5 vs top11
    python scripts/diag_topk_recall.py --top-k 20
    python scripts/diag_topk_recall.py --dataset complex_20.jsonl --query-type complex
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "DATA" / "eval_rag"
OUT_DIR = ROOT / "test_report"


def _measure(samples, top_k: int) -> dict:
    """用当前 retriever 单例逐题检索，返回 {question: [块正文]}。"""
    from public_kb.tools import kb_search

    out: dict[str, list[str]] = {}
    for s in samples:
        try:
            result = kb_search(s.question, top_k=top_k)
            out[s.question] = [c.content for c in result.chunks]
        except Exception as e:  # noqa: BLE001
            print(f"    [warn] 检索失败: {e}")
            out[s.question] = []
    return out


def _with_internal_top_k(top_k: int):
    """上下文管理器：把 `_build_retriever` 内部的 Settings 换成 retrieval_top_k=top_k。"""
    import contextlib

    import core.config.settings as cfg
    import public_kb.tools as tools

    @contextlib.contextmanager
    def _ctx():
        original = cfg.Settings
        settings = original(retrieval_top_k=top_k)
        cfg.Settings = lambda *a, **kw: settings  # 仅在重建 retriever 期间生效
        tools.reset_retriever()
        try:
            yield settings
        finally:
            cfg.Settings = original
            tools.reset_retriever()

    return _ctx()


def main() -> None:
    parser = argparse.ArgumentParser(description="检索内部 top_k 上限对照")
    parser.add_argument("--dataset", default="dev_100.jsonl")
    parser.add_argument("--query-type", default="multi_hop")
    parser.add_argument("--top-k", type=int, default=11, help="对照组的内部上限")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    from evals.datasets import distribution, load_jsonl
    from evals.metrics import evaluate_retrieval_text

    samples = [s for s in load_jsonl(DATA_DIR / args.dataset)
               if s.query_type == args.query_type]
    if args.limit > 0:
        samples = samples[: args.limit]
    print(f"金标集: {args.dataset}  {len(samples)} 条 {distribution(samples)}")

    from public_kb.tools import reset_retriever

    started = time.monotonic()
    reset_retriever()
    base_texts = _measure(samples, top_k=5)
    base_metrics = evaluate_retrieval_text(samples, base_texts, k=5)
    base_avg_chunks = sum(len(v) for v in base_texts.values()) / len(samples)

    with _with_internal_top_k(args.top_k):
        big_texts = _measure(samples, top_k=args.top_k)
    big_metrics = evaluate_retrieval_text(samples, big_texts, k=10_000)
    big_avg_chunks = sum(len(v) for v in big_texts.values()) / len(samples)
    elapsed = time.monotonic() - started

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    detail = OUT_DIR / f"topk_compare_{stamp}.jsonl"
    with detail.open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps({
                "question": s.question,
                f"top5": base_texts.get(s.question, []),
                f"top{args.top_k}": big_texts.get(s.question, []),
            }, ensure_ascii=False) + "\n")

    print(f"\n=== 检索内部上限对照（{args.dataset} / {args.query_type}）===")
    print(f"  {'指标':<22}{'内部top5':>14}{'内部top' + str(args.top_k):>14}")
    for name in base_metrics:
        family = name.split("@")[0]
        big = big_metrics.get(f"{family}@10000", 0.0)  # 后缀不同，按指标名对齐
        print(f"  {family:<22}{base_metrics[name]:>14.4f}{big:>14.4f}")
    print(f"  平均块数              {base_avg_chunks:>14.2f}{big_avg_chunks:>14.2f}")
    print(f"  耗时 {elapsed:.1f}s   明细: {detail}")


if __name__ == "__main__":
    main()
