"""评测门禁入口（E3）— 读 `test_report/` 最新产物，跑三层门禁，未达标非零退出。

检索层   retrieval_layer_gate_*.json
生成层   ragas_s1_*/summary.json
系统层   system_metrics_*/summary.json

用法：
    python scripts/run_eval_gate.py            # 打印门禁表，未达标 exit 1
    python scripts/run_eval_gate.py --quiet    # 只输出总判定
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.gates import (  # noqa: E402
    all_passed,
    check_generation,
    check_retrieval,
    check_system,
    render_gates,
)

REPORT_DIR = PROJECT_ROOT / "test_report"

try:  # Windows 控制台默认 GBK，无法编码 ✅/❌
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def _latest_file(pattern: str):
    hits = sorted(REPORT_DIR.glob(pattern))
    return hits[-1] if hits else None


def _latest_dir_json(glob_dir: str, name: str):
    dirs = [d for d in sorted(REPORT_DIR.glob(glob_dir)) if d.is_dir()]
    p = (dirs[-1] / name) if dirs else None
    return p if p and p.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser(description="评测门禁（E3）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    results: list[dict] = []
    if (p := _latest_file("retrieval_layer_gate_*.json")):
        results += check_retrieval(json.loads(p.read_text(encoding="utf-8")))
    if (p := _latest_dir_json("ragas_s1_*", "summary.json")):
        results += check_generation(json.loads(p.read_text(encoding="utf-8")))
    if (p := _latest_dir_json("system_metrics_*", "summary.json")):
        results += check_system(json.loads(p.read_text(encoding="utf-8")))

    if not results:
        print("未找到任何评测产物（先跑各层评测脚本）", file=sys.stderr)
        sys.exit(2)

    ok = all_passed(results)
    if args.quiet:
        print("GATE_PASS" if ok else "GATE_FAIL")
    else:
        print(render_gates(results))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
