"""构建「复杂/长/模糊」问题集（多跳修复前置 · 步骤 1）。

**动机**：现有金标最长 249 字、无 ≥300 字样本，而真实用户会出现 600 字左右的复合
模糊提问。单跳链路把整段问题当作一个查询向量，长问题会被多主题稀释（多跳题单查询
召回 0.700 vs 分解 0.850）。要验证「复杂度触发 deep_agent」这类改造，先得有这样
一批样本。

**做法**：以现有多跳金标（每条约 2 段金标正文）为种子，让 LLM **反推**真实用户口吻
的复合长问题——这样答案必然在库里、召回可量化，且与既有金标同源可比。

产物：`DATA/eval_rag/complex_<N>.jsonl`（query_type=complex, expected_route=deep_agent）
+ 同名 manifest。

用法：
    python scripts/build_complex_question_set.py --count 20 --min-chars 300
    python scripts/build_complex_question_set.py --count 5 --min-chars 300   # 冒烟
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.datasets import COMPLEX, EvalSample, dump_jsonl, load_jsonl  # noqa: E402

DATA_DIR = PROJECT_ROOT / "DATA" / "eval_rag"
SOURCE = DATA_DIR / "testset_1000_local.jsonl"

PROMPT = """你是公共资源交易 / 招投标领域的普通用户。请模仿真实用户向智能助手提问的方式，写一个问题。

要求：
1. 长度 ≥ {min_chars} 字——可以有背景铺垫、口语化表达、情绪，自然就好
2. 必须是**复合问题**：包含 2 个以上、需要分别查不同规定才能回答的子问题
3. 用你自己的话问，**不要照抄**下面的信息
4. 不要出现"资料""文档""上述材料""根据以下"这类字样
5. 只输出问题本身，不要解释、不要前缀、不要引号

你可以依据的信息：
【信息一】{p1}

【信息二】{p2}
"""

_PREFIX = re.compile(r"^(问题[:：]|问[:：])\s*")


def _clean(text: str) -> str:
    text = (text or "").strip().strip('"“”')
    return _PREFIX.sub("", text).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="构建复杂/长问题集")
    parser.add_argument("--count", type=int, default=20, help="生成条数")
    parser.add_argument("--min-chars", type=int, default=300, help="问题最小长度")
    parser.add_argument("--seed", type=int, default=42, help="种子选取随机种子")
    parser.add_argument("--max-retry", type=int, default=2, help="单条长度不达标时重试次数")
    args = parser.parse_args()

    from core.config.settings import Settings
    from core.llm.registry import create_llm_for_slot

    samples = load_jsonl(SOURCE)
    seeds = [s for s in samples if s.is_multi_hop and len(s.ground_truth_text) >= 2]
    import random

    rng = random.Random(args.seed)
    rng.shuffle(seeds)
    seeds = seeds[: args.count]
    print(f"种子: {len(seeds)} 条多跳金标（源 {len(samples)} 条）")

    llm = create_llm_for_slot("deep_agent", Settings())
    out_samples: list[EvalSample] = []
    started = time.monotonic()

    for i, seed in enumerate(seeds, 1):
        question = ""
        for attempt in range(args.max_retry + 1):
            prompt = PROMPT.format(
                min_chars=args.min_chars,
                p1=seed.ground_truth_text[0][:900],
                p2=seed.ground_truth_text[1][:900],
            )
            try:
                question = _clean(str(llm.invoke(prompt).content or ""))
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 第 {i} 条生成失败: {e}")
                question = ""
            if len(question) >= args.min_chars:
                break
            print(f"  第 {i} 条第 {attempt + 1} 次生成仅 {len(question)} 字，重试…")

        if len(question) < args.min_chars:
            print(f"[skip] 第 {i} 条未达 {args.min_chars} 字，丢弃")
            continue

        out_samples.append(
            EvalSample(
                question=question,
                reference=seed.reference,
                ground_truth_chunk_ids=seed.ground_truth_chunk_ids,
                ground_truth_text=seed.ground_truth_text,
                query_type=COMPLEX,
                expected_route="deep_agent",
            )
        )
        print(f"  [{i}] {len(question)} 字 | {question[:48]}…")

    out_path = DATA_DIR / f"complex_{args.count}.jsonl"
    dump_jsonl(out_samples, out_path)
    manifest = {
        "source": SOURCE.name,
        "seed": args.seed,
        "min_chars": args.min_chars,
        "requested": args.count,
        "produced": len(out_samples),
        "avg_chars": round(sum(len(s.question) for s in out_samples) / len(out_samples), 1)
        if out_samples else 0,
        "expected_route": "deep_agent",
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    (DATA_DIR / f"complex_{args.count}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n产物: {out_path}（{len(out_samples)} 条，均长 {manifest['avg_chars']} 字）")


if __name__ == "__main__":
    main()
