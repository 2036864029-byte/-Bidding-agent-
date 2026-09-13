"""Agent 轨迹评测跑批（E2-3）— 真实 deep_agent 调用在此层。

对样本逐条跑 **`build_deep_agent_node`（生产节点，非裸函数）**，收集 `data.trace`，
用 `evals.metrics.agent_trace` 汇总。并发（`workers`，主为 I/O）。

**带文件样本**（`files` 字段，见 `eval/file_golden_set.json`）：
`_prepare_file_session` 把 `eval/fixtures/<名>.md` 装进 filestore 并建立
**独立会话作用域**，再按 API 层的方式注入 `[文件：…]` 上下文 → 节点的文件名
可见性逻辑与 `read_file` 工具才走得通。上传时的显示名 = 去掉末尾 `.md`。

`expected_tools` / `expected_sequence`（gold，标注真值或弱标注）来源三层，人工只在最后一层：
  ① 样本自带（`expected_tools` / `expected_sequence`）；
  ② `derive_expected=True` 时按关键词规则派生 `expected_tools`（零人工，近似值）；
  ③ 人工标注（`eval/agent_golden_set.json` / `eval/file_golden_set.json`，含有序序列）。
不带 gold 也能得到全部客观轨迹指标（步数/工具频次/状态/反思/延迟）。

⚠️ 工具清单统一取 `deep_agent._default_tools()`——**不要在这里硬编码**：
曾经硬编码为 kb+price 两个，导致新增的 `read_file` 静默不被评测覆盖。

并发注意：deep_agent 的工具链会调 SiliconFlow reranker，`workers` 过大会触发 429
降级（见 S5 holdout 记录）；轨迹结构指标对重排质量不敏感，默认 workers=3。
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from evals.metrics.agent_trace import aggregate_traces, render_report, trace_summary

__all__ = ["derive_expected_tools", "load_agent_golden", "run_agent_trace"]

KB_TOOL = "kb_search_tool"
PRICE_TOOL = "price_query"

#: 带文件样本的 fixture 目录（`<名>.pdf.md` → 上传显示名 `<名>.pdf`）
FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "eval" / "fixtures"

# 弱规则：出现价格/中标类词才期望 price_query（近似，精确值需人工标注）
_PRICE_HINTS = (
    "价格", "报价", "中标价", "中标金额", "预算", "成交", "采购金额",
    "多少钱", "单价", "总价", "费用",
)


def load_agent_golden(path: Path | str) -> list[dict]:
    """装载 agent 金标（`eval/agent_golden_set.json` 或 `file_golden_set.json`）。

    每条转成 {question, query_type, expected_tools, expected_sequence, files?,
    answer_contains_any?}；金标里 `expected_tools` 即为 `expected_sequence`
    的集合版，评测时优先于规则派生。`files` 非空 → 走带文件路径（见模块文档）。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    return [
        {
            "question": it["q"],
            "query_type": it.get("query_type", ""),
            "expected_tools": it["expected_tools"],
            "expected_sequence": it["expected_sequence"],
            "files": list(it.get("files") or []),
            "answer_contains_any": list(it.get("answer_contains_any") or []),
        }
        for it in items
    ]


def derive_expected_tools(question: str, query_type: str = "") -> list[str]:
    """规则派生期望工具集合（零人工，弱标注；仅作近似）。"""
    tools = [KB_TOOL]
    if any(h in question for h in _PRICE_HINTS):
        tools.append(PRICE_TOOL)
    return tools


def _answer_hit(answer: str, groups: Sequence[Sequence[str]]) -> bool | None:
    """答案是否命中金标：每个 OR 组内任一写法出现即可（全组须命中）。

    返回 None 表示该样本未标注 `answer_contains_any`（不计入命中率分母）。
    """
    if not groups:
        return None
    text = answer or ""
    return all(any(form in text for form in group) for group in groups)


def _prepare_file_session(case: dict) -> list[str]:
    """把 fixture 装进 filestore + 建独立会话作用域 → 返回上传显示名列表。

    **每题一个新会话**：避免跨题污染（同时保证多文件题只看到自己的文件）。
    调用方须在同一线程内使用（会话作用域是 contextvar，线程池不复制）。
    """
    from agent.doc_context import chunk_record
    from core import filestore
    from core.config.settings import Settings
    from core.observability.context import reset_context, set_session_id, tenant_scoped

    reset_context()
    sid = f"fileeval-{uuid.uuid4().hex[:12]}"
    set_session_id(sid)
    scope = tenant_scoped(sid)   # 内部作用域 = tenant::sid，与 FileRecord / 工具查询同口径

    settings = Settings()
    names: list[str] = []
    for fname in case.get("files") or []:
        md = (FIXTURES_DIR / fname).read_text(encoding="utf-8")
        display = fname[:-3] if fname.endswith(".md") else fname
        rec = filestore.FileRecord(
            file_id=filestore.new_file_id(), source_name=display, source_format="pdf",
            markdown=md, preview=md[:60], chunk_count=0,
            session_id=scope, created_at=filestore.time.time(),
        )
        rec.chunk_count = len(chunk_record(rec, settings))
        filestore.put(rec)
        names.append(display)
    return names


