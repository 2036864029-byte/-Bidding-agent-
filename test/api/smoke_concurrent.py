"""并发冒烟（live 手动，方案 Phase 2 / C-9、C-10）— 10 并发不同 session 无串扰。

需先启动服务： python -m api.server  （默认 127.0.0.1:8000，可 --url 覆盖）

用法：
    python test/api/smoke_concurrent.py            # 10 并发 /chat，各 session 独立
    python test/api/smoke_concurrent.py --url http://127.0.0.1:8000 --n 10
退出码非 0 = 有请求失败。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sys

import httpx


def _hit(url: str, session_id: str) -> tuple[str, int, str]:
    try:
        r = httpx.post(f"{url}/chat", json={"question": "你好", "session_id": session_id}, timeout=60)
        return session_id, r.status_code, (r.json().get("answer") or "")[:20]
    except Exception as e:  # noqa: BLE001
        return session_id, -1, str(e)[:80]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()

    sessions = [f"sess-{i}" for i in range(args.n)]
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.n) as ex:
        futures = [ex.submit(_hit, args.url, s) for s in sessions]
        for fut in concurrent.futures.as_completed(futures):
            sid, code, ans = fut.result()
            ok = code == 200 and bool(ans)
            print(f"{sid}: http={code} ok={ok} answer[:20]={ans!r}")
            failures += 0 if ok else 1

    print(f"\n汇总: {args.n} 请求, 失败 {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
