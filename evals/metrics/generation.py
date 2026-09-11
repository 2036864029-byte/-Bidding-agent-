"""生成层评测指标（S1/E2-2 Ragas）— 纯函数层，无 LLM / Milvus 调用。

对 Layer 0（top-11）与 Layer 1（统一轻量补检 ~17.7 块）的生成质量做**逐题配对**
对比，判断 Layer 1 把上下文扩窗后是否稀释生成侧。判题由
`evals.runners.generation` 调 Ragas 完成，本模块只做数据变换与汇总（可单测）。

数据契约：
  samples : list[dict]，每题一条——
    {sample_id, question, reference, query_type,
     l0: {"contexts": [str], "answer": str} | None,   # None=检索/生成失败
     l1: {"contexts": [str], "answer": str} | None}
  rows    : list[dict]，Ragas `EvaluationResult.to_pandas()` 逐行转 dict——
    至少含 user_input 与三指标列（失败行该列为 NaN，随 JSON 序列化保留）。

指标口径（Ragas 0.3.1）：
  faithfulness      忠实度——答案的事实陈述是否都被检索上下文支持
  answer_relevancy  回答相关性——答案是否贴合问题（需嵌入模型）
  context_precision 上下文精度——检索块是否命中参考答案信息（对照 reference）
  context_recall    上下文召回——检索上下文是否覆盖参考的**全部分支信息**（LLM 判定）
"""

from __future__ import annotations

from typing import Dict, List, Sequence

GENERATION_METRICS: tuple[str, ...] = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)

_METRIC_LABELS = {
    "faithfulness": "忠实度 faithfulness",
    "answer_relevancy": "回答相关性 answer_relevancy",
    "context_precision": "上下文精度 context_precision",
    "context_recall": "上下文召回 context_recall",
}


def _present_metrics(rows_l0: Sequence[dict], rows_l1: Sequence[dict]) -> list[str]:
    """行中实际存在的指标列（支持分次补跑增量合并）。"""
    return [m for m in GENERATION_METRICS
            if any(m in row for row in rows_l0)
            or any(m in row for row in rows_l1)]


# ── 数据变换 ────────────────────────────────────────────────


def layer_valid(samples: Sequence[dict], layer: str) -> List[dict]:
    """某层有效样本（contexts 与 answer 均非空）。"""
    return [
        s for s in samples
        if (rec := s.get(layer)) is not None
        and rec.get("contexts") and rec.get("answer")
    ]


def build_ragas_rows(samples: Sequence[dict], layer: str) -> List[dict]:
    """某层有效样本 → Ragas Dataset 行（列名 = ragas 单轮标准字段）。

    多带一层 sample_id，便于判定后按 id 对齐（不依赖 question 唯一性）。
    """
    rows = []
    for s in layer_valid(samples, layer):
        rec = s[layer]
        rows.append({
            "sample_id": s["sample_id"],
            "user_input": s["question"],
            "response": rec["answer"],
            "retrieved_contexts": rec["contexts"],
            "reference": s.get("reference", ""),
        })
    return rows


def _mean(vals: Sequence[float]) -> float:
    """去 NaN 后取均值；空输入返回 NaN。"""
    vals = [v for v in vals if v == v]
    return round(sum(vals) / len(vals), 4) if vals else float("nan")


def _row_index(rows: Sequence[dict]) -> Dict[str, dict]:
    """user_input → row 索引（同题只保留最后一条）。"""
    index: Dict[str, dict] = {}
    for row in rows:
        index[row["user_input"]] = row
    return index


