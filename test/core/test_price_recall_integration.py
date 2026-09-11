"""price 5 级链接入 RecallPipeline 的离线集成测试（方案 Phase 3 / D-6、D-9）。

全 mock：SQL 执行器（_execute_sql_with_timeout）被替换，不触任何库/网络。
覆盖：阶段序与 is_scan 标记、扫描默认禁入 + 显式放行、首阶段命中短路、
deadline 熔断、逐关键词拆分聚合、FULLTEXT 缺失降级继续。
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import pytest

warnings.filterwarnings("ignore", category=DeprecationWarning)

import price_engine.recall as recall
import price_engine.recall_stages as recall_stages
from price_engine.models import HardFilters, SearchIntent
from price_engine.recall import _execute_recall_chain_core
from price_engine.recall_stages import build_recall_stages
from price_engine.schema import _HARDCODED_SCHEMA


class _Conn:  # 占位连接：测试中 SQL 执行器被替换，不会被真实使用
    pass


def _settings(allow_scan: bool = False, deadline_ms: int = 3000) -> SimpleNamespace:
    return SimpleNamespace(
        sql_query_timeout=15,
        price_recall_deadline_ms=deadline_ms,
        price_allow_full_scan=allow_scan,
        mysql_clean_db="ztb_clean",
    )


def _intent(keywords=("安徽海纳",)) -> SearchIntent:
    # company_name + time_range 保证 _build_full_scan_sql 能产出 SQL：
    # 无约束性条件时 P0-3 守卫返回 None（约束 = 时间/数值/代码精确，实体名不算）
    return SearchIntent(
        hard_filters=HardFilters(
            company_name="安徽海纳信息科技有限公司",
            time_range={"start": "2016-01-01"},
        ),
        semantic_keywords=list(keywords),
    )


def _patch(monkeypatch, *, allow_scan: bool, deadline_ms: int, fake_execute) -> None:
    monkeypatch.setattr(recall, "_get_settings", lambda: _settings(allow_scan, deadline_ms))
    monkeypatch.setattr(recall_stages, "_get_settings", lambda: _settings(allow_scan, deadline_ms))
    monkeypatch.setattr(recall_stages, "_execute_sql_with_timeout", fake_execute)


def test_stage_order_and_scan_flag():
    stats = {"sql_count": 0, "sql_time": 0.0}
    stages = build_recall_stages(
        "company_info", _HARDCODED_SCHEMA["company_info"], _intent(), stats,
    )
    assert [s.name for s in stages] == ["exact_constraint", "fulltext_or", "like_fallback", "split_keyword", "full_scan"]
    assert [s.is_scan for s in stages] == [False, False, False, False, True]


def test_default_scan_disabled_miss_returns_empty(monkeypatch):
    calls = []

    def fake(conn, sql, params, table, stage, timeout=None):  # noqa: ARG001
        calls.append(stage)
        return [], 0.0

    _patch(monkeypatch, allow_scan=False, deadline_ms=3000, fake_execute=fake)
    rows, _n, _t = _execute_recall_chain_core(
        _Conn(), "company_info", _HARDCODED_SCHEMA["company_info"], _intent(),
    )
    assert rows == []
    assert "full_scan" not in calls  # 默认禁入：全表扫描未被尝试


def test_scan_enabled_runs_and_annotates_stage5(monkeypatch):
    calls = []

    def fake(conn, sql, params, table, stage, timeout=None):  # noqa: ARG001
        calls.append(stage)
        if stage == "full_scan":
            return [{"_id_": "999", "name": "x"}], 0.01
        return [], 0.0

    _patch(monkeypatch, allow_scan=True, deadline_ms=3000, fake_execute=fake)
    rows, _n, _t = _execute_recall_chain_core(
        _Conn(), "company_info", _HARDCODED_SCHEMA["company_info"], _intent(),
    )
    assert rows and rows[0]["_recall_stage_"] == 5
    assert rows[0]["_id_"] == "999"
    assert "full_scan" in calls


def test_first_hit_short_circuits_later_stages(monkeypatch):
    calls = []

    def fake(conn, sql, params, table, stage, timeout=None):  # noqa: ARG001
        calls.append(stage)
        if stage == "fulltext_or":
            return [{"_id_": "1"}], 0.0
        return [], 0.0

    _patch(monkeypatch, allow_scan=True, deadline_ms=3000, fake_execute=fake)
    rows, _n, _t = _execute_recall_chain_core(
        _Conn(), "company_info", _HARDCODED_SCHEMA["company_info"], _intent(),
    )
    assert rows[0]["_recall_stage_"] == 1
    assert calls == ["fulltext_or"]  # 短路：like/split/scan 不再执行


def test_deadline_zero_fuses_without_sql(monkeypatch):
    calls = []

    def fake(conn, sql, params, table, stage, timeout=None):  # noqa: ARG001
        calls.append(stage)
        return [{"_id_": "1"}], 0.0

    _patch(monkeypatch, allow_scan=True, deadline_ms=0, fake_execute=fake)
    rows, _n, _t = _execute_recall_chain_core(
        _Conn(), "company_info", _HARDCODED_SCHEMA["company_info"], _intent(),
    )
    assert rows == []
    assert calls == []  # deadline 到 → 首阶段前即熔断


def test_split_keyword_aggregates_and_annotates_stage4(monkeypatch):
    calls = []

    def fake(conn, sql, params, table, stage, timeout=None):  # noqa: ARG001
        calls.append(stage)
        if stage.startswith("FULLTEXT_RETRY_"):
            return [{"_id_": "2", "name": "y"}], 0.0
        return [], 0.0

    _patch(monkeypatch, allow_scan=False, deadline_ms=3000, fake_execute=fake)
    rows, _n, _t = _execute_recall_chain_core(
        _Conn(), "company_info", _HARDCODED_SCHEMA["company_info"],
        _intent(["安徽海纳", "信息科技"]),
    )
    assert rows and rows[0]["_recall_stage_"] == 4
    assert rows[0]["_id_"] == "2"
    assert any(c.startswith("FULLTEXT_RETRY_") for c in calls)


def test_fulltext_missing_continues_to_like(monkeypatch):
    """FULLTEXT 阶段抛 fulltext 错误 → 记录 warning 并继续后续阶段（不熔断）。"""
    calls = []

    def fake(conn, sql, params, table, stage, timeout=None):  # noqa: ARG001
        calls.append(stage)
        if stage == "fulltext_or":
            raise RuntimeError("Can't find FULLTEXT index matching the column list")
        if stage == "like_fallback":
            return [{"_id_": "7"}], 0.0
        return [], 0.0

    _patch(monkeypatch, allow_scan=False, deadline_ms=3000, fake_execute=fake)
    rows, _n, _t = _execute_recall_chain_core(
        _Conn(), "company_info", _HARDCODED_SCHEMA["company_info"], _intent(),
    )
    assert rows[0]["_recall_stage_"] == 3
    assert calls == ["fulltext_or", "like_fallback"]


def test_sql_count_and_time_accumulate(monkeypatch):
    calls = []

    def fake(conn, sql, params, table, stage, timeout=None):  # noqa: ARG001
        calls.append(stage)
        return [], 0.05

    _patch(monkeypatch, allow_scan=False, deadline_ms=3000, fake_execute=fake)
    rows, n, t = _execute_recall_chain_core(
        _Conn(), "company_info", _HARDCODED_SCHEMA["company_info"], _intent(),
    )
    assert rows == []
    assert n == len([c for c in calls if c in ("fulltext_or", "like_fallback")])
    assert t == pytest.approx(0.05 * n, abs=1e-6)
