#!/usr/bin/env python
"""质量棘轮 —— 只允许变好，不允许变差。

**为什么不设绝对阈值**：绝对阈值（"覆盖率必须 80%"、"E501 必须为 0"）在存量项目上
只有两种结局 —— 要么为达标写假测试/改无用代码，要么直接关掉门禁。棘轮换成一条
更弱但**可持续**的约束：**不比上次差**。它不要求你今天清理存量，只要求你别再添新的。

两个指标，方向相反：

    lint      风格类告警数（E501 长行 / E731 lambda 赋值 ...）  —— 越少越好
    coverage  hermetic 子集覆盖率                              —— 越多越好

用法::

    .venv/Scripts/python.exe scripts/ci/ratchet.py lint
    .venv/Scripts/python.exe scripts/ci/ratchet.py coverage --report coverage.json
    .venv/Scripts/python.exe scripts/ci/ratchet.py --update lint      # 刷新基线（变好时）

基线存 `scripts/ci/baselines.json` —— **必须入库**。放进 `.gitignore` 的目录
（如 test_report/）等于没有基线：每个人、每次 CI 都是空基线，棘轮自动失效。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
BASELINES = Path(__file__).with_name("baselines.json")

if hasattr(sys.stdout, "reconfigure"):        # 见 local_ci.py 同名说明（GBK 控制台）
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

#: 棘轮桶的 ruff 规则 —— 全是**风格/清理类**。
#: 缺陷类（F8xx/E741）不在这里：它们由 lint 门禁**直接阻断**，不需要棘轮。
STYLE_RULES = ["E501", "E731", "F401", "I001", "W291", "W292"]

#: 与 lint 门禁相同的包范围（含 test/）。
PY_PACKAGES = ["agent", "api", "core", "price_engine", "public_kb", "orchestration", "test"]


@dataclass(frozen=True)
class Metric:
    """一个棘轮指标。

    Attributes:
        name: 基线里的键名。
        direction: "lower"（越小越好，如告警数）| "higher"（越大越好，如覆盖率）。
        measure: 返回 {子项名: 数值}。
    """

    name: str
    direction: str
    measure: Callable[[argparse.Namespace], Dict[str, float]]


# ── 指标实现 ──────────────────────────────────────────────────────

class RatchetError(RuntimeError):
    """无法测量（报告缺失/格式异常）—— 与"指标退化"区分开，便于诊断。

    退出码约定：0=通过，1=退化，2=测不了。
    之所以不用 `SystemExit("消息")` 表达：它的退出码恒为 1，与"退化"混在一起，
    诊断时分不清"指标涨了"还是"报告没生成"。
    """


def _measure_lint(args: argparse.Namespace) -> Dict[str, float]:
    """按规则统计风格类告警数（逐规则计数，便于定位是哪一类在涨）。"""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--statistics",
         "--select", ",".join(STYLE_RULES), "--output-format", "json", *PY_PACKAGES],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if result.returncode not in (0, 1):        # ruff: 0=无告警, 1=有告警
        raise RatchetError(f"ruff 执行异常（exit {result.returncode}）：{result.stderr[:200]}")
    # ruff --statistics --output-format json 在无告警时输出空串
    rows = json.loads(result.stdout or "[]")
    return {row["code"]: float(row["count"]) for row in rows}


def _measure_coverage(args: argparse.Namespace) -> Dict[str, float]:
    """读 pytest-cov 的 JSON 报告（`--cov-report=json:<path>` 产出）。"""
    report = Path(args.report)
    if not report.is_absolute():
        report = ROOT / report
    if not report.exists():
        raise RatchetError(
            f"找不到覆盖率报告 {report}。\n"
            f"    先在 test 门禁里加 `--cov-report=json:<path>` 产出它。"
        )
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        return {"percent": float(data["totals"]["percent_covered"])}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RatchetError(f"覆盖率报告格式异常（{report}）：{type(exc).__name__}: {exc}") from exc


METRICS: Dict[str, Metric] = {
    "lint": Metric("lint", "lower", _measure_lint),
    "coverage": Metric("coverage", "higher", _measure_coverage),
}


# ── 棘轮逻辑 ──────────────────────────────────────────────────────

def _load() -> Dict[str, Dict[str, float]]:
    if not BASELINES.exists():
        return {}
    return json.loads(BASELINES.read_text(encoding="utf-8"))


def _save(data: Dict[str, Dict[str, float]]) -> None:
    BASELINES.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _is_worse(direction: str, current: float, baseline: float) -> bool:
    return current < baseline if direction == "higher" else current > baseline


def _is_better(direction: str, current: float, baseline: float) -> bool:
    return current > baseline if direction == "higher" else current < baseline


def run(metric: Metric, args: argparse.Namespace) -> int:
    try:
        current = metric.measure(args)
    except RatchetError as exc:
        print(f"[X] {metric.name}: 测不了 —— {exc}", file=sys.stderr)
        return 2                                   # 2=测不了（区别于 1=退化）
    except Exception as exc:                          # noqa: BLE001
        print(f"[X] {metric.name}: 测量失败 —— {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    baselines = _load()
    previous = baselines.get(metric.name, {})

    regressions: List[Tuple[str, float, float]] = []
    improvements: List[Tuple[str, float, float]] = []
    for key, value in sorted(current.items()):
        was = previous.get(key)
        if was is None:
            continue
        if _is_worse(metric.direction, value, was):
            regressions.append((key, was, value))
        elif _is_better(metric.direction, value, was):
            improvements.append((key, was, value))

    direction_hint = "越大越好" if metric.direction == "higher" else "越小越好"
    print(f"\n棘轮 [{metric.name}]（{direction_hint}）")
    for key, value in sorted(current.items()):
        was = previous.get(key)
        mark = "?" if was is None else ("!" if _is_worse(metric.direction, value, was) else " ")
        was_str = "（新指标）" if was is None else f"（基线 {was:g}）"
        print(f"  {mark} {key:12s} {value:g} {was_str}")

    if args.update:
        baselines[metric.name] = {k: v for k, v in current.items()}
        _save(baselines)
        print(f"\n基线已刷新 → {BASELINES.relative_to(ROOT)}（记得提交）")
        return 0

    if regressions:
        print(f"\n[X] {metric.name} 棘轮触发 —— 不得比基线差：")
        for key, was, now in regressions:
            print(f"    {key}: {was:g} → {now:g}")
        print(f"    修法：修掉新增的；若确属误报，用 "
              f"`--update {metric.name}` 显式刷新基线并说明理由。")
        return 1

    if improvements:
        print(f"\n[OK] {metric.name} 变好了（可用 `--update {metric.name}` 固化基线）：")
        for key, was, now in improvements:
            print(f"    {key}: {was:g} → {now:g}")
    else:
        print(f"\n[OK] {metric.name} 棘轮通过")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ratchet", description="质量棘轮 —— 只允许变好")
    parser.add_argument("metric", nargs="?", choices=sorted(METRICS), help="要检查的指标")
    parser.add_argument("--update", action="store_true", help="把当前值固化为新基线")
    parser.add_argument("--report", default="coverage.json",
                        help="coverage 指标的报告路径（默认 coverage.json）")
    args = parser.parse_args(argv)

    if args.metric is None:
        print("可用指标：")
        for name, m in sorted(METRICS.items()):
            print(f"  {name:10s} 方向={m.direction}")
        return 2

    return run(METRICS[args.metric], args)


if __name__ == "__main__":
    sys.exit(main())
