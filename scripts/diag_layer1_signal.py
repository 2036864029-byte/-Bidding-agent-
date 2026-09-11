"""Layer 1 触发信号选型诊断（S2，零 LLM 优先）— dev_100 实测。

**要回答的问题**：路线 B 的「证据不足 → 按需补检」用什么触发器？

候选信号（零 LLM）：
  A 子句-区域散布：切子句，各子句 top-1 命中区域 ≥2 → 触发
  B 父块 doc 多样性：基线 top-11 区域 ≥2 且存在区域 ≤2 块 → 触发

**补检动作统一** = 子句检索池（按 score 降序）追加到基线 top-11 之后取 top-20，
基线原序保持（→ MRR 不摊薄，区别于 diag_clause_split 的无序并集）。

对照：基线 top-11 / 纯放大主查询 top-20 / A 触发补检 / B 触发补检 / A∨B 触发补检。
指标用正文口径（金标行级 id 过期，`evaluate_retrieval_text`）。

用法：
    python scripts/diag_layer1_signal.py                      # dev_100 全量
    python scripts/diag_layer1_signal.py --limit 40           # 快跑前 40 条
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "DATA" / "eval_rag"
OUT_DIR = ROOT / "test_report"

from evals.datasets import load_jsonl
from evals.metrics import evaluate_retrieval_text

SUPPLEMENT_K = 20   # 补检后的评估窗口
BASE_K = 11         # 基线窗口（= Layer 0 后的生产默认）


def _split_clauses(q: str, min_len: int) -> list[str]:
    """按标点切子句，过滤过短片段（纯连接词/介词短语检索无意义）。"""
    parts = [p.strip() for p in re.split(r"[？?。；;，,\n]", q)]
    return [p for p in parts if len(p) >= min_len]


def _region(chunk) -> str:
    return f"{chunk.doc_name}|{chunk.chapter}"


def _dedupe_by_content(pool):
    """按正文去重，保持 score 降序。"""
    seen: set = set()
    out = []
    for score, content in pool:
        if content in seen:
            continue
        seen.add(content)
        out.append((score, content))
    return out


def _supplement(base_top11, clause_pool, k: int) -> list[str]:
    """基线 top-11 原序 + 子句池新块（score 降序），截到 k。MRR 不摊薄。"""
    out = list(base_top11)
    seen = set(out)
    for _, content in clause_pool:
        if len(out) >= k:
            break
        if content not in seen:
            seen.add(content)
            out.append(content)
    return out[:k]


def main() -> None:
    parser = argparse.ArgumentParser(description="Layer 1 触发信号选型诊断")
    parser.add_argument("--dataset", default="dev_100.jsonl")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-clause-len", type=int, default=8,
                        help="信号 A：子句最短长度")
    parser.add_argument("--b-min-regions", type=int, default=2,
                        help="信号 B：基线区域数下限")
    parser.add_argument("--b-min-block-in-region", type=int, default=2,
                        help="信号 B：存在某区域块数 ≤ 此值即触发")
    args = parser.parse_args()

    from public_kb.tools import kb_search

    samples = load_jsonl(DATA_DIR / args.dataset)
    if args.limit > 0:
        samples = samples[: args.limit]
    n = len(samples)
    print(f"金标集: {args.dataset}  {n} 条 "
          f"(multi={sum(s.is_multi_hop for s in samples)}, "
          f"single={sum(not s.is_multi_hop for s in samples)})")
    print(f"信号参数: min_clause_len={args.min_clause_len} "
          f"B_regions>={args.b_min_regions} B_small_region<={args.b_min_block_in_region}")

    started = time.monotonic()

    base_texts: dict[str, list[str]] = {}
    big_texts: dict[str, list[str]] = {}
    clause_pool: dict[str, list[tuple[float, str]]] = {}
    sig: dict[str, dict[str, bool]] = {}
    a_clause_count: dict[str, int] = {}

    for i, s in enumerate(samples, 1):
        r11 = kb_search(s.question, top_k=BASE_K)
        base_texts[s.question] = [c.content for c in r11.chunks]

        r20 = kb_search(s.question, top_k=SUPPLEMENT_K)
        big_texts[s.question] = [c.content for c in r20.chunks]

        # 子句检索（信号 A 检测 + 补检池共用）
        clauses = _split_clauses(s.question, args.min_clause_len)
        a_clause_count[s.question] = len(clauses)
        pool: list[tuple[float, str]] = []
        regions_a: set[str] = set()
        for cl in clauses:
            rc = kb_search(cl, top_k=5)
            if not rc.chunks:
                continue
            regions_a.add(_region(rc.chunks[0]))
            for c in rc.chunks:
                pool.append((c.score, c.content))
        pool.sort(reverse=True)
        clause_pool[s.question] = _dedupe_by_content(pool)

        sig_a = len(regions_a) >= 2
        base_regions = Counter(_region(c) for c in r11.chunks)
        sig_b = (
            len(base_regions) >= args.b_min_regions
            and any(cnt <= args.b_min_block_in_region for cnt in base_regions.values())
        )
        sig[s.question] = {"A": sig_a, "B": sig_b, "AorB": sig_a or sig_b}
        if i % 20 == 0:
            print(f"  ...{i}/{n} ({time.monotonic() - started:.0f}s)")

    elapsed_base = time.monotonic() - started

    # ── 补检模拟 ────────────────────────────────────────────
    def texts_for(mode: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for s in samples:
            q = s.question
            if mode == "base":
                out[q] = base_texts[q][:BASE_K]
            elif mode == "big":
                out[q] = big_texts[q][:SUPPLEMENT_K]
            else:
                if sig[q].get(mode, False):
                    out[q] = _supplement(base_texts[q], clause_pool[q], SUPPLEMENT_K)
                else:
                    out[q] = base_texts[q][:BASE_K]
        return out

    modes = {
        "base": (f"基线 top-{BASE_K}", BASE_K),
        "big": (f"纯放大 top-{SUPPLEMENT_K}", SUPPLEMENT_K),
        "A": ("A 触发补检", SUPPLEMENT_K),
        "B": ("B 触发补检", SUPPLEMENT_K),
        "AorB": ("A∨B 触发补检", SUPPLEMENT_K),
    }

    print(f"\n=== 触发率（判别力）===")
    print(f"  {'信号':<8}{'multi 触发':>14}{'single 触发(误伤)':>20}")
    for m in ("A", "B", "AorB"):
        multi_trig = sum(1 for s in samples if s.is_multi_hop and sig[s.question][m])
        single_trig = sum(1 for s in samples if not s.is_multi_hop and sig[s.question][m])
        multi_n = sum(1 for s in samples if s.is_multi_hop)
        single_n = sum(1 for s in samples if not s.is_multi_hop)
        mrate = f"{multi_trig / multi_n:.1%}" if multi_n else "-"
        srate = f"{single_trig / single_n:.1%}" if single_n else "-"
        print(f"  {m:<8}{mrate:>12}{srate:>18}   ({multi_trig}/{multi_n}, {single_trig}/{single_n})")

    # 触发样本 vs 未触发样本的基线缺口（验证信号与「缺块」相关）
    print(f"\n=== 触发样本的基线缺口（multi 上触发与否 vs 双段全中）===")
    for m in ("A", "B", "AorB"):
        trig = [s for s in samples if s.is_multi_hop and sig[s.question][m]]
        notrig = [s for s in samples if s.is_multi_hop and not sig[s.question][m]]
        if trig and notrig:
            t_full = evaluate_retrieval_text(trig, base_texts, k=BASE_K)["full_recall@11"]
            n_full = evaluate_retrieval_text(notrig, base_texts, k=BASE_K)["full_recall@11"]
            print(f"  {m:<8}触发组基线双段全中 {t_full:.3f}  vs  未触发组 {n_full:.3f}"
                  f"   (触发组 n={len(trig)}, 未触发 n={len(notrig)})")

    print(f"\n=== 各方案指标（正文口径，multi）===")
    multi = [s for s in samples if s.is_multi_hop]
    print(f"  {'方案':<22}{'recall':>9}{'双段全中':>10}{'MRR':>9}")
    for m, (label, k) in modes.items():
        texts = texts_for(m)
        ev = evaluate_retrieval_text(multi, texts, k=k)
        print(f"  {label:<22}{ev[f'recall@{k}']:>9.4f}{ev[f'full_recall@{k}']:>10.4f}"
              f"{ev[f'mrr@{k}']:>9.4f}")

    # 补检带来的新块是否命中金标（验证补检取到的是新内容）
    print(f"\n=== 补检新块命中分析（A∨B 触发样本）===")
    new_hits = new_total = 0
    for s in samples:
        if not sig[s.question]["AorB"]:
            continue
        base_set = set(base_texts[s.question])
        golds = s.ground_truth_text
        if not golds:
            continue
        for _, content in clause_pool[s.question]:
            if content in base_set:
                continue
            new_total += 1
            if any(g.replace(" ", "") in content.replace(" ", "")
                   or content.replace(" ", "") in g.replace(" ", "")
                   for g in golds):
                new_hits += 1
    print(f"  补检新块 {new_total} 条，其中命中金标 {new_hits} 条"
          f" ({new_hits / new_total:.1%})" if new_total else "  无触发样本")

    elapsed = time.monotonic() - started
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    detail = OUT_DIR / f"layer1_signal_{stamp}.jsonl"
    with detail.open("w", encoding="utf-8") as fh:
        for s in samples:
            q = s.question
            fh.write(json.dumps({
                "question": q,
                "query_type": s.query_type,
                "clauses": _split_clauses(q, args.min_clause_len),
                "signals": {k: v for k, v in sig[q].items() if k != "AorB"},
                "base_top11": base_texts[q],
                "big_top20": big_texts[q][:SUPPLEMENT_K],
                "supplemented": _supplement(base_texts[q], clause_pool[q], SUPPLEMENT_K)
                if sig[q]["AorB"] else base_texts[q][:BASE_K],
            }, ensure_ascii=False) + "\n")

    print(f"\n耗时 {elapsed:.1f}s（检索阶段 {elapsed_base:.1f}s）   明细: {detail}")


if __name__ == "__main__":
    main()
