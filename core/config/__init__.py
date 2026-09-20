# 功能：core 配置包。
"""
core.config — 全局单一配置中心。

由 public_kb/config.py 上提合并而来（方案 Phase 0 / A-1）。
所有包共享同一 Settings。原 public_kb.config 的 re-export 兼容壳
**已随 P6 结构收敛删除**（`test/core/test_legacy_path_allowlist.py` 守卫防回潮），
新代码一律 `from core.config.settings import Settings`。
"""
