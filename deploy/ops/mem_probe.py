#!/usr/bin/env python3
"""服务器内存 / CPU 峰值采样器 —— 给「2C4G 到底够不够」提供实测数据。

## 为什么需要它（而不是「跑起来看着没崩就行」）

2C4G 的风险不在启动瞬间，而在 **MemAvailable 的余量** 和 **OOM killer 挑谁杀**：

- 空载「能跑」很容易，加一点并发就变成「第一次能用、第二次 502」；
- 根因通常是内核杀了 Milvus 或 api 进程，而两者都配了 `restart: unless-stopped`，
  会自动起回来 —— 表面看像「偶发卡顿」，实则是容量不足；
- 所以要看三样东西：**可用内存最低点**、**容器重启计数**、**swap 有没有被用起来**。

单看 `docker stats` 的瞬时值没用（它只报当前值，且 CPU% 是累计平均），必须连续采样取峰值。

## 用法（在服务器上，仓库根目录）

```bash
# 1) 空载基线：stack 刚起来就开始采，别等 —— Milvus 首次加载 segment 就是内存峰值
python3 deploy/ops/mem_probe.py --duration 1800

# 2) 压测档位：本脚本只采样，压测在另一个终端跑 scripts/load_test_chat.py
python3 deploy/ops/mem_probe.py --duration 240 --out load_c5.jsonl

# 3) 事后出报告（不含采样，只读文件）
python3 deploy/ops/mem_probe.py --report baseline.jsonl

# 4) 只想快速看一眼当前状态
python3 deploy/ops/mem_probe.py --duration 0
```

采样结果落在 `deploy/ops/reports/` 下（JSONL，每行一个采样点），终端同时打印峰值报告
与绿/黄/红判定。

依赖：python3 标准库 + docker CLI。**Linux only**（读 `/proc/meminfo`）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 判定阈值（MB）──────────────────────────────────────────────────────────
# 说明：4G 机器 MemTotal ≈ 3.7-3.9 GB（内核 + 保留区吃掉一部分）。
#   ≥ 800MB 余量：有缓冲，可以进压测
#   300-800MB  ：无缓冲，任何一次解析/召回峰值都可能直接 OOM，只能当「能启动」看
#   < 300MB    ：已经贴着上限，不必压测，直接判定升配
# 这几条是**经验值**，不是理论推导；跑够样本后可据实调整。
HEADROOM_GREEN_MB = 800
HEADROOM_YELLOW_MB = 300
SWAP_WARN_MB = 200

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


# ── 采样 ──────────────────────────────────────────────────────────────────
def read_meminfo() -> dict[str, float]:
    """/proc/meminfo → {键: MB}（内核单位是 kB）。"""
    raw: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                raw[key.strip()] = int(parts[0])
    mb = 1 / 1024
    return {
        "mem_total": raw.get("MemTotal", 0) * mb,
        "mem_available": raw.get("MemAvailable", 0) * mb,
        "swap_total": raw.get("SwapTotal", 0) * mb,
        "swap_free": raw.get("SwapFree", 0) * mb,
    }


_SIZE_UNIT_MB = {
    "B": 1 / 1024 / 1024,
    "kB": 1 / 1024,
    "KiB": 1 / 1024,
    "MiB": 1.0,
    "GiB": 1024.0,
    "TiB": 1024.0 * 1024,
}


def parse_size_mb(text: str) -> float:
    """``"1.234GiB"`` / ``"512MiB"`` / ``"0B"`` → MB。docker 的 MemUsage 首段格式。"""
    token = text.strip().split()[0] if text.strip() else "0B"
    digits = ""
    unit = ""
    for ch in token:
        if ch.isdigit() or ch == ".":
            digits += ch
        else:
            unit += ch
    try:
        value = float(digits or 0)
    except ValueError:
        return 0.0
    return value * _SIZE_UNIT_MB.get(unit, 1.0)


def _docker(*args: str, timeout: float = 30.0) -> str:
    proc = subprocess.run(  # noqa: S603 - 参数是常量，无 shell
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} 失败: {proc.stderr.strip()[:200]}")
    return proc.stdout


def sample_containers() -> tuple[dict[str, float], float]:
    """所有运行中容器的内存（MB）与 CPU 合计（%）。"""
    out = _docker("stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}")
    mem: dict[str, float] = {}
    cpu = 0.0
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        name = cols[0].strip()
        mem[name] = round(parse_size_mb(cols[1].split("/")[0]), 1)
        try:
            cpu += float(cols[2].strip().rstrip("%") or 0)
        except ValueError:
            pass
    return mem, round(cpu, 1)


def sample_restarts() -> dict[str, int]:
    """每个容器的 RestartCount —— OOM 的间接证据（计数器涨了 = 有人被杀过）。"""
    raw = _docker("ps", "-a", "--format", "{{.Names}}")
    names = [n.strip() for n in raw.splitlines() if n.strip()]
    if not names:
        return {}
    counts: dict[str, int] = {}
    for i in range(0, len(names), 40):  # 命令行长度保护
        chunk = names[i : i + 40]
        out = _docker("inspect", "--format", "{{.Name}}\t{{.RestartCount}}", *chunk)
        for line in out.splitlines():
            cols = line.split("\t")
            if len(cols) == 2:
                counts[cols[0].lstrip("/")] = int(cols[1].strip() or 0)
    return counts


def take_sample(elapsed: float) -> dict:
    meminfo = read_meminfo()
    containers, cpu = sample_containers()
    return {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "elapsed_s": round(elapsed, 1),
        "mem_total_mb": round(meminfo["mem_total"], 1),
        "mem_available_mb": round(meminfo["mem_available"], 1),
        "swap_used_mb": round(meminfo["swap_total"] - meminfo["swap_free"], 1),
        "containers_mb": containers,
        "containers_total_mb": round(sum(containers.values()), 1),
        "cpu_pct_sum": cpu,
        "restarts": sample_restarts(),
    }


# ── 报告 ──────────────────────────────────────────────────────────────────
def _load(jsonl: Path) -> list[dict]:
    rows = []
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _peak_containers(rows: list[dict]) -> list[tuple[str, float]]:
    peaks: dict[str, float] = {}
    for row in rows:
        for name, mb in row["containers_mb"].items():
            peaks[name] = max(peaks.get(name, 0.0), mb)
    return sorted(peaks.items(), key=lambda kv: kv[1], reverse=True)


def _restart_deltas(rows: list[dict]) -> dict[str, int]:
    first, last = rows[0].get("restarts", {}), rows[-1].get("restarts", {})
    return {n: last[n] - first.get(n, 0) for n in last if last[n] - first.get(n, 0) > 0}


def print_report(rows: list[dict], source: str) -> int:
    """打印峰值表 + 判定，返回退出码：0 绿 / 1 黄 / 2 红。"""
    if not rows:
        print("没有采样点，无法出报告。")
        return 2

    span = rows[-1]["elapsed_s"] - rows[0]["elapsed_s"]
    print()
    print("=" * 72)
    print(f"内存实测报告  |  {source}  |  {len(rows)} 个采样点 / {span:.0f}s")
    print("=" * 72)

    total = rows[0]["mem_total_mb"]
    min_avail = min(r["mem_available_mb"] for r in rows)
    min_avail_at = next(r for r in rows if r["mem_available_mb"] == min_avail)["elapsed_s"]
    peak_swap = max(r["swap_used_mb"] for r in rows)
    peak_cpu = max(r["cpu_pct_sum"] for r in rows)
    peak_containers = max(r["containers_total_mb"] for r in rows)

    print(f"物理内存        : {total:.0f} MB")
    print(f"容器合计峰值    : {peak_containers:.0f} MB")
    print(f"可用内存最低点  : {min_avail:.0f} MB  (t=+{min_avail_at:.0f}s)")
    print(f"swap 使用峰值   : {peak_swap:.0f} MB")
    print(f"容器 CPU 合计峰 : {peak_cpu:.0f}%  (2 核 = 200% 为满，含 docker stats 自身开销)")
    print()
    print("-- 各容器内存峰值 (MB) --")
    for name, mb in _peak_containers(rows):
        print(f"  {name:<24}: {mb:>8.1f}")

    deltas = _restart_deltas(rows)
    if deltas:
        print()
        print("-- ⚠ 容器重启计数增长（极可能被 OOM killer 杀过）--")
        for name, n in sorted(deltas.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {name:<24}: +{n}")

    print()
    verdict, code = _verdict(min_avail, peak_swap, deltas)
    print(f"判定：{verdict}")
    print("=" * 72)
    print("结论请连同上表一起记录到 docs/资源实测方案_2C4G.md。")
    return code


def _verdict(min_avail: float, peak_swap: float, deltas: dict[str, int]) -> tuple[str, int]:
    if deltas:
        names = "、".join(sorted(deltas))
        return (
            f"🔴 红 —— 采样期内有容器重启（{names}）。这是 OOM 的直接证据，"
            f"不是「偶发卡顿」。2C4G 跑不住完整栈，应升配或砍组件。",
            2,
        )
    if min_avail < HEADROOM_YELLOW_MB:
        return (
            f"🔴 红 —— 可用内存最低只剩 {min_avail:.0f} MB（阈值 {HEADROOM_YELLOW_MB} MB）。"
            f"已贴着上限，任何一次解析/召回/并发峰值都会直接把机器打崩，压测没有意义。",
            2,
        )
    if min_avail < HEADROOM_GREEN_MB:
        note = (
            f"🟡 黄 —— 可用内存最低只剩 {min_avail:.0f} MB（阈值 {HEADROOM_GREEN_MB} MB）。"
            f"能启动但没有缓冲：可以继续压测，但预期会打到红线，同时准备瘦身方案。"
        )
        if peak_swap > SWAP_WARN_MB:
            note += f" 且 swap 峰值 {peak_swap:.0f} MB，已经在靠 swap 续命（延迟会毛刺）。"
        return (note, 1)
    note = (
        f"🟢 绿 —— 可用内存最低 {min_avail:.0f} MB（阈值 {HEADROOM_GREEN_MB} MB），"
        f"采样期内无容器重启。可以进压测档位。"
    )
    if peak_swap > SWAP_WARN_MB:
        note += f" 但 swap 峰值 {peak_swap:.0f} MB 偏高，留意延迟毛刺。"
    return (note, 0)


# ── CLI ───────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="服务器内存/CPU 峰值采样（2C4G 容量判定）")
    ap.add_argument("--duration", type=float, default=1800, help="采样时长（秒），0 = 只采一次")
    ap.add_argument("--interval", type=float, default=3.0, help="采样间隔（秒）；docker stats 本身约 1-2s")
    ap.add_argument("--out", default="", help="JSONL 输出路径，默认 reports/probe-<时间戳>.jsonl")
    ap.add_argument("--report", default="", help="只出报告：读已有 JSONL，不采样")
    args = ap.parse_args()

    if args.report:
        path = Path(args.report)
        if not path.exists():
            print(f"找不到采样文件：{path}")
            return 2
        return print_report(_load(path), source=str(path))

    if not Path("/proc/meminfo").exists():
        print("这台机器没有 /proc/meminfo —— 本脚本只支持 Linux（在服务器上跑）。")
        return 2
    if shutil.which("docker") is None:
        print("找不到 docker CLI。先在服务器上把 stack 起起来再采。")
        return 2

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.out:
        out = Path(args.out)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = REPORTS_DIR / f"probe-{stamp}.jsonl"

    duration = max(args.duration, 0.0)
    started = time.monotonic()
    rows: list[dict] = []
    print(f"开始采样 → {out}  (时长 {duration:.0f}s / 间隔 {args.interval:.0f}s)")
    print("提示：压测请在另一个终端跑 scripts/load_test_chat.py，本脚本只负责看内存。")

    with out.open("w", encoding="utf-8") as fh:
        tick = 0
        try:
            while True:
                elapsed = time.monotonic() - started
                try:
                    row = take_sample(elapsed)
                except (RuntimeError, OSError) as exc:
                    print(f"  ⚠ 采样失败（t=+{elapsed:.0f}s）: {exc}")
                    if duration == 0:
                        return 2
                    time.sleep(args.interval)
                    continue

                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()

                if tick % 10 == 0:  # 每 10 个点报一次，避免刷屏
                    print(
                        f"  t=+{elapsed:6.0f}s  可用 {row['mem_available_mb']:7.0f} MB  "
                        f"容器 {row['containers_total_mb']:7.0f} MB  "
                        f"swap {row['swap_used_mb']:5.0f} MB  cpu {row['cpu_pct_sum']:5.0f}%"
                    )
                tick += 1

                if duration == 0 or elapsed >= duration:
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n手动中断，按已采到的样本出报告。")

    return print_report(rows, source=str(out))


if __name__ == "__main__":
    raise SystemExit(main())
