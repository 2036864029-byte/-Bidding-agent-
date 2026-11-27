"""channels.feishu.telemetry — 心跳判活与指标（不联网、不碰真实飞书）。

守住三件事：
① 心跳新鲜度判定 —— 这是容器健康检查的唯一依据，判错了会误重启或漏报；
② `disconnected` **不**判不健康（SDK 自带重连，短暂断连不该触发重启风暴）；
③ 指标计数器确实在累加（消息结果 / 回复结果）。
"""

from __future__ import annotations

import json
import time

import pytest

from channels.feishu import telemetry
from channels.feishu.telemetry import Heartbeat, check_heartbeat, read_heartbeat


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


@pytest.fixture(autouse=True)
def _isolate_heartbeat(tmp_path):
    """每个用例用独立心跳单例 + 临时路径，避免写到仓库 DATA/ 或串状态。"""
    hb = Heartbeat(str(tmp_path / "hb.json"), interval_s=60)
    telemetry.set_heartbeat(hb)
    yield hb
    telemetry.set_heartbeat(None)


# ── 心跳文件 ─────────────────────────────────────────────────────


def test_heartbeat_written_and_read_back(_isolate_heartbeat, tmp_path):
    hb = _isolate_heartbeat
    hb.set_state("connected")
    hb.touch_event()
    hb.touch_reply()
    hb.write()

    data = read_heartbeat(hb.path)
    assert data is not None
    assert data["state"] == "connected"
    assert data["events"] == 1
    assert data["replies"] == 1
    assert data["last_event_at"] is not None
    # 不留下临时文件（原子写用 rename）
    assert list((tmp_path).glob("*.tmp")) == []


def test_read_missing_file_returns_none(tmp_path):
    assert read_heartbeat(tmp_path / "nope.json") is None


def test_read_broken_json_returns_none(tmp_path):
    p = tmp_path / "hb.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_heartbeat(p) is None


# ── 判活 ────────────────────────────────────────────────────────


def test_fresh_heartbeat_is_healthy(_isolate_heartbeat):
    hb = _isolate_heartbeat
    hb.set_state("connected")
    hb.write()
    ok, reason = check_heartbeat(hb.path)
    assert ok, reason


def test_stale_heartbeat_is_unhealthy(_isolate_heartbeat):
    """进程在、但心跳停了（长连接僵死 / 线程卡住）→ 必须判不健康。"""
    hb = _isolate_heartbeat
    hb.write()
    data = json.loads(hb.path.read_text(encoding="utf-8"))
    data["updated_at"] = time.time() - 999
    hb.path.write_text(json.dumps(data), encoding="utf-8")

    ok, reason = check_heartbeat(hb.path, max_age_s=45)
    assert not ok
    assert "过期" in reason


def test_missing_heartbeat_is_unhealthy(tmp_path):
    ok, reason = check_heartbeat(tmp_path / "absent.json")
    assert not ok
    assert "缺失" in reason


@pytest.mark.parametrize("state", ["stopped", "failed"])
def test_terminal_states_unhealthy(_isolate_heartbeat, state):
    hb = _isolate_heartbeat
    hb.set_state(state, note="boom")
    hb.write()
    ok, reason = check_heartbeat(hb.path)
    assert not ok
    assert state in reason


def test_disconnected_is_still_healthy(_isolate_heartbeat):
    """SDK 自己会重连：短暂 disconnected 不该触发容器重启。"""
    hb = _isolate_heartbeat
    hb.set_state("disconnected")
    hb.write()
    ok, reason = check_heartbeat(hb.path)
    assert ok, reason


def test_cli_exit_codes(_isolate_heartbeat, capsys):
    hb = _isolate_heartbeat
    hb.set_state("connected")
    hb.write()
    assert telemetry.main(["--check", str(hb.path)]) == 0
    assert "OK" in capsys.readouterr().out

    hb.set_state("failed")
    hb.write()
    assert telemetry.main(["--check", str(hb.path)]) == 1
    assert "FAIL" in capsys.readouterr().out


# ── 指标 ────────────────────────────────────────────────────────

requires_metrics = pytest.mark.skipif(
    not telemetry._METRICS_AVAILABLE, reason="prometheus_client 不可用"
)


@requires_metrics
def test_record_message_increments_and_sets_last_event():
    before = _counter_value(telemetry.MESSAGES_TOTAL, result="ok")
    telemetry.record_message("ok", seconds=0.42)
    assert _counter_value(telemetry.MESSAGES_TOTAL, result="ok") == before + 1
    assert telemetry.LAST_EVENT_TS._value.get() > 0


@requires_metrics
def test_record_reply_increments_both_results():
    ok_before = _counter_value(telemetry.REPLY_TOTAL, result="ok")
    fail_before = _counter_value(telemetry.REPLY_TOTAL, result="failed")
    telemetry.record_reply(True)
    telemetry.record_reply(False)
    assert _counter_value(telemetry.REPLY_TOTAL, result="ok") == ok_before + 1
    assert _counter_value(telemetry.REPLY_TOTAL, result="failed") == fail_before + 1


def test_metrics_server_disabled_when_port_zero(monkeypatch):
    """port<=0 时不起抓取端口（避免在测试/单机环境下占端口）。"""
    monkeypatch.setenv("FEISHU_METRICS_PORT", "0")
    assert telemetry.start_metrics_server() is None
