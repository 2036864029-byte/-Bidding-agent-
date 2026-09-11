"""统一评测报告（E2-5）— 把五层指标汇成**一份 Markdown**。

五层与产出脚本：
  retrieval   检索层       `scripts/run_retrieval_eval.py`（--compare-layers）
  router      路由层       `scripts/run_router_intent_eval.py`
  generation  生成层 Ragas `scripts/run_ragas_generation_eval.py`
  agent_trace Agent 轨迹   `scripts/run_agent_trace_eval.py`
  system      系统指标     `scripts/run_system_metrics_eval.py`

本模块只做**装配**（纯函数）：各层先各自 render 成 Markdown，本模块拼成带目录/元信息的
统一报告，缺层显式标注"未运行"。便于 E3 门禁对拍与团队阅读。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Optional

LAYERS = ("retrieval", "router", "generation", "agent_trace", "system")
_LAYER_TITLES = {
    "retrieval": "检索层（确定性指标）",
    "router": "路由层（意图准确率）",
    "generation": "生成层（Ragas 判定）",
    "agent_trace": "Agent 轨迹层（deep_agent）",
    "system": "系统层（延迟/token/错误）",
}


def build_unified_report(
    sections: Dict[str, str], *, meta: Optional[dict] = None
) -> str:
    """sections: {layer: markdown 文本}（缺层标注"未运行"）。返回统一报告。"""
    meta = meta or {}
    lines = [
        "# 招投标智能助手 · 统一评测报告（E2-5）",
        "",
        f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    for k, v in meta.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## 目录", ""]
    for i, layer in enumerate(LAYERS, 1):
        state = "✅" if sections.get(layer) else "— 未运行"
        lines.append(f"{i}. **{_LAYER_TITLES[layer]}** {state}")
    lines.append("")
    for layer in LAYERS:
        lines += ["---", "", f"## {_LAYER_TITLES[layer]}", ""]
        body = sections.get(layer)
        lines.append(body.strip() if body else "（未运行，无数据）")
        lines.append("")
    return "\n".join(lines)


def write_unified_report(
    out_path: Path, sections: Dict[str, str], *, meta: Optional[dict] = None
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_unified_report(sections, meta=meta), encoding="utf-8")
    return out_path


__all__ = ["LAYERS", "build_unified_report", "write_unified_report"]
