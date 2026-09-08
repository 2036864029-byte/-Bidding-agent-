"""Agent 链路 live 冒烟（Wave 0 收口后 / Wave 1 入口基线）。

覆盖：knowledge_qa / price_inquiry / general_chat / doc_qa / price→knowledge handoff。
每条用例打印 intent、branch、关键诊断字段与耗时，末尾汇总 PASS/FAIL，并把完整结果
写入 ``test_report/agent_live_smoke_<mode>.json``（该目录已 gitignore）。

用法：
    uv run python scripts/run_agent_live_smoke.py                 # 默认（AGENT_MODE=supervisor）
    AGENT_MODE=single USE_TOOL_KB=false uv run python scripts/run_agent_live_smoke.py

注意：``USE_TOOL_KB`` 与 ``AGENT_MODE`` 都在**导入期**读取，切换必须新起进程。

依赖真实 DeepSeek / Milvus / MySQL。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REPORT_DIR = PROJECT_ROOT / "test_report"


# ── 取样：从真实库里取一条项目编号与一个中标供应商，让用例可复现 ──
def _sample_from_db() -> tuple[str, str]:
    import pymysql

    from core.config.settings import Settings

    s = Settings()
    conn = pymysql.connect(
        host=s.mysql_host, user=s.mysql_user, password=s.mysql_password,
        port=s.mysql_port, database=s.mysql_clean_db, charset="utf8mb4",
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT project_number FROM bid_project "
                "WHERE project_number IS NOT NULL AND project_number <> '' "
                "ORDER BY id DESC LIMIT 1"
            )
            project_number = str(cur.fetchone()[0])
            cur.execute(
                "SELECT successful_bidder FROM bid_project "
                "WHERE successful_bidder IS NOT NULL AND successful_bidder <> '' "
                "ORDER BY id DESC LIMIT 1"
            )
            company = str(cur.fetchone()[0])
    finally:
        conn.close()
    return project_number, company


def _cases(project_number: str, company: str) -> list[dict]:
    return [
        {
            "name": "knowledge",
            "question": "招标方式有哪些？",
            "expect_branch": "knowledge_qa",
        },
        {
            "name": "knowledge_citation",
            "question": "评标委员会怎么组成？",
            "expect_branch": "knowledge_qa",
        },
        {
            "name": "price_project",
            "question": f"项目编号 {project_number} 的中标情况",
            "expect_branch": "price_inquiry",
        },
        {
            "name": "price_company",
            "question": f"{company}中标了哪些项目？",
            "expect_branch": "price_inquiry",
        },
        {
            "name": "general_chat",
            "question": "你好",
            "expect_branch": "general_chat",
        },
        {
            "name": "doc_qa_no_file",
            "question": "帮我分析这个文档",
            "expect_branch": "doc_qa",
        },
        {
            "name": "handoff_price_to_knowledge",
            "question": f"项目编号 {project_number} 的中标情况，中标通知书有什么法律效力？",
            "expect_branch": "knowledge_qa",
            "expect_handoff_context": True,
        },
    ]


def _summarize(case: dict, result: dict, elapsed: float) -> dict:
    br = result.get("business_result") or {}
    data = br.get("data") if isinstance(br.get("data"), dict) else {}
    answer = str(br.get("answer") or result.get("answer") or "")
    return {
        "name": case["name"],
        "question": case["question"],
        "intent": result.get("intent"),
        "branch": br.get("branch"),
        "elapsed_s": round(elapsed, 2),
        "answer_head": answer[:160],
        "answer_len": len(answer),
        "sources": len(data.get("sources") or []),
        "citations": len(data.get("citations") or []),
        "records": len(data.get("records") or []),
        "recall_trace": bool(data.get("retrieval_diagnostics")),
        "citation_all_passed": (
            (data.get("citation_validation") or {}).get("all_passed")
            if isinstance(data.get("citation_validation"), dict) else None
        ),
        "handoff_context": bool(data.get("handoff_context")),
        "status": data.get("status"),
        "error": data.get("error"),
    }


def _check(case: dict, row: dict, mode: str) -> tuple[bool | None, str]:
    """返回 (ok, why)；ok=None 表示本模式不适用（跳过）。"""
    if case.get("expect_handoff_context") and mode != "supervisor":
        return None, "handoff 仅 supervisor 模式（single 无 handoff_decider）"
    if row["branch"] != case["expect_branch"]:
        return False, f"branch={row['branch']} != {case['expect_branch']}"
    if not row["answer_len"]:
        return False, "answer 为空"
    if case.get("expect_handoff_context") and not row["handoff_context"]:
        return False, "缺少 handoff_context（跨 agent 组合终态未发生）"
    if case["name"].startswith("knowledge") and row["citations"] == 0:
        return False, "knowledge 分支无引用"
    if case["name"].startswith("price") and row["records"] == 0 and row["error"]:
        return False, f"price 分支报错: {row['error']}"
    return True, "ok"


def main() -> int:
    mode = os.getenv("AGENT_MODE", "supervisor")
    tool_kb = os.getenv("USE_TOOL_KB", "false")
    print(f"== agent live smoke ==  AGENT_MODE={mode}  USE_TOOL_KB={tool_kb}", flush=True)

    project_number, company = _sample_from_db()
    print(f"sample project_number={project_number!r} company={company!r}\n", flush=True)

    from agent import AgentGraph

    t0 = time.time()
    agent = AgentGraph()
    print(f"AgentGraph 就绪 ({time.time() - t0:.1f}s)\n", flush=True)

    rows: list[dict] = []
    passed = 0
    skipped = 0
    for case in _cases(project_number, company):
        print("-" * 78, flush=True)
        print(f"[{case['name']}] {case['question']}", flush=True)
        t = time.time()
        try:
            result = agent.invoke(case["question"], thread_id=f"smoke-{case['name']}")
        except Exception as exc:  # 冒烟脚本不中断，记录后继续
            elapsed = time.time() - t
            print(f"  EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
            rows.append({
                "name": case["name"], "question": case["question"],
                "intent": None, "branch": None, "elapsed_s": round(elapsed, 2),
                "answer_head": "", "answer_len": 0, "sources": 0, "citations": 0,
                "records": 0, "recall_trace": False, "citation_all_passed": None,
                "handoff_context": False, "status": None, "error": str(exc)[:200],
            })
            continue

        elapsed = time.time() - t
        row = _summarize(case, result, elapsed)
        ok, why = _check(case, row, mode)
        if ok is True:
            passed += 1
        elif ok is None:
            skipped += 1
        rows.append(row)
        verdict = "PASS" if ok is True else ("SKIP: " + why if ok is None else "FAIL: " + why)
        print(f"  branch={row['branch']} intent={row['intent']} {elapsed:.1f}s "
              f"citations={row['citations']} records={row['records']} "
              f"recall_trace={row['recall_trace']} -> {verdict}",
              flush=True)
        print(f"  answer: {row['answer_head']}", flush=True)

    print("\n" + "=" * 78, flush=True)
    print(f"结果：{passed}/{len(rows) - skipped} 通过，{skipped} 跳过"
          f"（AGENT_MODE={mode} USE_TOOL_KB={tool_kb}）", flush=True)
    for row in rows:
        ok, why = _check(next(c for c in _cases(project_number, company)
                              if c["name"] == row["name"]), row, mode)
        if ok is False:
            print(f"  FAIL {row['name']}: {why}", flush=True)

    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"agent_live_smoke_{mode}_{tool_kb}.json"
    out.write_text(json.dumps(
        {"mode": mode, "use_tool_kb": tool_kb, "passed": passed,
         "skipped": skipped, "total": len(rows), "rows": rows},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    print(f"报告已写入 {out}", flush=True)
    return 0 if passed + skipped == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
