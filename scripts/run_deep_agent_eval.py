"""Deep Agent 轨迹消费评测脚本（W3-3 验收②：轨迹可被评测脚本消费）。

逐条经 ``run_deep_agent`` 走真实链路（DeepSeek 规划/综合/反思 + 真实
kb_search / price_query 工具），对每条结果按 **data.trace** 归因：

  - plan 步骤数（复杂问题应 ≥2 步拆解）与工具分布；
  - 每步 observation 的 status / records_count（是否真正取到数据）；
  - reflection 结论（adequate/revised/gap）。

结果增量写入 ``test_report/deep_agent_results.jsonl``（断点续跑），并生成
汇总报告 ``test_report/deep_agent_report.md``。

用法：
    python scripts/run_deep_agent_eval.py                # 默认问题集
    python scripts/run_deep_agent_eval.py --limit 2      # 冒烟（前 2 条）
    python scripts/run_deep_agent_eval.py --questions "q1" "q2"  # 自定义
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from core.config.settings import Settings  # noqa: E402
from core.llm.registry import create_llm_for_slot  # noqa: E402

OUT_DIR = PROJECT_ROOT / "test_report"
OUT_FILE = OUT_DIR / "deep_agent_results.jsonl"
REPORT_FILE = OUT_DIR / "deep_agent_report.md"

DEFAULT_QUESTIONS = [
    "公开招标的适用条件是什么？",
    "某科技有限公司最近中标了项目，这些项目涉及哪些法律风险？",
    "评标委员会如何组成？",
    "企业被行政处罚后还能参与招投标吗？",
    "某公司有不良记录吗？",
]


def _run_one(question: str) -> dict:
    from agent.nodes.deep_agent import run_deep_agent
    from public_kb.tools import kb_search_as_tool
    from price_engine.tools import price_query_as_tool

    llm = create_llm_for_slot("deep_agent", Settings())
    tools = [kb_search_as_tool(), price_query_as_tool()]
    started = time.monotonic()
    result = run_deep_agent(question, llm=llm, tools=tools)
    latency_ms = int((time.monotonic() - started) * 1000)

    trace = result["trace"]
    plan = trace.get("plan", [])
    steps = trace.get("steps", [])
    reflection = trace.get("reflection") or {}
    return {
        "question": question,
        "answer": result["answer"],
        "draft_answer": result["draft_answer"],
        "latency_ms": latency_ms,
        "trace": {
            "mode": trace.get("mode"),
            "rounds": trace.get("rounds"),
            "plan_steps": len(plan),
            "plan": [
                {"id": p.get("id"), "tool": p.get("tool"), "query": p.get("query")}
                for p in plan
            ],
            "steps": [
                {"step_id": s.get("step_id"), "tool": s.get("tool"),
                 "status": s.get("status"), "records_count": s.get("records_count"),
                 "observation_head": s.get("observation_head", "")[:200]}
                for s in steps
            ],
            "reflection": {
                "verdict": reflection.get("verdict"),
                "notes": reflection.get("notes", ""),
            },
        },
    }


def _aggregate(results: list[dict]) -> dict:
    total = len(results)
    plans = [r["trace"]["plan_steps"] for r in results]
    multi = sum(1 for n in plans if n >= 2)
    verdicts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    status_by_step: dict[str, int] = {}
    for r in results:
        v = r["trace"]["reflection"]["verdict"]
        verdicts[v] = verdicts.get(v, 0) + 1
        for s in r["trace"]["steps"]:
            tool_counts[s["tool"]] = tool_counts.get(s["tool"], 0) + 1
            status_by_step[s["status"]] = status_by_step.get(s["status"], 0) + 1
    return {
        "total": total,
        "plan_steps_dist": {f"{n}步": plans.count(n) for n in sorted(set(plans))},
        "multi_step_questions": multi,
        "multi_step_ratio": round(multi / total, 3) if total else 0.0,
        "reflection_verdicts": verdicts,
        "tool_usage": tool_counts,
        "step_status": status_by_step,
        "avg_latency_ms": round(sum(r["latency_ms"] for r in results) / total) if total else 0,
    }


def _write_report(agg: dict) -> Path:
    lines = [
        "# Deep Agent 轨迹评测报告",
        "",
        f"- 样本数：**{agg['total']}**（生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}）",
        f"- 平均耗时：{agg['avg_latency_ms']} ms",
        "",
        "## 规划（Plan-and-Solve）",
        "",
        f"- 规划步骤分布：{agg['plan_steps_dist']}",
        f"- 复杂问题（≥2 步）占比：**{agg['multi_step_ratio']:.1%}**"
        f"（{agg['multi_step_questions']}/{agg['total']}）",
        "",
        "## 执行（Observation）",
        "",
        f"- 工具使用：{agg['tool_usage']}",
        f"- 步骤状态：{agg['step_status']}",
        "",
        "## 反思（Reflection）",
        "",
        f"- 结论分布：{agg['reflection_verdicts']}",
        "",
        "> 详细轨迹见 `deep_agent_results.jsonl`（每行含 plan/steps/reflection，评测脚本可继续消费）。",
    ]
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return REPORT_FILE


def main() -> None:
    parser = argparse.ArgumentParser(description="Deep Agent 轨迹评测")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    parser.add_argument("--questions", nargs="*", default=None, help="自定义问题集")
    args = parser.parse_args()

    questions = args.questions or DEFAULT_QUESTIONS
    if args.limit > 0:
        questions = questions[: args.limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done_ids = set()
    if OUT_FILE.exists():
        for line in OUT_FILE.read_text(encoding="utf-8").splitlines():
            try:
                done_ids.add(json.loads(line)["question"])
            except (json.JSONDecodeError, KeyError):
                continue

    results = []
    with OUT_FILE.open("a", encoding="utf-8") as fh:
        for q in questions:
            if q in done_ids:
                print(f"[skip] 已存在: {q[:40]}")
                continue
            print(f"[run ] {q}")
            row = _run_one(q)
            row["_run_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            results.append(row)

    if not results:
        # 全部跳过时也读回已存结果做聚合
        if OUT_FILE.exists():
            for line in OUT_FILE.read_text(encoding="utf-8").splitlines():
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        print("无新运行样本（全部命中断点续跑缓存），从已有结果聚合。")

    agg = _aggregate(results)
    report = _write_report(agg)
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"报告: {report}")


if __name__ == "__main__":
    main()
