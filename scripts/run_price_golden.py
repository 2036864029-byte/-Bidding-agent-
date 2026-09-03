"""price golden 验证脚本（P3 L3 门禁：price golden 指标不降）。

对 eval/price_golden_set.json 逐题**直接调 price_inquiry 节点**（不经 router，
省一次 LLM 调用，隔离 price 行为），记录确定性摘要：sub_route / query_type /
行数 / 首行签名，落 json 到 test_report/。用于 P3 引擎下沉 / RecallPipeline
改造前后对拍：before/after 逐题一致 = 行为零漂移。

用法:
    python scripts/run_price_golden.py --out test_report/price_golden_after.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage

from agent.nodes.price_inquiry.node import node_price_inquiry


def _row_signature(record: dict) -> str:
    """首行稳定签名：取有序前 10 个字段的 json 摘要。"""
    keys = sorted(record.keys())[:10]
    return json.dumps({k: record[k] for k in keys if record[k] is not None},
                      ensure_ascii=False, default=str)[:400]


def _summarize(br: dict) -> dict:
    data = br.get("data") or {}
    intent = data.get("intent") or {}
    records = data.get("records") or []
    sig = ""
    if records and isinstance(records[0], dict):
        sig = _row_signature(records[0])
    elif records:
        sig = json.dumps(records[0], ensure_ascii=False, default=str)[:200]
    return {
        "sub_route": intent.get("sub_route") or data.get("sub_route"),
        "query_type": intent.get("query_type") or data.get("query_type"),
        "branch": br.get("branch"),
        "n_records": len(records) if isinstance(records, list) else None,
        "tables": data.get("tables") or [],
        "first_sig": sig,
        "answer_head": (br.get("answer") or "")[:60],
    }


def run(path: Path, out: Path) -> None:
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    rows = []
    for it in items:
        try:
            result = node_price_inquiry({"messages": [HumanMessage(content=it["q"])]})
            summary = _summarize(result.get("business_result") or {})
        except Exception as e:  # 节点抛错（应被 _with_fallback 兜住，此处直接记录）
            summary = {"error": str(e)[:200]}
        rows.append({"q": it["q"], **summary})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== {path.name} → {out}")
    for r in rows:
        print(f"   [{r.get('sub_route') or r.get('branch') or 'ERR':<15}] {r.get('query_type','')} rows={r.get('n_records')} | {r['q']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--set", type=Path, default=ROOT / "eval" / "price_golden_set.json")
    p.add_argument("--out", type=Path, default=ROOT / "test_report" / "price_golden_after.json")
    args = p.parse_args()
    run(args.set, args.out)


if __name__ == "__main__":
    main()
