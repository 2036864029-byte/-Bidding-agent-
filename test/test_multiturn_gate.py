"""多轮改写预判门禁（2026-09-16，Phase 0/1）—— 离线回归。

跑 `evals.runners.multiturn.run_precheck` 对 `DATA/eval_multiturn/multiturn_30.jsonl`
做确定性改写预判断言，并用 `evals.gates.check_multiturn` 判门禁。零 LLM、零网络，
可进 CI；任何改写触发规则的改动跑本文件防断链回归。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "DATA" / "eval_multiturn" / "multiturn_30.jsonl"


def test_multiturn_gate_passes():
    from evals.datasets.multiturn import load_jsonl
    from evals.gates import all_passed, check_multiturn
    from evals.runners.multiturn import run_precheck

    samples = load_jsonl(DATA)
    assert len(samples) == 30, "金标数量变化须同步方案 §2"

    res = run_precheck(samples)
    results = check_multiturn(res["summary"])

    # 逐项断言（失败时给出实测值，便于定位）
    for r in results:
        assert r["passed"], f"{r['name']} FAIL（实测 {r['value']}，基准 {r['target']}）"
    assert all_passed(results)


def test_multiturn_gate_fixture_well_formed():
    """金标自身健全性：追问轮都有 expected，且 precheck 与 rewrite 字段齐备。"""
    from evals.datasets.multiturn import load_jsonl

    samples = load_jsonl(DATA)
    for s in samples:
        assert len(s.turns) >= 2, f"{s.id} 至少 2 轮"
        for turn, exp in s.expected.items():
            assert turn in {"turn2", "turn3"}, f"{s.id} {turn} 只断言第 2/3 轮"
            assert "precheck" in exp and "rewrite" in exp, f"{s.id} {turn} 缺 precheck/rewrite"
