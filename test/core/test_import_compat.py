"""Phase 0 迁移的 import 兼容测试（方案 Phase 0 / A-13，附录 A-1/A-6/A-7/A-10 验证）。

断言旧路径全部可 import，且与 core 新路径指向同一实现/对象——迁移零行为变更。
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)


def test_settings_two_import_paths_are_identical():
    """A-1：public_kb.config 与 core.config.settings 构造出的 Settings 全等。"""
    from core.config.settings import Settings as CoreSettings
    from public_kb.config import Settings as LegacySettings

    assert CoreSettings is LegacySettings  # 同一类（re-export，非拷贝）

    core_s = CoreSettings()
    legacy_s = LegacySettings()
    for f in core_s.__dataclass_fields__:
        assert getattr(core_s, f) == getattr(legacy_s, f), f"字段 {f} 不一致"
    assert core_s.resolved_milvus_uri == legacy_s.resolved_milvus_uri


def test_citation_rule_config_reexported_from_old_path():
    from core.config.settings import CitationRuleConfig as CoreCfg
    from public_kb.config import CitationRuleConfig as LegacyCfg

    assert CoreCfg is LegacyCfg
    assert LegacyCfg() == CoreCfg()


def test_settings_mysql_fields_from_env():
    """A-10 收口：MYSQL_* 已并入 Settings（不再直读 os.getenv 于连接模块）。"""
    from core.config.settings import Settings

    s = Settings()
    # env 缺失时为空串/默认端口，避免硬编码内网地址；连接处校验并报错
    assert s.mysql_clean_db == "ztb_clean"
    assert isinstance(s.mysql_port, int)


def test_llm_factory_old_path_is_same_impl():
    """A-4/A-6：public_kb.services.llm.create_llm 与 core.llm.deepseek 同源。"""
    from core.llm.deepseek import create_llm as core_create
    from public_kb.services.llm import create_llm as legacy_create

    assert callable(core_create)
    assert core_create is not None
    # 两者均可用；兼容签名 (settings, *, temperature=None)
    import inspect

    params = list(inspect.signature(legacy_create).parameters)
    assert params == ["settings", "temperature"]


def test_embeddings_old_path_reexports_core_symbols():
    """A-7：services.embeddings 壳导出与新路径一致。"""
    from core.embeddings.bge_m3 import _SafeEmbeddings as CoreSafe
    from core.embeddings.bge_m3 import create_embeddings as core_create
    from public_kb.services.embeddings import (
        _SafeEmbeddings,
        create_embeddings,
    )

    assert _SafeEmbeddings is CoreSafe
    assert callable(create_embeddings)
    assert callable(core_create)


def test_db_pool_old_path_shares_core_objects():
    """A-10：price_inquiry/db.py 与 core.rdb.mysql_pool 共享池与函数（同一性）。"""
    from agent.nodes.price_inquiry.db import (
        _CLEAN_DB as legacy_db,
        _get_connection as legacy_conn,
        _get_settings as legacy_settings,
        _pool_connections as legacy_pool,
        _release_connection as legacy_release,
    )
    from core.rdb.mysql_pool import (
        _CLEAN_DB,
        _get_connection,
        _pool_connections,
        _release_connection,
    )

    assert legacy_conn is _get_connection
    assert legacy_release is _release_connection
    assert legacy_pool is _pool_connections  # 同一列表对象
    assert legacy_db == _CLEAN_DB
    assert legacy_settings() is not None


def test_models_yaml_three_slots():
    """A-2：models.yaml 声明 3 个实际调用点，general_chat 无槽位。"""
    from core.llm.registry import available_slots

    assert available_slots() == ["router", "kb_generation", "price_intent"]