def summarize(
    samples: Sequence[dict],
    rows_l0: Sequence[dict],
    rows_l1: Sequence[dict],
    collect_stats: dict,
) -> dict:
    """分层均值 + 逐题配对 delta + 按 query_type 分桶。

    Args:
        samples: 采集结果（含两层的 contexts/answer 与 question/reference）。
        rows_l0 / rows_l1: 两层 Ragas 判定行（带 user_input + 三指标列）。
        collect_stats: 采集阶段统计（空检索 / 生成失败 / 耗时等）。

    Returns:
        汇总 dict（结构见模块 docstring；paired_delta 仅统计同题两测都出分的样本）。
    """
    idx0, idx1 = _row_index(rows_l0), _row_index(rows_l1)
    valid0, valid1 = layer_valid(samples, "l0"), layer_valid(samples, "l1")
    paired = [s for s in samples
              if s["sample_id"] in {x["sample_id"] for x in valid0}
              and s["sample_id"] in {x["sample_id"] for x in valid1}]
    metrics = _present_metrics(rows_l0, rows_l1)

    summary: dict = {
        "metrics": metrics,
        "n_total": len(samples),
        "n_valid_l0": len(valid0),
        "n_valid_l1": len(valid1),
        "n_paired": len(paired),
        "collect": collect_stats,
        "per_layer": {
            "l0": {"n": len(rows_l0), "mean": {}},
            "l1": {"n": len(rows_l1), "mean": {}},
        },
        "paired_delta": {
            m: {"mean": float("nan"), "pairs": 0,
                "worsened": 0, "unchanged": 0, "improved": 0}
            for m in metrics
        },
        "by_type": {},
    }

    for layer, rows in (("l0", rows_l0), ("l1", rows_l1)):
        for m in metrics:
            summary["per_layer"][layer]["mean"][m] = _mean(
                [row.get(m, float("nan")) for row in rows])

    for m in metrics:
        deltas = []
        for s in paired:
            r0, r1 = idx0.get(s["question"]), idx1.get(s["question"])
            if r0 is None or r1 is None:
                continue
            v0, v1 = r0.get(m, float("nan")), r1.get(m, float("nan"))
            if v0 != v0 or v1 != v1:
                continue
            deltas.append(v1 - v0)
        d = summary["paired_delta"][m]
        d["pairs"] = len(deltas)
        d["mean"] = _mean(deltas)
        d["worsened"] = sum(1 for x in deltas if x < -1e-9)
        d["unchanged"] = sum(1 for x in deltas if abs(x) <= 1e-9)
        d["improved"] = sum(1 for x in deltas if x > 1e-9)

    for qt in sorted({s.get("query_type", "") for s in paired}):
        sub = [s for s in paired if s.get("query_type") == qt]
        bucket: dict = {"n": len(sub), "per_layer": {"l0": {}, "l1": {}}}
        for layer, rows, idx in (("l0", rows_l0, idx0), ("l1", rows_l1, idx1)):
            for m in metrics:
                bucket["per_layer"][layer][m] = _mean([
                    idx[s["question"]].get(m, float("nan"))
                    for s in sub if s["question"] in idx
                ])
        summary["by_type"][qt] = bucket
    return summary


# ── 报告渲染 ────────────────────────────────────────────────


def render_report(summary: dict) -> str:
    """summary → Markdown 报告文本。"""
    lines = [
        "# S1 Ragas 生成层评测（Layer 0 vs Layer 1）",
        "",
        f"- 样本: 共 {summary['n_total']} 题，有效 L0={summary['n_valid_l0']} / "
        f"L1={summary['n_valid_l1']}，可配对 {summary['n_paired']}",
        f"- 采集: 空检索 {summary['collect'].get('empty_retrieval', 0)} 题，"
        f"生成失败 {summary['collect'].get('gen_error', 0)} 题，"
        f"并发 {summary['collect'].get('workers', '?')}，耗时 {summary['collect'].get('time_s', 0)}s",
        "",
        "## 1. 分层均值与配对 delta",
        "",
        "| 指标 | Layer0 (top-11) | Layer1 (补检) | Δ(L1-L0) | 变差/不变/变好 | 配对 n |",
        "|---|---|---|---|---|---|",
    ]
    for m in summary["metrics"]:
        d = summary["paired_delta"][m]
        dstr = f"{d['mean']:+.4f}" if d["mean"] == d["mean"] else "N/A"
        lines.append(
            f"| {_METRIC_LABELS[m]} | "
            f"{summary['per_layer']['l0']['mean'][m]:.4f} | "
            f"{summary['per_layer']['l1']['mean'][m]:.4f} | {dstr} | "
            f"{d['worsened']}/{d['unchanged']}/{d['improved']} | {d['pairs']} |"
        )
    lines += ["", "## 2. 按 query_type 分桶（层均值）", ""]
    for qt, bucket in summary["by_type"].items():
        lines.append(f"### {qt}（n={bucket['n']}）")
        lines.append("| 指标 | L0 | L1 |")
        lines.append("|---|---|---|")
        for m in summary["metrics"]:
            lines.append(
                f"| {_METRIC_LABELS[m]} | "
                f"{bucket['per_layer']['l0'][m]:.4f} | "
                f"{bucket['per_layer']['l1'][m]:.4f} |"
            )
        lines.append("")
    lines += [
        "## 3. 判定口径",
        "",
        "- 生成模型 = kb_generation 槽位；判定模型 = judge 配置（见 summary.collect）。",
        "- retrieved_contexts = 该层实际喂给生成 LLM 的块正文列表；reference = 金标参考答案。",
        "- 配对 delta 仅对同题两测都出分的样本；失败行（NaN）不计入。",
        "- 关注点：Δ(faithfulness / answer_relevancy) 显著为负 → 生成侧被扩窗稀释。",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "GENERATION_METRICS",
    "layer_valid",
    "build_ragas_rows",
    "summarize",
    "render_report",
]
