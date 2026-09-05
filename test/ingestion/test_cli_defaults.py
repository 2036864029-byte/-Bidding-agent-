"""9.3 Track B 验收：ingestion CLI 输入/工作目录配置化（9.3 代码优化执行计划 / B-1~B-3）。

断言 settings 默认路径 + env 覆盖；CLI --files/--work-dir 缺省走 settings 默认、
显式参数覆盖默认。全程 monkeypatch run_file_ingestion，不触真实入库。
"""

from __future__ import annotations

from core.config.settings import Settings
from public_kb.ingestion import cli


def test_settings_default_paths_aligned_to_data_layout():
    s = Settings()
    assert s.ingestion_input_dir.endswith(("DATA/input", "DATA\\input"))
    assert s.ingestion_work_dir.endswith(("DATA/work", "DATA\\work"))


def test_settings_paths_env_override(monkeypatch):
    monkeypatch.setenv("INGESTION_INPUT_DIR", "/tmp/ztb_in")
    monkeypatch.setenv("INGESTION_WORK_DIR", "/tmp/ztb_wk")
    s = Settings()
    assert s.ingestion_input_dir == "/tmp/ztb_in"
    assert s.ingestion_work_dir == "/tmp/ztb_wk"


def _capture_run(monkeypatch) -> dict:
    captured = {}
    monkeypatch.setattr(
        cli,
        "run_file_ingestion",
        lambda *a, **k: captured.update(args=a, kwargs=k) or [],
    )
    return captured


def test_main_defaults_files_to_input_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("INGESTION_INPUT_DIR", str(tmp_path))
    captured = _capture_run(monkeypatch)
    assert cli.main([]) == 0
    assert captured["args"][0] == str(tmp_path)  # files_dir = 默认输入目录
    assert captured["kwargs"]["work_dir"] is not None  # 默认启用 checkpoint/落盘


def test_main_explicit_files_overrides_default(monkeypatch, tmp_path):
    monkeypatch.setenv("INGESTION_INPUT_DIR", str(tmp_path / "default_input"))
    explicit = str(tmp_path / "explicit")
    captured = _capture_run(monkeypatch)
    cli.main(["--files", explicit])
    assert captured["args"][0] == explicit


def test_main_default_work_dir_from_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("INGESTION_INPUT_DIR", str(tmp_path))
    monkeypatch.setenv("INGESTION_WORK_DIR", str(tmp_path / "work"))
    captured = _capture_run(monkeypatch)
    cli.main([])
    assert captured["kwargs"]["work_dir"] == str(tmp_path / "work")


def test_main_explicit_work_dir_overrides_default(monkeypatch, tmp_path):
    monkeypatch.setenv("INGESTION_INPUT_DIR", str(tmp_path))
    monkeypatch.setenv("INGESTION_WORK_DIR", str(tmp_path / "work"))
    explicit = str(tmp_path / "explicit_work")
    captured = _capture_run(monkeypatch)
    cli.main(["--files", str(tmp_path), "--work-dir", explicit])
    assert captured["kwargs"]["work_dir"] == explicit
