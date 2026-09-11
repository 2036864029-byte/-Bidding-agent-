"""Langfuse 模型定价初始化（E4-3a）— 让成本维度有数。

读 `deploy/langfuse/model_prices.json` → 经 `POST/PUT /api/public/models` 幂等写入
Langfuse。幂等：按 modelName 匹配，已存在则更新，否则创建。

用法：
    python scripts/setup_langfuse_pricing.py            # 应用定价
    python scripts/setup_langfuse_pricing.py --dry-run  # 只打印不写
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PRICES = PROJECT_ROOT / "deploy" / "langfuse" / "model_prices.json"


def _load_env() -> tuple[str, str, str]:
    """从项目 .env 读 Langfuse 连接信息。"""
    env = {}
    path = PROJECT_ROOT / ".env"
    for line in path.read_text(encoding="utf-8").splitlines():
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
    parser = argparse.ArgumentParser(description="Langfuse 模型定价初始化")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--config", type=Path, default=PRICES)
    args = parser.parse_args()

    import requests

    host, pub, sec = _load_env()
    if not pub or not sec:
        raise SystemExit("缺少 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY（见 .env）")
    auth = (pub, sec)

    data = json.loads(args.config.read_text(encoding="utf-8"))
    wanted = data["models"]

    existing = requests.get(f"{host}/api/public/models", auth=auth, timeout=20).json().get("data", [])
    by_name = {m.get("modelName"): m for m in existing}

    for m in wanted:
        body = {k: v for k, v in m.items() if not k.startswith("_")}
        cur = by_name.get(m["modelName"])
        if args.dry_run:
            print(f"[dry] {'update' if cur else 'create'} {m['modelName']} "
                  f"in={m['inputPrice']}/out={m['outputPrice']}")
            continue
        if cur:
            r = requests.put(f"{host}/api/public/models/{cur['id']}", json=body, auth=auth, timeout=20)
            action = "update"
        else:
            r = requests.post(f"{host}/api/public/models", json=body, auth=auth, timeout=20)
            action = "create"
        ok = r.status_code in (200, 201)
        print(f"[{action}] {m['modelName']} -> HTTP {r.status_code}"
              + ("" if ok else f" {r.text[:200]}"))

    if not args.dry_run:
        models = requests.get(f"{host}/api/public/models", auth=auth, timeout=20).json().get("data", [])
        hit = [x for x in models if x.get("modelName") == "deepseek-chat"]
        if hit:
            h = hit[0]
            print(f"\n已生效: {h['modelName']} in={h['inputPrice']} out={h['outputPrice']} "
                  f"unit={h['unit']} pattern={h['matchPattern']}")


if __name__ == "__main__":
    main()
