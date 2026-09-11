"""生成层评测跑批（S1/E2-2 Ragas）— 真实 Milvus + LLM 调用在此层。

流程：
  1. collect — 每题用 `kb_search`（L0）与 `kb_search_expanded`（L1）双检 +
     同模型生成两版答案；`ThreadPoolExecutor` 并发（workers），逐题写
     samples.jsonl（断点续跑，按 sample_id 去重）。
  2. ragas  — 两层各跑一次 `evaluate`（faithfulness / answer_relevancy /
     context_precision）；**判定模型可配置**（JudgeConfig，默认智谱 glm-4-plus，
     未配 key 时回退 kb_generation 槽位），批量并发由 `batch_size` 控制。
  3. 汇总   — `evals.metrics.generation.summarize` 逐题配对 delta + 分层均值，
     写 summary.json / report.md。

用法（脚本侧）：
    from evals.runners.generation import run_generation_eval
    summary = run_generation_eval(cases, workers=8, batch_size=8)

并发注意：collect 共享 retriever 单例（MilvusClient + HTTP reranker），默认
workers=4 已够；ragas 的 `batch_size` 决定批内并发（建议 6~12）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence

from evals.metrics.generation import (
    GENERATION_METRICS,
    build_ragas_rows,
    render_report,
    summarize,
)

logger = logging.getLogger(__name__)

__all__ = ["JudgeConfig", "build_judge_llm", "run_generation_eval"]


@dataclass
class JudgeConfig:
    """Ragas 判定模型（OpenAI 兼容），读环境变量。

    - JUDGE_MODEL    模型名（默认 qwen3.8-flash @ DashScope；与 .env 同步）。
    - JUDGE_BASE_URL OpenAI 兼容端点（默认 DashScope compatible-mode）。
    - JUDGE_API_KEY  密钥；未配置时回退 kb_generation 槽位（项目默认 deepseek）。
    """

    model: str = field(
        default_factory=lambda: os.getenv("JUDGE_MODEL", "qwen3.8-flash"))
    base_url: str = field(
        default_factory=lambda: os.getenv(
            "JUDGE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    api_key: str = field(
        default_factory=lambda: os.getenv(
            "JUDGE_API_KEY",
            os.getenv("DASHSCOPE_API_KEY", os.getenv("ZHIPU_API_KEY", ""))))


def build_judge_llm(cfg: JudgeConfig, settings=None):
    """构造判定 LLM。配置了 JUDGE_API_KEY → OpenAI 兼容 ChatOpenAI；否则回退默认。

    Args:
        cfg: JudgeConfig（读 env）。
        settings: 回退路径用（kb_generation 槽位）；None 时新建 Settings()。

    Returns:
        langchain BaseChatModel（ChatOpenAI，temperature=0）。
    """
    if cfg.api_key:
        from langchain_openai import ChatOpenAI

        logger.info("Ragas judge → %s @ %s", cfg.model, cfg.base_url)
        return ChatOpenAI(
            model=cfg.model,
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
            temperature=0.0,
        )
    from core.config.settings import Settings
    from core.llm.registry import create_llm_for_slot

    settings = settings or Settings()
    logger.info("Ragas judge → 回退 kb_generation 槽位（未配置 JUDGE_API_KEY）")
    return create_llm_for_slot("kb_generation", settings)


def _one_question(case: dict) -> dict:
    """单题双检 + 双生成（collect 阶段每个 worker 的任务）。"""
    from public_kb.tools import kb_search, kb_search_expanded

    rec = {
        "sample_id": case["sample_id"],
        "question": case["question"],
        "reference": case.get("reference", ""),
        "query_type": case.get("query_type", ""),
        "l0": None,
        "l1": None,
    }
    for layer, fn in (("l0", kb_search), ("l1", kb_search_expanded)):
        try:
            r = fn(case["question"])
            if not r.chunks:
                rec[layer] = {"contexts": [], "answer": "", "retrieved": 0}
                continue
            answer = _gen_answer(r.rendered_context, case["question"])
            rec[layer] = {
                "contexts": [c.content for c in r.chunks],
                "answer": answer,
                "retrieved": len(r.chunks),
            }
        except Exception as e:  # noqa: BLE001  单题失败降级，不中断整批
            rec[layer] = {
                "contexts": [], "answer": "", "retrieved": 0,
                "error": f"{type(e).__name__}: {e}",
            }
    return rec


def _gen_answer(rendered_context: str, question: str) -> str:
    """按子图 _generate_node 同一链路生成（非流式；护栏重试不进本评测）。"""
    from langchain_core.output_parsers import StrOutputParser

    gen_llm, prompt = _runtime_parts()
    return (prompt | gen_llm | StrOutputParser()).invoke(
        {"context": rendered_context, "question": question}).strip()


_runtime: dict = {}


def _runtime_parts():
    """进程级生成运行时（llm + prompt），带锁惰性创建。"""
    if not _runtime:
        from core.config.settings import Settings
        from core.llm.registry import create_llm_for_slot

        from public_kb.rendering.prompts import build_prompt

        settings = Settings()
        _runtime["llm"] = create_llm_for_slot("kb_generation", settings)
        _runtime["prompt"] = build_prompt(
            settings.system_prompt,
            enable_inline_citations=settings.enable_inline_citations,
        )
    return _runtime["llm"], _runtime["prompt"]


def _load_done(samples_file: Path) -> set:
    if not samples_file.exists():
        return set()
    done = set()
    for line in samples_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                done.add(json.loads(line)["sample_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def _collect(
    cases: Sequence[dict],
    *,
    workers: int,
    resume: bool,
    out_file: Path,
) -> tuple[List[dict], dict]:
    """并发采集（检索 + 生成）。返回 (全部样本按 sample_id 排序, 统计)。"""
    done = _load_done(out_file) if resume else set()
    todo = [c for c in cases if c["sample_id"] not in done]
    print(f"collect: total={len(cases)} done={len(done)} todo={len(todo)} "
          f"workers={workers}", flush=True)

    t0 = time.time()
    new_recs: List[dict] = []
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i, rec in enumerate(pool.map(_one_question, todo), 1):
                new_recs.append(rec)
                if i % 20 == 0 or i == len(todo):
                    print(f"  [{i}/{len(todo)}] {rec['question'][:24]}…", flush=True)

    samples = done_recs(out_file) + new_recs if resume and out_file.exists() else new_recs
    samples.sort(key=lambda s: s["sample_id"])
    out_file.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n",
        encoding="utf-8")

    stats = {
        "empty_retrieval": sum(1 for s in samples
                               if s["l0"] and not s["l0"].get("contexts")
                               or s["l1"] and not s["l1"].get("contexts")),
        "gen_error": sum(1 for s in samples
                         if "error" in (s.get("l0") or {})
                         or "error" in (s.get("l1") or {})),
        "workers": workers,
        "time_s": round(time.time() - t0, 1),
    }
    return samples, stats


def done_recs(samples_file: Path) -> List[dict]:
    """读已存在的 samples.jsonl（resume 时先取旧记录）。"""
    if not samples_file.exists():
        return []
    return [json.loads(line) for line in
            samples_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_ragas(samples: Sequence[dict], layer: str, settings,
               batch_size: int, names: Sequence[str]) -> List[dict]:
    """对某一层跑 Ragas（仅 requested 指标），返回判定行（失败列 NaN）。"""
    from datasets import Dataset
    from ragas import evaluate

    rows_in = build_ragas_rows(samples, layer)
    if not rows_in:
        print(f"[{layer}] no valid samples, skip", flush=True)
        return []
    ds = Dataset.from_list(rows_in)
    print(f"[{layer}] evaluating {len(rows_in)} samples with "
          f"{list(names) or 'ALL'}…", flush=True)
    judge_llm, embeddings = _ragas_runtime(settings)
    metrics, _ = _ragas_metrics(judge_llm, embeddings, names)
    result = evaluate(
        ds,
        metrics=metrics,
        run_config=result_run_config(),
        raise_exceptions=False,
        show_progress=True,
        batch_size=batch_size,
    )
    pdf = result.to_pandas()
    return [dict(row) for row in pdf.to_dict(orient="records")]


def _merge_rows(existing: Sequence[dict], new: Sequence[dict]) -> List[dict]:
    """按 user_input 合并判定行（新行补入已有行的指标列；缺失行追加）。

    支持分次增量补跑：首跑 3 指标，后续 `--metrics context_recall` 补跑后
    合并进 l{layer}.jsonl，再 summarize 即得全指标报告。
    """
    by_q = {row["user_input"]: row for row in existing}
    for row in new:
        prev = by_q.get(row["user_input"])
        if prev is None:
            by_q[row["user_input"]] = dict(row)
        else:
            prev.update(row)
    return list(by_q.values())


def result_run_config():
    from ragas.run_config import RunConfig

    return RunConfig(timeout=180, max_retries=3, max_workers=16)


def _ragas_metrics(judge_llm, embeddings, names: Sequence[str]):
    """按需构造 Ragas 指标并绑定 judge LLM / 嵌入。

    Args:
        names: 指标名子集（如 ("context_recall",) 增量补跑）；空=全部。
    """
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    llm_w = LangchainLLMWrapper(judge_llm)
    emb_w = LangchainEmbeddingsWrapper(embeddings)
    all_metrics = _all_ragas_metrics()
    selected = [
        m for m in all_metrics
        if not names or m.name in names
    ]
    for m in all_metrics:
        m.llm = llm_w
    answer_relevancy = next(m for m in all_metrics if m.name == "answer_relevancy")
    answer_relevancy.embeddings = emb_w
    return selected, llm_w


def _all_ragas_metrics():
    """ragas 指标单例（faithfulness / answer_relevancy / context_precision / context_recall）。"""
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    return [faithfulness, answer_relevancy, context_precision, context_recall]


def _ragas_runtime(settings):
    """进程级 Ragas 判定运行时（judge LLM + 嵌入），带锁惰性。"""
    if "_judge" not in _runtime:
        from core.embeddings import get_embeddings

        _runtime["_judge"] = build_judge_llm(JudgeConfig(), settings)
        _runtime["_emb"] = get_embeddings(settings)
    return _runtime["_judge"], _runtime["_emb"]


def run_generation_eval(
    cases: Sequence[dict],
    *,
    workers: int = 4,
    batch_size: int = 12,
    metrics: Sequence[str] | None = None,
    shard: tuple[int, int] | None = None,
    resume: bool = True,
    out_dir: Path,
) -> dict:
    """S1 生成层评测主入口：采集 → 两层 Ragas → 汇总，产物写 out_dir。

    Args:
        cases: 金标用例（含 question / reference / query_type / sample_id）。
        workers: 采集阶段并发（检索 + 生成）。
        batch_size: Ragas 判定批内并发。
        metrics: 判定的指标子集（None=全部，含 context_recall）。支持增量补跑：
            已有 l{layer}.jsonl 时按 user_input 合并（首跑之外的指标再跑一次即并入）。
        shard: (K, N) 进程级分片——只处理 cases[K::N]，用于绕开 ragas 内部并发瓶颈
            （多进程并行）。分片结果用 merge_shards(out_dir) 合并。
        resume: 复用 out_dir/samples.jsonl 已采集样本（按 sample_id 去重）。
        out_dir: 产物目录（samples.jsonl / layer0.jsonl / layer1.jsonl /
            summary.json / report.md）。

    Returns:
        summary dict（同 evals.metrics.generation.summarize）。
    """
    from core.config.settings import Settings

    if shard is not None:
        k, n = shard
        cases = list(cases)[k::n]
        print(f"shard {k}/{n}: {len(cases)} samples", flush=True)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    samples_file = out_dir / "samples.jsonl"

    samples, collect_stats = _collect(
        cases, workers=workers, resume=resume, out_file=samples_file)
    if not samples:
        raise ValueError("没有可评测样本（samples.jsonl 为空）")

    settings = Settings()
    names = list(metrics) if metrics else []
    layer_rows: dict[str, List[dict]] = {}
    for layer in ("l0", "l1"):
        new_rows = _run_ragas(samples, layer, settings, batch_size, names)
        layer_file = out_dir / f"{layer}.jsonl"
        existing = (
            [json.loads(line) for line in
             layer_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            if layer_file.exists() else []
        )
        merged = _merge_rows(existing, new_rows)
        merged.sort(key=lambda r: r["user_input"])
        layer_file.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n",
            encoding="utf-8")
        layer_rows[layer] = merged

    summary = summarize(samples, layer_rows["l0"], layer_rows["l1"], collect_stats)
    summary["judge"] = {
        "model": JudgeConfig().model,
        "base_url": JudgeConfig().base_url,
        "configured": bool(JudgeConfig().api_key),
    }

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(
        render_report(summary), encoding="utf-8")
    return summary


def merge_shards(out_dir: Path) -> dict:
    """合并 `out_dir/shard_*/` 的分片产物 → 统一 samples / l0 / l1 / summary / report。

    分片是进程级并行（绕开 ragas 内部并发瓶颈）的产物；各分片样本不相交，
    按 sample_id / user_input 去重合并即可复原全量结果。
    """
    out_dir = Path(out_dir)
    shard_dirs = sorted(d for d in out_dir.glob("shard_*") if d.is_dir())
    if not shard_dirs:
        raise ValueError(f"未找到分片目录: {out_dir}/shard_*")

    samples: dict[str, dict] = {}
    for d in shard_dirs:
        f = d / "samples.jsonl"
        if f.exists():
            for s in done_recs(f):
                samples.setdefault(s["sample_id"], s)
    merged_samples = sorted(samples.values(), key=lambda s: s["sample_id"])

    layer_rows: dict[str, List[dict]] = {}
    for layer in ("l0", "l1"):
        rows: List[dict] = []
        for d in shard_dirs:
            f = d / f"{layer}.jsonl"
            if f.exists():
                rows = _merge_rows(rows, done_recs(f))
        rows.sort(key=lambda r: r["user_input"])
        layer_rows[layer] = rows
        (out_dir / f"{layer}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    (out_dir / "samples.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in merged_samples) + "\n",
        encoding="utf-8")

    collect_stats = {
        "empty_retrieval": sum(
            1 for s in merged_samples
            if (s["l0"] and not s["l0"].get("contexts"))
            or (s["l1"] and not s["l1"].get("contexts"))),
        "gen_error": sum(
            1 for s in merged_samples
            if "error" in (s.get("l0") or {}) or "error" in (s.get("l1") or {})),
        "workers": len(shard_dirs),
        "time_s": 0.0,
    }
    summary = summarize(merged_samples, layer_rows["l0"], layer_rows["l1"],
                        collect_stats)
    summary["judge"] = {
        "model": JudgeConfig().model,
        "base_url": JudgeConfig().base_url,
        "configured": bool(JudgeConfig().api_key),
        "shards": len(shard_dirs),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(summary), encoding="utf-8")
    return summary
