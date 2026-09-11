"""系统指标评测跑批（E2-4）— 真实 AgentGraph 调用在此层。

在**独立进程**里跑 N 题（Prometheus 计数器不可重置，独立进程天然从零累计），
跑完读 `core.observability.metrics.render_metrics()` → 解析 → 汇总系统段
（延迟分位 / token / 错误率 / 降级），写 out_dir。

数据源是**进程内 Prometheus**，不依赖 Langfuse。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from evals.metrics.system import parse_prometheus, render_report, summarize_system

__all__ = ["run_system_eval"]


def run_system_eval(cases: Sequence[dict], *, workers: int = 4, out_dir: Path) -> dict:
    """跑 N 题 → 采集进程内指标 → 系统段汇总。

    Args:
        cases: [{question, sample_id?}]。
        workers: 并发（AgentGraph 为 I/O 主导）。
        out_dir: 产物目录（summary.json / report.md / metrics.prom）。
    """
    from agent import AgentGraph
    from core.observability import metrics

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics.reset()

    agent = AgentGraph()

    def _one(idx_case) -> str | None:
        i, case = idx_case
        try:
            agent.invoke(case["question"], thread_id=f"sys-{case.get('sample_id', i)}")
            return None
        except Exception as e:  # noqa: BLE001  单题失败不中断
            return f"{type(e).__name__}: {e}"

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        errs = list(pool.map(_one, list(enumerate(cases))))
    elapsed = round(time.monotonic() - t0, 1)

    text = metrics.render_metrics().decode("utf-8", "ignore")
    summary = summarize_system(parse_prometheus(text))
    summary["n"] = len(cases)
    summary["errors"] = sum(1 for e in errs if e)
    summary["error_samples"] = [e for e in errs if e][:5]
    summary["elapsed_s"] = elapsed

    (out_dir / "metrics.prom").write_text(text, encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(summary), encoding="utf-8")
    return summary
