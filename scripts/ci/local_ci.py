#!/usr/bin/env python
"""本地 CI —— 一条命令跑完全部门禁，失败可定位。

**为什么先做本地版**（而不是只写 `.github/workflows/ci.yml`）：本项目是纯本地
个人项目、**无远端** —— GitHub Actions 的 workflow 永远不会被触发，只写 yml
等于零执行。但 CI 的价值（一次跑完所有门禁、失败可定位、结果可复现）与有没有
云端无关。workflow 文件同批提供，日后接入远端即可直接生效。

**四个门禁解耦**（与 workflow 的 job 一一对应）：

    lint      缺陷类静态检查 —— F8xx / E741（"写错了"类，零风格争议）
    test      hermetic 子集 + 覆盖率（integration/e2e 由 conftest 自动跳过）
    contract  契约测试（前后端 reply_type / price 列 key）
    frontend  typecheck + vitest + build

**解耦的收益**：失败定位清晰（哪个门禁红＝哪一类问题）、可单独跑、
部分失败不阻塞其余（lint 挂了仍能看到测试结果）。

用法::

    .venv/Scripts/python.exe scripts/ci/local_ci.py            # 全部
    .venv/Scripts/python.exe scripts/ci/local_ci.py lint test  # 只跑指定门禁
    .venv/Scripts/python.exe scripts/ci/local_ci.py --list     # 列出门禁
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Sequence

#: Windows 控制台默认 cp936（GBK）—— 几何符号（▶ ─ ═ ✓ ✗）编不出来会直接抛
#: UnicodeEncodeError 把运行器打挂。故结构符号一律 ASCII（中文 GBK 可编，无碍）。
#: 下面这道 reconfigure 只是兜底：万一将来又混进非 GBK 字符，退化为 "?" 而非崩溃。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
CONTRACT_DIR = ROOT / "test" / "contract"
RATCHET = ROOT / "scripts" / "ci" / "ratchet.py"
COVERAGE_JSON = "coverage.json"     # 由 test 门禁产出，供覆盖率棘轮消费（已 gitignore）

#: lint 门禁的规则集 —— 两类，都**阻断**：
#:   缺陷类  F8xx / E741        —— "写错了"（未定义名/未用变量/重定义/歧义名），零风格争议
#:   清理类  I001/F401/W291/W292 —— 导入无序/未用导入/行尾空白（P3-2 已 autofix 清零，
#:                                进阻断防止回潮；留棘轮也能防涨，但既然已是 0，阻断更强）
#: **不含** E501（长行）/ E731（lambda 赋值）—— 纯风格偏好，走棘轮，见 P3-3。
RUFF_BLOCKING_RULES = (
    "F821,F822,F823,F811,F841,E741,F601,F602,F632,I001,F401,W291,W292"
)

#: 被检查的 Python 包（含 test/ —— 2026-09-20 起纳入）。
PY_PACKAGES = ["agent", "api", "core", "price_engine", "public_kb", "orchestration", "test"]


# ── 环境解析（本机踩过的坑，集中在这里，不散到各门禁）──────────────

def _venv_python() -> str:
    """项目 venv 的解释器。

    **必须显式解析**：本机 PATH 里的 `python` 是 Anaconda 的
    （`D:\\rengongzhineng\\anaconda\\python.exe`），用它跑测试会因缺依赖全线报错，
    且报错形态像"代码坏了"。此约束见项目记忆「本地环境约定」。
    """
    for rel in ("Scripts/python.exe", "bin/python"):     # Windows / POSIX
        candidate = ROOT / ".venv" / rel
        if candidate.exists():
            return str(candidate)
    print("  [!] 未找到 .venv，回退当前解释器 —— 请确认依赖已装", file=sys.stderr)
    return sys.executable


def _node_tool(name: str) -> str:
    """解析 node 侧可执行文件（npm）。

    PATH 优先；找不到时回退到常见安装位置，最后才报错并给出明确指引 ——
    「Node 不在 PATH」在本项目踩过（见记忆「本地环境约定」），
    与其让调用方猜，不如让这里说清楚。
    """
    found = shutil.which(name)
    if found:
        return found
    for directory in (os.environ.get("ZTB_NODE_DIR"), r"D:\Zhao327"):
        if not directory:
            continue
        for suffix in (".cmd", ".exe", ""):
            candidate = Path(directory) / f"{name}{suffix}"
            if candidate.exists():
                return str(candidate)
    raise FileNotFoundError(
        f"找不到 {name}。请把它加入 PATH，或设环境变量 ZTB_NODE_DIR 指向其所在目录。"
    )


# ── 门禁模型 ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class GateResult:
    name: str
    code: int
    seconds: float

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass(frozen=True)
class Gate:
    """一个可独立执行、独立成败的门禁。

    `run` 自己负责全部执行细节（单命令或多步串联都行）—— 这样"门禁"这个概念
    不绑定"一条命令"的假设。前端门禁就是三步串联，硬套单命令模型会逼出
    特殊分支。
    """

    name: str
    desc: str
    run: Callable[[], GateResult]


def _run_steps(name: str, steps: Sequence[Sequence[str]], cwd: Path) -> GateResult:
    """按序执行多条命令，任一失败即整体失败（不继续跑后续步骤）。"""
    started = time.perf_counter()
    for step in steps:
        print(f"  $ {' '.join(step)}")
        # Windows 下 npm 是 .cmd 批处理，必须经 shell 才能执行
        code = subprocess.run(
            list(step), cwd=str(cwd), shell=(os.name == "nt"),
        ).returncode
        if code != 0:
            return GateResult(name, code, time.perf_counter() - started)
    return GateResult(name, 0, time.perf_counter() - started)


# ── 各门禁 ────────────────────────────────────────────────────────

def _lint() -> GateResult:
    """静态检查：阻断集（缺陷类 + 已清零的清理类）+ 风格类棘轮。"""
    return _run_steps("lint", (
        [_venv_python(), "-m", "ruff", "check",
         "--select", RUFF_BLOCKING_RULES, *PY_PACKAGES],
        [_venv_python(), str(RATCHET), "lint"],
    ), ROOT)


def _test() -> GateResult:
    # integration / e2e 由 conftest 自动跳过（P1 的分层），故此处无需 -m 过滤 ——
    # 这是 P1 的收益：CI 不必知道哪些用例是 live 的。
    return _run_steps("test", (
        [_venv_python(), "-m", "pytest", "test/", "-q",
         "--cov=agent", "--cov=api", "--cov=core",
         "--cov=price_engine", "--cov=orchestration",
         "--cov-report=term-missing:skip-covered",
         f"--cov-report=json:{COVERAGE_JSON}"],
        [_venv_python(), str(RATCHET), "coverage", "--report", COVERAGE_JSON],
    ), ROOT)


def _contract() -> GateResult:
    if not CONTRACT_DIR.is_dir():
        # 契约测试由方案 P4 落地。此处显式失败而非静默跳过 ——
        # 静默跳过会让"门禁存在"变成假象。
        print(f"  [X] 契约测试目录不存在：{CONTRACT_DIR.relative_to(ROOT)}（见方案 P4）",
              file=sys.stderr)
        return GateResult("contract", 1, 0.0)
    return _run_steps("contract", ([
        _venv_python(), "-m", "pytest", "test/contract", "-v",
    ],), ROOT)


def _frontend() -> GateResult:
    npm = _node_tool("npm")
    return _run_steps("frontend", (
        (npm, "run", "typecheck"),
        (npm, "run", "test"),
        (npm, "run", "build"),
    ), FRONTEND)


def _build_gates() -> List[Gate]:
    return [
        Gate("lint", "缺陷类静态检查（F8xx / E741）", _lint),
        Gate("test", "hermetic 子集 + 覆盖率", _test),
        Gate("contract", "契约测试（前后端 reply_type / price 列 key）", _contract),
        Gate("frontend", "typecheck + vitest + build", _frontend),
    ]


# ── 入口 ──────────────────────────────────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="local_ci", description="本地 CI —— 一条命令跑完全部门禁",
    )
    parser.add_argument("gates", nargs="*", help="要跑的门禁名（默认全部）")
    parser.add_argument("--list", action="store_true", help="列出门禁后退出")
    args = parser.parse_args(argv)

    gates = _build_gates()
    if args.list:
        for gate in gates:
            print(f"  {gate.name:10s} {gate.desc}")
        return 0

    if args.gates:
        known = {g.name: g for g in gates}
        unknown = [n for n in args.gates if n not in known]
        if unknown:
            print(f"未知门禁：{unknown}（可用：{list(known)}）", file=sys.stderr)
            return 2
        selected = [known[n] for n in args.gates]
    else:
        selected = list(gates)

    print(f"本地 CI —— 共 {len(selected)} 个门禁：{[g.name for g in selected]}")

    results: List[GateResult] = []
    for gate in selected:
        print(f"\n{'-' * 70}\n> {gate.name} —— {gate.desc}\n{'-' * 70}")
        results.append(gate.run())

    print(f"\n{'=' * 70}\n门禁汇总\n{'=' * 70}")
    for result in results:
        mark = "[OK]   PASS" if result.ok else "[FAIL] FAIL"
        tail = "" if result.ok else f"  (exit {result.code})"
        print(f"  {mark}  {result.name:10s} {result.seconds:6.1f}s{tail}")

    failed = [r.name for r in results if not r.ok]
    if failed:
        print(f"\n失败门禁：{failed}")
        return 1
    print("\n全部门禁通过 [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
