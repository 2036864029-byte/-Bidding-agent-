"""多轮对话评测跑批（多轮基座 2026-09-16）。

两类模式：

- **offline**（Phase 1 门禁）：只跑确定性改写预判 `looks_like_followup`，
  逐轮断言 `expected.precheck`，产出省略召回 / 自包含误改写率 —— 零 LLM、不触网，
  可进 CI。
- **graph**（Phase 2+ 在线）：逐样本**串行**把 turns 喂给完整 AgentGraph
  （同 thread_id 保持多轮记忆），记录每轮 intent / branch / refusal / 改写后问题
  —— 真实 LLM + Milvus，供人工/周期核验。

与 `evals/runners/router.py` 的边界一致：不 import 运行时包（agent 由调用方注入，
本模块内函数惰性 import）。
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union

from langchain_core.messages import AIMessage, HumanMessage

from evals.datasets.multiturn import MultiTurnSample

PathLike = Union[str, Path]


def _alternating_messages(turns: Sequence[str], upto: int) -> List:
    """把前 `upto` 轮构造成 [H,A,H,A,...] 消息序列（末条必为用户 H）。

    AIMessage 用占位文本（改写预判只读 HumanMessage；占位保证与真实形态一致）。
    """
    msgs: List = []
    seq = list(turns[:upto])
    for i, q in enumerate(seq):
        msgs.append(HumanMessage(content=q))
        if i != len(seq) - 1:
            msgs.append(AIMessage(content="（前一轮回答）"))
    return msgs


# ── offline：确定性改写预判门禁 ────────────────────────────────


def run_precheck(
    samples: Sequence[MultiTurnSample],
    precheck_fn: Optional[Callable[[Sequence], bool]] = None,
) -> Dict[str, object]:
    """逐轮断言确定性改写预判，不调 LLM/不触网。

    Args:
        samples: 金标样本（只用 `turns` + `expected.precheck`）。
        precheck_fn: `(messages) -> bool`；缺省用
            `agent.nodes.rewrite.looks_like_followup`（惰性 import）。

    Returns:
        {"per_turn": [...], "summary": {...}} —— summary 字段与 `evals.gates.
        check_multiturn` 对齐（ellipsis_recall / self_contained_false_positive）。
    """
    if precheck_fn is None:
        from agent.nodes.rewrite import looks_like_followup

        precheck_fn = looks_like_followup

    rows: List[dict] = []
    for s in samples:
        for i in range(1, len(s.turns)):  # 从第 2 轮起才有"追问"概念
            turn = f"turn{i + 1}"
            exp = s.expected.get(turn) or {}
            msgs = _alternating_messages(s.turns, i + 1)
            actual = bool(precheck_fn(msgs))
            expected_precheck = exp.get("precheck")
            rows.append({
                "id": s.id,
                "turn": turn,
                "question": s.turns[i],
                "expected_precheck": expected_precheck,
                "expected_rewrite": exp.get("rewrite"),
                "actual_precheck": actual,
                "passed": expected_precheck is None or actual == expected_precheck,
            })

    ellipsis = [r for r in rows if r["expected_rewrite"] is True]
    self_contained = [r for r in rows if r["expected_rewrite"] is False]
    recall = (
        sum(1 for r in ellipsis if r["actual_precheck"]) / len(ellipsis)
        if ellipsis else None
    )
    fp = (
        sum(1 for r in self_contained if r["actual_precheck"]) / len(self_contained)
        if self_contained else None
    )
    summary: Dict[str, object] = {
        "n_turns": len(rows),
        "passed_turns": sum(1 for r in rows if r["passed"]),
        "ellipsis_recall": round(recall, 4) if recall is not None else None,
        "self_contained_false_positive": round(fp, 4) if fp is not None else None,
    }
    return {"per_turn": rows, "summary": summary}


# ── graph：在线全图检查 ───────────────────────────────────────


def _is_refusal(business_result: dict, answer: str) -> bool:
    """拒答判定：引用校验 is_refusal 标记优先，否则按拒答文案兜底。"""
    data = business_result.get("data")
    if isinstance(data, dict):
        cv = data.get("citation_validation")
        if isinstance(cv, dict) and cv.get("is_refusal"):
            return True
    return "暂无相关内容" in (answer or "")


def _last_human(messages: Sequence) -> str:
    """改写后问题 = 末条用户消息内容（rewrite 节点同 id 原位替换后即归一化问题）。"""
    for m in reversed(list(messages)):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


def _run_sample(sample: MultiTurnSample, agent) -> dict:
    """单样本：同一 thread_id 串行走完整 AgentGraph，逐轮记录。"""
    thread_id = f"mt-{uuid.uuid4().hex[:12]}"
    turns: List[dict] = []
    try:
        for i, q in enumerate(sample.turns, 1):
            res = agent.invoke(q, thread_id=thread_id)
            br = res.get("business_result") or {}
            state = agent.get_state(thread_id) or {}
            turns.append({
                "turn": i,
                "question": q,
                "intent": str(res.get("intent") or ""),
                "branch": str(br.get("branch") or ""),
                "answer": str(res.get("answer") or "")[:2000],
                "resolved_question": _last_human(state.get("messages") or []),
                "refusal": _is_refusal(br, str(res.get("answer") or "")),
            })
        return {"id": sample.id, "turns": turns, "error": None}
    except Exception as e:  # noqa: BLE001  单样本失败降级
        return {"id": sample.id, "turns": turns, "error": f"{type(e).__name__}: {e}"}


def run_multiturn(
    samples: Sequence[MultiTurnSample],
    agent,
    *,
    workers: int = 1,
    out_dir: Optional[PathLike] = None,
) -> Dict[str, object]:
    """在线跑完整 AgentGraph（真实 LLM + Milvus），可选落盘产物。

    Args:
        samples: 金标样本（只用 `turns`）。
        agent: AgentGraph 实例（须含 invoke / get_state）。
        workers: 样本间并发（单样本内必须串行——多轮记忆依赖同 thread_id）。
        out_dir: 给出则写 per_sample.jsonl / summary.json / report.md。

    Returns:
        {"per_sample": [...], "summary": {...}}。
    """
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        rows = list(pool.map(lambda s: _run_sample(s, agent), samples))
    elapsed = round(time.monotonic() - t0, 1)

    errors = sum(1 for r in rows if r["error"])
    turns = [t for r in rows for t in r["turns"]]
    checked = [t for t in turns if t["turn"] >= 2]
    refusal_rate = (
        round(sum(1 for t in checked if t["refusal"]) / len(checked), 4)
        if checked else None
    )
    summary: Dict[str, object] = {
        "n_samples": len(rows),
        "n_turns": len(turns),
        "followup_turns": len(checked),
        "refusal_rate": refusal_rate,
        "errors": errors,
        "elapsed_s": elapsed,
    }

    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "per_sample.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
        (out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (out / "report.md").write_text(_render_report(rows, summary), encoding="utf-8")

    return {"per_sample": rows, "summary": summary}


def _render_report(rows: Sequence[dict], summary: dict) -> str:
    lines = [
        "# 多轮对话评测（graph 在线）", "",
        f"- 样本 {summary.get('n_samples')} · 轮次 {summary.get('n_turns')} · "
        f"追问轮 {summary.get('followup_turns')} · 拒答率 {summary.get('refusal_rate')}",
        f"- 错误 {summary.get('errors')} · 耗时 {summary.get('elapsed_s')}s", "",
        "| id | turn | 问题 | intent | branch | 改写后 | 拒答 |", "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        for t in r.get("turns") or []:
            lines.append(
                f"| {r['id']} | {t['turn']} | {t['question'][:24]} | "
                f"{t['intent']} | {t['branch']} | {t['resolved_question'][:24]} | "
                f"{t['refusal']} |"
            )
        if r.get("error"):
            lines.append(f"| {r['id']} | — | **错误: {r['error']}** | | | | |")
    return "\n".join(lines) + "\n"


__all__ = ["run_precheck", "run_multiturn"]
