"""Langfuse 观测看板初始化（E4-3c）。

流程（实测的 unstable API 契约）：
  1. `POST /api/public/unstable/dashboard-widgets` 逐个建 widget 定义 → 拿 id
  2. `POST /api/public/unstable/dashboards` 建看板，definition.widgets 引用
     {type:"widget", id:<布局 id>, widgetId:<定义 id>, x/y/width/height}
幂等：同名看板存在时，先删其 widget 定义与看板，再重建。

注：Langfuse 标该 API 为 unstable（可能随版本变动）。

用法：
    python scripts/setup_langfuse_dashboard.py --dry-run
    python scripts/setup_langfuse_dashboard.py
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DASHBOARD_NAME = "招投标智能助手 · 观测总览"

#: 入口根 span（API / CLI）；「请求量 / 端到端延迟」只统计整次请求
_ROOT_FILTER = {
    "type": "stringOptions", "column": "name", "operator": "any of",
    "value": ["POST /chat", "cli.chat"],
}

WIDGETS = [
    {"name": "请求量趋势", "view": "observations", "dimensions": [],
     "metrics": [{"measure": "count", "agg": "count"}],
     "filters": [_ROOT_FILTER], "chartType": "LINE_TIME_SERIES"},
    {"name": "端到端延迟 p95", "view": "observations", "dimensions": [],
     "metrics": [{"measure": "latency", "agg": "p95"}],
     "filters": [_ROOT_FILTER], "chartType": "LINE_TIME_SERIES"},
    {"name": "成本趋势（CNY）", "view": "observations", "dimensions": [],
     "metrics": [{"measure": "totalCost", "agg": "sum"}],
     "filters": [], "chartType": "LINE_TIME_SERIES"},
    {"name": "Token 消耗", "view": "observations", "dimensions": [],
     "metrics": [{"measure": "inputTokens", "agg": "sum"},
                 {"measure": "outputTokens", "agg": "sum"}],
     "filters": [], "chartType": "LINE_TIME_SERIES"},
    {"name": "分层延迟（按 span 名）", "view": "observations",
     "dimensions": [{"field": "name"}],
     "metrics": [{"measure": "latency", "agg": "avg"}],
     "filters": [], "chartType": "LINE_TIME_SERIES"},
    {"name": "质量分趋势（按 score 名）", "view": "scores-numeric",
     "dimensions": [{"field": "name"}],
     "metrics": [{"measure": "value", "agg": "avg"}],
     "filters": [], "chartType": "LINE_TIME_SERIES"},
]


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Langfuse 观测看板初始化")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print(f"[dry] 将重建看板「{DASHBOARD_NAME}」，widget {len(WIDGETS)} 个：")
        for i, w in enumerate(WIDGETS):
            print(f"  - [{i % 2 * 6},{i // 2 * 6}] {w['name']} ({w['view']}/{w['chartType']})")
        return

    import requests

    host, pub, sec = _load_env()
    if not pub or not sec:
        raise SystemExit("缺少 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY（见 .env）")
    auth = (pub, sec)
    api = f"{host}/api/public/unstable"

    # 1. 幂等清理：删同名看板及其 widget 定义
    dashes = requests.get(f"{api}/dashboards", auth=auth, timeout=20).json().get("data", [])
    for d in dashes:
        if d.get("name") != DASHBOARD_NAME:
            continue
        for w in (d.get("definition") or {}).get("widgets", []):
            if w.get("widgetId"):
                requests.delete(f"{api}/dashboard-widgets/{w['widgetId']}", auth=auth, timeout=20)
        requests.delete(f"{api}/dashboards/{d['id']}", auth=auth, timeout=20)
        print(f"清理旧看板 {d['id']}")

    # 2. 建 widget 定义
    placed = []
    for i, spec in enumerate(WIDGETS):
        r = requests.post(f"{api}/dashboard-widgets", json=spec, auth=auth, timeout=20)
        if r.status_code not in (200, 201):
            raise SystemExit(f"建 widget「{spec['name']}」失败 HTTP {r.status_code}: {r.text[:300]}")
        wid = r.json()["id"]
        placed.append({
            "type": "widget", "id": str(uuid.uuid4()), "widgetId": wid,
            "x": (i % 2) * 6, "y": (i // 2) * 6, "width": 6, "height": 6,
        })
        print(f"  + widget「{spec['name']}」 {wid}")

    # 3. 建看板
    body = {
        "name": DASHBOARD_NAME,
        "description": "E4-3 观测总览：请求量 / 延迟 / 成本 / Token / 分层延迟 / 质量分"
                       "（由 scripts/setup_langfuse_dashboard.py 生成）",
        "definition": {"widgets": placed},
    }
    r = requests.post(f"{api}/dashboards", json=body, auth=auth, timeout=20)
    print(f"[create] 「{DASHBOARD_NAME}」 -> HTTP {r.status_code}")
    if r.status_code not in (200, 201):
        print(r.text[:400])
        return
    print(f"  id={r.json().get('id')}")
    # 看板 URL 在**项目作用域**下（顶层 /dashboards 是 404）
    projects = requests.get(f"{host}/api/public/projects", auth=auth, timeout=20).json().get("data", [])
    pid = projects[0]["id"] if projects else "<projectId>"
    print(f"  打开: {host}/project/{pid}/dashboards")


if __name__ == "__main__":
    main()
