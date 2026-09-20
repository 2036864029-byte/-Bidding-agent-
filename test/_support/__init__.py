"""测试支撑件（非测试自身）。

`test/_support/` 放**被多个测试共用**的基建，避免"每个用例各写一份 mock"：
  - `hermetic.py`  非 live 用例的网络闸门
  - `fakes.py`     假件（embedder / LLM 等）

导入约定：本项目 `test/` 下无 `__init__.py`，pytest 会把 `test/` 前插到 sys.path，
故用**裸模块名**导入（与既有 `from _diag_common import get_connection` 一致）：
    from _support.hermetic import NetworkGate
"""
