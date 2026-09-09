"""原子性判定「上限」诊断（E5-1 前置）— 判定与路由解耦。

E5-2 的命中率受两个因素影响：① 判据本身好不好；② 路由提示里 6 个能力互相竞争。
本脚本剥掉 ②：只给 LLM **一个二分类问题**（这题是否多原子？），量出判据在
金标 single/multi 标注上的**上限**——即"只用问题文本判断，最多能到多少"。

用途：判据改写时先在这里快速迭代（每次 60 次调用，~10s），定稿后再回
`run_router_intent_eval.py --gold` 走真实路由。

用法:
    python scripts/diag_atomicity_ceiling.py                 # 30 single + 30 multi
    python scripts/diag_atomicity_ceiling.py --per-type 50 --seed 7
    python scripts/diag_atomicity_ceiling.py --show-errors   # 打印判错的题
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from core.config.settings import Settings  # noqa: E402
from core.llm.registry import create_llm_for_slot  # noqa: E402

DATA_DIR = ROOT / "DATA" / "eval_rag"

SYSTEM_PROMPT_V1 = """你是招投标问答系统的**问题复杂度判别器**。判断用户问题是否属于
「多原子问题」——即需要**分别查证两个及以上不同的事项**、再综合回答。

【多原子（multi_atomic=true）】
问题包含两个及以上**各自独立的事项**：不同法规/不同文件/不同制度/不同环节/不同情形，
每个事项各自需要独立依据。典型信号：
- 明确要求结合多份资料或多条规定（"结合资料1与资料2"、"结合两条规定"）；
- 并列多个不同事项并要求分别说明（"分别说明A与B"，且 A、B 属不同事项）；
- "其一…其二…"、"（1）（2）（3）"且各问分属不同事项；
- 条件分叉到另一情形（"若…又该如何"、"此时…同时…"）。

【单原子（multi_atomic=false）】
问题只围绕**一个事项/一个制度/一个流程**展开，答案通常出自同一处规定。即使问题
有多个问句、篇幅较长，只要问句都在追同一件事的不同细节，即为单原子。典型信号：
- 同一流程的时间、条件、材料、后果（如"多久公示？异议何时提出？如何处理？"）；
- 同一文件的细节（如"何时废止？施行日期是哪天？"）；
- 同一制度的构成要素（如"应当遵循哪些原则？这些原则分别有何含义？"）。

只输出 JSON：{"multi_atomic": true/false, "reason": "不超过20字的理由"}"""

SYSTEM_PROMPT_V2 = """判断下面这个问题，答案是否**分散在两份不同的资料/文件/章节**里
（而不是同一份资料里的同一节）。

【true】问题需要把**两个不同来源**的规定拼在一起才能回答。典型信号：
- 问题点明两条不同的规定/两部不同的法规/两份资料（"结合资料1与资料2"、"结合两条规定"、
  "根据《A办法》和《B条例》"）；
- 问题同时问两件**分属不同制度**的事（如既问"该企业能不能受让"又问"该适用哪部规定"）；
- 一句话里用"同时/另外/此外/分别/又该如何"并列了**两个不同事项**。

【false】问题虽然可能有多个问句、篇幅也长，但都在追问**同一件事**的不同细节，
答案通常能在同一节规定里找齐。典型信号：
- 同一流程的时间、条件、材料、后果（"多久公示？异议何时提出？如何处理？"）；
- 同一文件的细节（"何时废止？施行日期是哪天？"）；
- 同一制度的构成要素（"应当遵循哪些原则？各自含义是什么？"）；
- 同一情形下的不同侧面（"佣金怎么收？未成交又怎么收？"——同一节即含两种情形）。

只输出 JSON：{"multi_atomic": true/false, "reason": "不超过20字的理由"}"""

SYSTEM_PROMPT_V3 = """你是招投标问答系统的**多跳问题判别器**。判断用户问题是否需要
**先拆成两个及以上子问题、分别检索后再综合**（多跳=true）。

经验规律（来自人工标注的 1000 条真实问题）：
- 多跳问题**通常较长**（中位 120 字以上），且常出现这些词：**同时、分别、另外、此外、
  并说明、并指出**，以及"结合资料/结合两条规定/结合相关条文"；
- 但**只要问句都在追问同一件事**（同一流程的时间/条件/材料/后果，同一文件的细节，
  同一制度的构成要素），即使很长、有多个问号，也**不是**多跳；
- 只有**两个及以上各自独立的事项**（不同法规/不同制度/不同环节/不同情形）才判多跳。

只输出 JSON：{"multi_atomic": true/false, "reason": "不超过20字的理由"}"""

VARIANTS = {"v1": SYSTEM_PROMPT_V1, "v2": SYSTEM_PROMPT_V2, "v3": SYSTEM_PROMPT_V3}


def _parse(text: str) -> bool | None:
    """从模型输出里抠出 multi_atomic 布尔值（容忍前后缀说明）。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return bool(json.loads(m.group(0)).get("multi_atomic"))
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="原子性判定上限诊断")
    parser.add_argument("--per-type", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--show-errors", action="store_true")
    parser.add_argument("--variant", default="v1", choices=sorted(VARIANTS),
                        help="判据文案变体（用于比较不同框架的上限）")
    parser.add_argument("--all-variants", action="store_true",
                        help="跑全部变体并对比（同一批样本）")
    args = parser.parse_args()

    import random

    from evals.datasets import load_jsonl

    samples = load_jsonl(DATA_DIR / "testset_1000_local.jsonl")
    rng = random.Random(args.seed)
    picked = []
    for qt in ("single_hop", "multi_hop"):
        pool = [s for s in samples if s.query_type == qt]
        picked.extend(rng.sample(pool, min(args.per_type, len(pool))))

    llm = create_llm_for_slot("router", Settings())

    def _judge_one(sample, system_prompt: str) -> dict:
        resp = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=sample.question),
        ])
        verdict = _parse(str(resp.content))
        return {
            "query_type": sample.query_type,
            "pred": verdict,
            "text": str(resp.content)[:80],
            "question": sample.question,
        }

    def _score(rows: list[dict], title: str) -> None:
        tp = sum(1 for r in rows if r["query_type"] == "multi_hop" and r["pred"] is True)
        fn = sum(1 for r in rows if r["query_type"] == "multi_hop" and r["pred"] is not True)
        fp = sum(1 for r in rows if r["query_type"] == "single_hop" and r["pred"] is True)
        tn = sum(1 for r in rows if r["query_type"] == "single_hop" and r["pred"] is not True)
        print(f"=== 原子性判定上限 · {title} ===")
        print(f"  multi 命中率   {tp / (tp + fn):.4f}  ({tp}/{tp + fn})")
        print(f"  single 误判率  {fp / (fp + tn):.4f}  ({fp}/{fp + tn})")
        print(f"  整体准确率     {(tp + tn) / len(rows):.4f}  ({tp + tn}/{len(rows)})")
        unparsed = sum(1 for r in rows if r["pred"] is None)
        if unparsed:
            print(f"  解析失败       {unparsed}")

    variants = sorted(VARIANTS) if args.all_variants else [args.variant]
    last_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name in variants:
            rows = list(pool.map(lambda s: _judge_one(s, VARIANTS[name]), picked))
            _score(rows, name)
            last_rows = rows

    if args.show_errors:
        for r in last_rows:
            if (r["query_type"] == "multi_hop") != (r["pred"] is True):
                tag = "漏(multi)" if r["query_type"] == "multi_hop" else "误(single)"
                print(f"\n[{tag}] {r['question'][:180]}\n     → {r['text']}")


if __name__ == "__main__":
    main()