def _run_one(case: dict) -> dict:
    """单题跑 deep_agent（生产节点路径），收 data.trace + 答案（每个 worker 独立建 llm/tools）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.nodes.doc_qa import DOC_CONTEXT_MARKER
    from agent.nodes.deep_agent import _default_tools, build_deep_agent_node
    from core.config.settings import Settings
    from core.llm.registry import create_llm_for_slot

    llm = create_llm_for_slot("deep_agent", Settings())
    tools = _default_tools()
    t0 = time.monotonic()
    try:
        names = _prepare_file_session(case) if case.get("files") else []
        messages = []
        if names:
            # 复刻 API 层的注入形状（api/graph 把文件名放进 marker SystemMessage）
            ctx = f"{DOC_CONTEXT_MARKER}\n" + "\n".join(f"[文件：{n}]" for n in names)
            messages.append(SystemMessage(content=ctx))
        messages.append(HumanMessage(content=case["question"]))
        out = build_deep_agent_node(llm=llm, tools=tools)(
            {"messages": messages}
        )["business_result"]
        answer = str(out.get("answer") or "")
        return {
            "question": case["question"],
            "query_type": case.get("query_type", ""),
            "answer": answer,
            "answer_hit": _answer_hit(answer, case.get("answer_contains_any") or []),
            "trace": (out.get("data") or {}).get("trace") or {},
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "expected_tools": case.get("expected_tools"),
            "expected_sequence": case.get("expected_sequence"),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001  单题失败降级
        return {
            "question": case["question"],
            "query_type": case.get("query_type", ""),
            "answer": "", "answer_hit": None,
            "trace": {}, "latency_ms": None,
            "expected_tools": case.get("expected_tools"),
            "expected_sequence": case.get("expected_sequence"),
            "error": f"{type(e).__name__}: {e}",
        }


def run_agent_trace(
    cases: Sequence[dict],
    *,
    workers: int = 3,
    derive_expected: bool = True,
    out_dir: Path,
) -> dict:
    """跑 deep_agent → 汇总轨迹指标，产物写 out_dir。

    Args:
        cases: [{question, query_type?, expected_tools?, expected_sequence?}]。
        workers: 并发（deep_agent 工具链打 SiliconFlow，过大触发 429）。
        derive_expected: 样本无 expected_tools 时按规则派生（弱标注）。
        out_dir: 产物目录（per_sample.jsonl / summary.json / report.md）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        rows = list(pool.map(_run_one, cases))
    elapsed = round(time.monotonic() - t0, 1)

    for r, c in zip(rows, cases):
        if not r.get("expected_tools") and derive_expected:
            r["expected_tools"] = derive_expected_tools(
                c["question"], c.get("query_type", ""))

    summary = aggregate_traces(rows)
    summary["elapsed_s"] = elapsed
    summary["errors"] = sum(1 for r in rows if r["error"])
    # 带文件集才有的答案命中率（未标注 answer_contains_any 的样本不入分母）
    hits = [r["answer_hit"] for r in rows if r.get("answer_hit") is not None]
    summary["answer_hit"] = {
        "n": len(hits),
        "hit": sum(1 for h in hits if h),
        "rate": round(sum(1 for h in hits if h) / len(hits), 4) if hits else None,
    }
    # 按 query_type 分组（带文件集的 5 类差异大，混在一起看不出问题）
    by_type: dict[str, dict] = {}
    for r in rows:
        qt = r.get("query_type") or "unknown"
        bucket = by_type.setdefault(qt, {"n": 0, "errors": 0, "answer_hit_n": 0, "answer_hit": 0})
        bucket["n"] += 1
        bucket["errors"] += 1 if r["error"] else 0
        if r.get("answer_hit") is not None:
            bucket["answer_hit_n"] += 1
            bucket["answer_hit"] += 1 if r["answer_hit"] else 0
    for bucket in by_type.values():
        n = bucket["answer_hit_n"]
        bucket["answer_hit_rate"] = round(bucket["answer_hit"] / n, 4) if n else None
    summary["by_query_type"] = by_type

    per_sample = [
        {"question": r["question"], "error": r["error"], "latency_ms": r["latency_ms"],
         "query_type": r.get("query_type", ""), "answer_hit": r.get("answer_hit"),
         "answer": (r.get("answer") or "")[:400],
         "summary": trace_summary(r["trace"], expected_tools=r["expected_tools"],
                                  expected_sequence=r["expected_sequence"])}
        for r in rows
    ]
    (out_dir / "per_sample.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in per_sample) + "\n",
        encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(summary), encoding="utf-8")
    return summary
