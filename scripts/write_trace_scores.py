"""Langfuse 质量分回写（E4-3b）— 让看板能看质量趋势，而非只有延迟/成本。

对近期 trace 计算**确定性**质量信号（零额外 LLM 调用），经 `POST /api/public/scores`
写回 Langfuse：
  - answered             答案非空且非降级 = 1，否则 0
  - single_branch_complex 多源问题仅走单分支 = 1（E5 路由关注点），否则 0

分数用确定性 id（trace+name 派生）→ 重复运行幂等（同 id 覆盖）。

用法：
    python scripts/write_trace_scores.py --since-hours 24
    python scripts/write_trace_scores.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.trace_feedback import (  # noqa: E402
    FLAG_EMPTY,
    FLAG_FALLBACK,
    FLAG_MULTI_ATOM,
    extract_question,
    fetch_root_traces,
    fetch_trace_events,
    flag_root,
    load_ch_cfg,
)


def _load_env() -> tuple[str, str, str]:
    env = {}
    for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return (
        env.get("LANGFUSE_HOST", "http://localhost:3000").rstrip("/"),
        env.get("LANGFUSE_PUBLIC_KEY", ""),
        env.get("LANGFUSE_SECRET_KEY", ""),
    )


def _score_id(trace_id: str, name: str) -> str:
    return "ztb-" + hashlib.sha1(f"{trace_id}:{name}".encode()).hexdigest()[:20]


def _scores_for(root: dict, events: list[dict]) -> list[dict]:
    flags = flag_root(root, events)
    answered = 0.0 if (FLAG_EMPTY in flags or FLAG_FALLBACK in flags) else 1.0
    return [
        {"name": "answered", "value": answered,
         "comment": f"flags={','.join(flags) or 'none'}"},
        {"name": "single_branch_complex", "value": 1.0 if FLAG_MULTI_ATOM in flags else 0.0,
         "comment": "多源问题仅走单分支（E5 路由关注点）" if FLAG_MULTI_ATOM in flags else ""},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Langfuse 质量分回写")
    parser.add_argument("--since-hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--root-name", default="", help="根 span 名前缀过滤（默认不过滤，含 CLI）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import requests

    host, pub, sec = _load_env()
    if not pub or not sec:
        raise SystemExit("缺少 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY（见 .env）")
    auth = (pub, sec)

    cfg = load_ch_cfg()
    roots = fetch_root_traces(cfg, since_hours=args.since_hours, limit=args.limit,
                              root_name=args.root_name)
    n_scores = 0
    for root in roots:
        events = fetch_trace_events(cfg, root["trace_id"])
        if not extract_question(events):   # 无问题文本的 trace（如健康检查）跳过
            continue
        for s in _scores_for(root, events):
            body = {
                "id": _score_id(root["trace_id"], s["name"]),
                "traceId": root["trace_id"],
                "name": s["name"],
                "value": s["value"],
                "dataType": "NUMERIC",
                "comment": s["comment"][:200],
            }
            if args.dry_run:
                print(f"[dry] {root['trace_id'][:12]} {s['name']}={s['value']}")
                n_scores += 1
                continue
            r = requests.post(f"{host}/api/public/scores", json=body, auth=auth, timeout=20)
            if r.status_code in (200, 201):
                n_scores += 1
            else:
                print(f"[warn] score 写入失败 {r.status_code}: {r.text[:150]}")
    print(f"完成：trace {len(roots)} 条，写入 {n_scores} 个分数"
          + ("（dry-run）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
