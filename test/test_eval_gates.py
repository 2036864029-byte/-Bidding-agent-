# 功能：评测门禁（E3）— pytest golden 形态，读 test_report 最新产物判定。
"""test_eval_gates —— 五层指标门禁的 pytest 形态（opt-in）。

启用方式：RUN_EVAL_GATE=1 python -m pytest test/test_eval_gates.py
产物缺失 → skip（不阻塞日常测试，与 test_layout_metrics_gate.py 同款约定）。

等价 CLI：`python scripts/run_eval_gate.py`（未达标非零退出，供 CI/定时任务）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from evals.gates import (  # noqa: E402
    all_passed,
    check_generation,
    check_retrieval,
    check_system,
    render_gates,
)

_REPORT = _PROJECT_ROOT / "test_report"
_ENABLED = os.environ.get("RUN_EVAL_GATE") == "1"

pytestmark = pytest.mark.golden


def _latest_file(pattern: str):
    hits = sorted(_REPORT.glob(pattern))
    return hits[-1] if hits else None


def _latest_dir_json(glob_dir: str, name: str):
    dirs = [d for d in sorted(_REPORT.glob(glob_dir)) if d.is_dir()]
    p = (dirs[-1] / name) if dirs else None
    return p if p and p.exists() else None


@pytest.mark.skipif(not _ENABLED, reason="RUN_EVAL_GATE=1 才启用（需先跑各层评测）")
def test_eval_gates_pass():
    """检索/生成/系统三层门禁（读最近一次产物）。"""
    results: list[dict] = []
    if (p := _latest_file("retrieval_layer_gate_*.json")):
        results += check_retrieval(json.loads(p.read_text(encoding="utf-8")))
    if (p := _latest_dir_json("ragas_s1_*", "summary.json")):
        results += check_generation(json.loads(p.read_text(encoding="utf-8")))
    if (p := _latest_dir_json("system_metrics_*", "summary.json")):
        results += check_system(json.loads(p.read_text(encoding="utf-8")))

    if not results:
        pytest.skip("test_report 无评测产物（先跑各层评测脚本）")
    assert all_passed(results), "\n" + render_gates(results)
