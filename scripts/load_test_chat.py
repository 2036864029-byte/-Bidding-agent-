"""并发压测 CLI — 打 N 并发到 /chat，统计延迟分位与错误分布。

用途（方案 C8）：标定 `core/concurrency/limits.py` 的全局在飞闸门
（`Settings.max_inflight_requests`，默认 32 —— 该值是估值，需实测校准），
并观察过载时的降级行为是否如设计（503 + Retry-After，而非无限排队或雪崩）。

输出：
  - 成功率 / HTTP 状态分布 / 业务错误码分布
  - 延迟分位 p50 / p90 / p95 / p99（总体 + 按问题类型）
  - "闸门生效"证据：503（`sys_2004`）计数

用法：
    # 1) 另开终端起服务
    #    python -m api.server          （默认 127.0.0.1:8000）
    # 2) 打并发
    python scripts/load_test_chat.py --concurrency 20 --total 40
    python scripts/load_test_chat.py --concurrency 100 --total 200 --endpoint stream

注意：
  - **真实调用 LLM**（DeepSeek），产生费用与上游配额消耗；先用小 --total 试。
  - 本地开发经 SSH 隧道调远端模型，隧道会额外增加延迟 → **绝对延迟数字要打折看**，
    横向对比（不同并发档位之间）才有意义。
  - 需要 `NO_PROXY` 或 `trust_env=False`（脚本已内置后者），否则 Windows 系统代理会劫持。
  - **⚠ 必须区分客户端，否则测的是限流器而不是容量**：入口限流
    （`api/middleware/rate_limit.py`）按**客户端键**限流（默认 burst=20 / 60 每分钟），
    键取 `X-Tenant-ID` → `X-Forwarded-For` 首跳 → 直连 IP。从同一台机器压测时所有请求
    同键 → 很快被 429 淹没，**并发数一高就全是 429，根本触不到真实容量**。
    因此默认 `--clients 100`：给每个合成客户端发不同的 `X-Forwarded-For`，模拟多用户。
    想看"单客户端被限流"的效果就传 `--clients 0`。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 默认问题集：覆盖三条主要链路（知识问答 / 价格查询 / 兜底）
DEFAULT_QUESTIONS: list[tuple[str, str]] = [
    ("kb", "招标方式有哪些？"),
    ("kb", "投标保证金不得超过招标项目估算价的多少？"),
    ("kb", "中标通知书发出后多少日内签订合同？"),
    ("kb", "评标委员会成员人数有什么要求？"),
    ("price", "有哪些公司中标了？"),
]


@dataclass
class Sample:
    """单次请求的观测结果。"""

    qtype: str
    status: int                      # HTTP 状态；0 = 传输层异常
    error_code: str | None
    latency: float                   # 秒
    ok: bool


@dataclass
class Report:
    samples: list[Sample] = field(default_factory=list)

    def add(self, s: Sample) -> None:
        self.samples.append(s)


def _percentile(values: list[float], p: float) -> float:
    """线性插值分位（p ∈ [0,1]）；values 需非空。"""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = p * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def _post_chat(client: httpx.Client, url: str, question: str,
               headers: dict[str, str], timeout: float) -> tuple[int, str | None]:
    """POST /chat，返回 (status, 业务错误码)。"""
    r = client.post(url, json={"question": question}, headers=headers, timeout=timeout)
    code: str | None = None
    try:
        body: Any = r.json()
        code = body.get("error_code") or (body.get("error") or {}).get("code")
    except Exception:  # noqa: BLE001  非 JSON 响应
        code = None
    return r.status_code, code


def _post_stream(client: httpx.Client, url: str, question: str,
                 headers: dict[str, str], timeout: float) -> tuple[int, str | None]:
    """POST /chat/stream（SSE），返回 (status, 业务错误码)。

    逐帧读完即算完成；`error` 事件里的 code 作为业务错误码。
    """
    code: str | None = None
    with client.stream(
        "POST", url, json={"question": question, "stream": True},
        headers=headers, timeout=timeout,
    ) as resp:
        status = resp.status_code
        for line in resp.iter_lines():
            if line.startswith("data:") and "sys_" in line:
                try:
                    payload = json.loads(line[5:].strip())
                    code = (payload.get("error") or {}).get("code") or code
                except Exception:  # noqa: BLE001
                    pass
    return status, code


def _headers_for(idx: int, args: argparse.Namespace) -> dict[str, str]:
    """构造本次请求的头 —— 按 ``--clients`` 给不同的合成客户端身份。

    入口限流按**客户端键**限流（`X-Tenant-ID` → `X-Forwarded-For` 首跳 → 直连 IP）。
    同机压测若不区分客户端，所有请求同键 → 全被 429，测不到真实容量。
    """
    if args.clients <= 0:
        return args.headers
    cid = idx % args.clients
    return {**args.headers, "X-Forwarded-For": f"10.{cid // 256}.{cid % 256}.1"}


def _worker(idx: int, args: argparse.Namespace, report: Report,
            questions: list[tuple[str, str]]) -> None:
    """单并发槽位：跑分配到的请求。"""
    qtype, question = questions[idx % len(questions)]
    url = f"{args.base_url}{'/chat/stream' if args.endpoint == 'stream' else '/chat'}"
    headers = _headers_for(idx, args)
    t0 = time.perf_counter()
    status, code = 0, None
    try:
        with httpx.Client(trust_env=False) as client:
            if args.endpoint == "stream":
                status, code = _post_stream(client, url, question, headers, args.timeout)
            else:
                status, code = _post_chat(client, url, question, headers, args.timeout)
    except Exception as exc:  # noqa: BLE001  传输层异常（超时/连接失败）
        status, code = 0, f"{type(exc).__name__}"
    latency = time.perf_counter() - t0
    ok = status == 200 and not (code or "").startswith(("sys_", "dep_", "model_"))
    report.add(Sample(qtype=qtype, status=status, error_code=code,
                      latency=latency, ok=ok))


def _print_report(report: Report, args: argparse.Namespace, wall: float) -> int:
    s = report.samples
    total = len(s)
    ok = sum(1 for x in s if x.ok)
    lat = [x.latency for x in s]
    ok_lat = [x.latency for x in s if x.ok]

    print()
    print("=" * 64)
    print(f"并发={args.concurrency}  总请求={args.total}  端点={args.endpoint}")
    print("=" * 64)
    print(f"成功率          : {ok}/{total} ({ok / total * 100:.1f}%)")
    print(f"墙钟耗时        : {wall:.1f}s   吞吐 {total / wall:.2f} req/s")
    print(f"延迟 (全部)     : p50={_percentile(lat, .5):.2f}s  "
          f"p90={_percentile(lat, .9):.2f}s  "
          f"p95={_percentile(lat, .95):.2f}s  "
          f"p99={_percentile(lat, .99):.2f}s  max={max(lat):.2f}s")
    if ok_lat:
        print(f"延迟 (仅成功)   : p50={_percentile(ok_lat, .5):.2f}s  "
              f"p95={_percentile(ok_lat, .95):.2f}s  "
              f"mean={statistics.fmean(ok_lat):.2f}s")

    print()
    print("-- HTTP 状态分布 --")
    by_status: dict[int, int] = {}
    for x in s:
        by_status[x.status] = by_status.get(x.status, 0) + 1
    for st, n in sorted(by_status.items()):
        label = {0: "传输异常", 200: "OK", 429: "限流", 503: "闸门打满"}.get(st, "")
        print(f"  {st:>3} {label:<8}: {n}")

    codes = [x.error_code for x in s if x.error_code]
    if codes:
        print()
        print("-- 业务错误码分布 --")
        for c, n in sorted({c: codes.count(c) for c in set(codes)}.items()):
            print(f"  {c:<12}: {n}")

    print()
    print("-- 按问题类型 --")
    for t in sorted({x.qtype for x in s}):
        sub = [x for x in s if x.qtype == t]
        sub_ok = [x for x in sub if x.ok]
        p95 = _percentile([x.latency for x in sub_ok], .95) if sub_ok else 0.0
        print(f"  {t:<6}: {len(sub_ok)}/{len(sub)} 成功, 成功延迟 p95={p95:.2f}s")

    # 闸门生效判据：并发超过闸门上限时，应看到 503 而不是雪崩/超时
    busy = by_status.get(503, 0)
    if busy:
        print()
        print(f"✓ 观察到 {busy} 次 503（闸门打满 → 快速失败），符合 C1 设计")
    else:
        limit = _configured_gate_limit()
        if limit and args.concurrency > limit:
            print()
            print(f"⚠ 并发 {args.concurrency} 已超过配置的闸门 {limit}，但未观察到 503 —— "
                  f"可能服务端闸门被调高、或请求未真正并发")
    return 0 if ok == total else 1


def _configured_gate_limit() -> int:
    """本机配置的闸门上限（仅作提示；服务端实际值以它自己的 .env 为准）。"""
    try:
        from core.config.settings import Settings

        return int(Settings().max_inflight_requests)
    except Exception:  # noqa: BLE001  取不到就不提示
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description="并发压测 /chat（C8）")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--concurrency", type=int, default=10, help="并发槽位数")
    p.add_argument("--total", type=int, default=0, help="总请求数（默认 = 并发数）")
    p.add_argument("--endpoint", choices=("chat", "stream"), default="chat")
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--api-key", default="", help="API_KEYS 非空时必填")
    p.add_argument("--clients", type=int, default=100,
                   help="合成客户端数（各自不同的 X-Forwarded-For）；0 = 全部同一客户端（看限流效果）")
    p.add_argument("--warmup", type=int, default=1, help="预热请求数（不计入统计）")
    p.add_argument("--questions-file", default="",
                   help="可选：每行一个问题，覆盖默认问题集")
    args = p.parse_args()

    total = args.total or args.concurrency
    questions = DEFAULT_QUESTIONS
    if args.questions_file:
        lines = [ln.strip() for ln in open(args.questions_file, encoding="utf-8") if ln.strip()]
        questions = [("kb", ln) for ln in lines] or DEFAULT_QUESTIONS
    args.headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}

    print(f"压测目标 {args.base_url} | 并发 {args.concurrency} | 总数 {total} | "
          f"端点 {args.endpoint} | 合成客户端 {args.clients or '单一'}")
    if args.warmup:
        print(f"预热 {args.warmup} 次...")
        for i in range(args.warmup):
            try:
                with httpx.Client(trust_env=False) as c:
                    _post_chat(c, f"{args.base_url}/chat", questions[i % len(questions)][1],
                               args.headers, args.timeout)
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠ 预热失败（服务没起？）: {type(exc).__name__}: {exc}")
                return 2
        print("预热完成")

    report = Report()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(lambda i: _worker(i, args, report, questions), range(total)))
    return _print_report(report, args, time.perf_counter() - t0)


if __name__ == "__main__":
    raise SystemExit(main())
