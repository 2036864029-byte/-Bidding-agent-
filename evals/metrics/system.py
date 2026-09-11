"""系统指标（E2-4）— 解析 Prometheus 文本，出延迟 / token / 错误率汇总。**纯函数**。

数据源：`core.observability.metrics.render_metrics()`（进程内 Prometheus 文本）或
API 的 `GET /metrics`。**不依赖 Langfuse**（v4 events_only 下 `/api/public/traces`
不可用；进程内指标更稳、零外部依赖）。

指标族：
  agent_branch_latency_seconds{branch}   分支/节点耗时（Histogram）
  llm_latency_seconds{slot,model}        LLM 调用耗时（Histogram）
  llm_tokens_total{slot,model,kind}      token 计数（kind=prompt/completion）
  llm_errors_total{slot,model}           LLM 失败计数
  agent_guardrail_fails_total{branch}    护栏失败
  agent_recall_fallback_total{layer}     检索降级

流程：评测侧 `run_*` 跑批 → `render_metrics()` → `parse_prometheus` → `summarize_system`。
Prometheus 计数器不可重置，故须在**独立进程**里跑（每次评测一个进程，天然从零累计）。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

_LINE = re.compile(r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>[-+0-9.eE]+)$')
_LABEL = re.compile(r'(?P<k>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<v>(?:[^"\\]|\\.)*)"')


def parse_prometheus(text: str) -> Dict[str, List[dict]]:
    """Prometheus 文本 → {metric_name: [{labels: {...}, value: float}]}。"""
    out: Dict[str, List[dict]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        labels = {lm.group("k"): lm.group("v")
                  for lm in _LABEL.finditer(m.group("labels") or "")}
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        out.setdefault(m.group("name"), []).append({"labels": labels, "value": value})
    return out


def _hist_quantile(buckets: List[tuple], total: float, q: float) -> Optional[float]:
    """从累积桶 [(le, cum_count)[递增]] 估分位（桶内线性插值；+Inf 桶报上一有限上界）。"""
    if not buckets or total <= 0:
        return None
    target = q * total
    prev_le, prev_cum = 0.0, 0.0
    for le, cum in buckets:
        if cum >= target:
            if le == float("inf"):
                return round(prev_le, 4) if prev_le else None
            if cum == prev_cum:
                return le
            frac = (target - prev_cum) / (cum - prev_cum)
            return round(prev_le + (le - prev_le) * frac, 4)
        prev_le, prev_cum = le, cum
    return round(prev_le, 4) if prev_le else None


def _histograms(parsed: Dict[str, List[dict]]) -> Dict[str, Dict[tuple, dict]]:
    """把 *_bucket / *_count / *_sum 归并成 {base_name: {label_tuple: {...}}}。"""
    result: Dict[str, Dict[tuple, dict]] = {}
    for name, rows in parsed.items():
        for suffix in ("_bucket", "_count", "_sum"):
            if not name.endswith(suffix):
                continue
            base = name[: -len(suffix)]
            for row in rows:
                labels = dict(row["labels"])
                le = labels.pop("le", None)
                key = tuple(sorted(labels.items()))
                entry = result.setdefault(base, {}).setdefault(
                    key, {"labels": labels, "buckets": [], "count": 0.0, "sum": 0.0})
                if suffix == "_bucket":
                    if le is not None:
                        try:
                            entry["buckets"].append((float(le), row["value"]))
                        except ValueError:
                            pass
                    else:
                        entry["count"] = row["value"]      # +Inf 桶
                elif suffix == "_count":
                    entry["count"] = row["value"]
                else:
                    entry["sum"] = row["value"]
    for base in result:
        for entry in result[base].values():
            entry["buckets"].sort(key=lambda x: x[0])
    return result


def summarize_system(parsed: Dict[str, List[dict]]) -> dict:
    """解析结果 → 系统段汇总（延迟分位 / token / 错误 / 降级）。"""
    hist = _histograms(parsed)

    def _latency_block(base: str) -> Dict[str, dict]:
        out = {}
        for key, e in (hist.get(base) or {}).items():
            labels = e["labels"]
            total = e["count"] or (e["buckets"][-1][1] if e["buckets"] else 0.0)
            if total <= 0:
                continue
            name = "|".join(f"{k}={v}" for k, v in sorted(labels.items())) or "-"
            out[name] = {
                "count": int(total),
                "mean_ms": round(e["sum"] / total * 1000, 1),
                "p50_ms": (lambda x: round(x * 1000, 1) if x is not None else None)(
                    _hist_quantile(e["buckets"], total, 0.5)),
                "p95_ms": (lambda x: round(x * 1000, 1) if x is not None else None)(
                    _hist_quantile(e["buckets"], total, 0.95)),
            }
        return out

    def _counter_by(metric: str, key_names: tuple) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for row in parsed.get(metric, []):
            labels = {k: row["labels"].get(k, "") for k in key_names}
            key = "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
            out[key] = out.get(key, 0.0) + row["value"]
        return {k: round(v, 1) for k, v in sorted(out.items())}

    # LLM token：按 sorted(slot, model) 汇总 prompt/completion（键序与延迟块一致）
    tokens: Dict[str, dict] = {}
    for row in parsed.get("llm_tokens_total", []):
        lab = row["labels"]
        labels = {"slot": lab.get("slot", ""), "model": lab.get("model", "")}
        key = "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
        entry = tokens.setdefault(key, {"prompt": 0.0, "completion": 0.0})
        kind = lab.get("kind", "?")
        entry[kind] = entry.get(kind, 0.0) + row["value"]

    return {
        "branch_latency": _latency_block("agent_branch_latency_seconds"),
        "llm_latency": _latency_block("llm_latency_seconds"),
        "llm_tokens": {k: {kk: int(vv) for kk, vv in v.items()}
                       for k, v in sorted(tokens.items())},
        "llm_errors": _counter_by("llm_errors_total", ("slot", "model")),
        "guardrail_fails": _counter_by("agent_guardrail_fails_total", ("branch",)),
        "recall_fallbacks": _counter_by("agent_recall_fallback_total", ("layer",)),
    }


def render_report(summary: dict) -> str:
    """系统段 → Markdown（供统一报告 E2-5 拼接）。"""
    lines = ["# 系统指标（E2-4，进程内 Prometheus）", ""]

    def _table(title: str, block: Dict[str, dict]):
        lines.append(f"## {title}")
        if not block:
            lines.append("（无数据）")
            lines.append("")
            return
        lines.append("| 维度 | 次数 | 均值(ms) | p50(ms) | p95(ms) |")
        lines.append("|---|---|---|---|---|")
        for name, v in block.items():
            lines.append(f"| {name} | {v['count']} | {v['mean_ms']} | "
                         f"{v['p50_ms']} | {v['p95_ms']} |")
        lines.append("")

    _table("分支/节点耗时", summary.get("branch_latency", {}))
    _table("LLM 调用耗时", summary.get("llm_latency", {}))

    tok = summary.get("llm_tokens") or {}
    lines.append("## LLM token 消耗")
    if tok:
        lines.append("| slot/model | prompt | completion |")
        lines.append("|---|---|---|")
        for k, v in tok.items():
            lines.append(f"| {k} | {v.get('prompt', 0)} | {v.get('completion', 0)} |")
    else:
        lines.append("（无数据）")
    lines += ["", "## 错误与降级", ""]
    lines.append(f"- LLM 失败: {summary.get('llm_errors') or '无'}")
    lines.append(f"- 护栏失败: {summary.get('guardrail_fails') or '无'}")
    lines.append(f"- 检索降级: {summary.get('recall_fallbacks') or '无'}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["parse_prometheus", "summarize_system", "render_report"]
