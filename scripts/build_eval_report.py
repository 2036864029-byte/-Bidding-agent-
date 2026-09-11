"""统一评测报告入口（E2-5）— 逻辑在 `evals/report`。

自动发现 `test_report/` 下各层最新产物，拼成一份统一 Markdown：
  检索层   retrieval_layer_gate_*.json
  路由层   router_gold_*.json
  生成层   ragas_s1_*/report.md
  轨迹层   agent_trace_*/report.md
  系统层   system_metrics_*/report.md

用法：
    python scripts/build_eval_report.py
    python scripts/build_eval_report.py --out test_report/unified.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.report import LAYERS, write_unified_report  # noqa: E402

REPORT_DIR = PROJECT_ROOT / "test_report"


def _latest(pattern: str):
    hits = sorted(REPORT_DIR.glob(pattern))
    return hits[-1] if hits else None


def _render_retrieval(path: Path) -> str:
    d = json.loads(path.read_text(encoding="utf-8"))
    lines = [f"- 数据集 {d.get('dataset')} · {d.get('samples')} 条 · "
             f"top_k={d.get('top_k')} · {d.get('elapsed_seconds')}s", "",
             "| 口径 | 指标 | L0 | L1 | Δ |", "|---|---|---|---|---|"]
    for scope in ("multi", "single"):
        for m in ("multi_hop_recall", "recall", "mrr", "hit_rate"):
            v0 = (d.get("l0", {}).get(scope) or {}).get(m)
            v1 = (d.get("l1", {}).get(scope) or {}).get(m)
            if v0 is None or v1 is None:
                continue
            lines.append(f"| {scope} | {m} | {v0:.4f} | {v1:.4f} | {v1 - v0:+.4f} |")
    lines += ["", "**门禁**"]
    for name, g in (d.get("gates") or {}).items():
        lines.append(f"- {'✅ PASS' if g.get('passed') else '❌ FAIL'} {name} "
                     f"(L1={g.get('l1')}, 基准={g.get('target')})")
    return "\n".join(lines)


def _render_router(path: Path) -> str:
    d = json.loads(path.read_text(encoding="utf-8"))
    lines = [f"- 数据集 {d.get('datasets')} · {d.get('elapsed_seconds')}s "
             f"· workers={d.get('workers')}", ""]
    for k, v in (d.get("metrics") or {}).items():
        lines.append(f"- {k}: {v}")
    if d.get("gates"):
        lines += ["", "**门禁**"]
        for name, g in d["gates"].items():
            lines.append(f"- {name}: {g}")
    return "\n".join(lines)


def _read_layer_md(glob_dir: str) -> str | None:
    """最新**目录**下的 report.md（glob 会同时匹配 .log 文件，须过滤 is_dir）。"""
    dirs = [d for d in sorted(REPORT_DIR.glob(glob_dir)) if d.is_dir()]
    p = (dirs[-1] / "report.md") if dirs else None
    return p.read_text(encoding="utf-8") if p and p.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser(description="统一评测报告（E2-5）")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    sections: dict[str, str] = {}
    if (p := _latest("retrieval_layer_gate_*.json")):
        sections["retrieval"] = _render_retrieval(p)
    if (p := _latest("router_gold_*.json")):
        sections["router"] = _render_router(p)
    for layer, glob in (("generation", "ragas_s1_*"),
                        ("agent_trace", "agent_trace_*"),
                        ("system", "system_metrics_*")):
        if (md := _read_layer_md(glob)):
            sections[layer] = md

    out = args.out or REPORT_DIR / "unified_eval_report.md"
    write_unified_report(out, sections, meta={"层数": f"{len(sections)}/{len(LAYERS)}"})
    print(f"统一报告 → {out}")
    print(f"已纳入层: {sorted(sections)}  缺层: {sorted(set(LAYERS) - set(sections))}")


if __name__ == "__main__":
    main()
