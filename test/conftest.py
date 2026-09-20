"""pytest 全局夹具。

W4-2：检索缓存（core.cache）是**进程级单例**，跨用例会互相污染
（同一问题命中上一用例的缓存结果 → 断言拿到过期/异构数据）。
每个用例前后清空缓存注册表，保证用例隔离。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_retrieval_cache():
    from core.cache import reset_caches

    reset_caches()
    yield
    reset_caches()


@pytest.fixture(autouse=True)
def _reset_mineru_version_memo():
    """P5 加固：MinerU parser_version 是**模块级** memo（按 base_url），跨用例会
    互相短路探测（后一个用例拿到前一个的版本，断言全错）。"""
    import public_kb.services.mineru_api_parser as parser_mod

    parser_mod.reset_version_memo()
    yield
    parser_mod.reset_version_memo()


@pytest.fixture(autouse=True)
def _reset_capability_overrides():
    """能力开关覆盖（`CAPABILITY_ENABLED/DISABLED`）读后进程内缓存 → 跨用例会串。"""
    from agent.registry import reset_capability_overrides

    reset_capability_overrides()
    yield
    reset_capability_overrides()


@pytest.fixture(autouse=True)
def _reset_inflight_gate():
    """C1：在飞闸门是进程级单例，跨用例会残留计数与配置 → 每个用例前后重置。"""
    from core.concurrency.limits import reset_inflight_gate

    reset_inflight_gate()
    yield
    reset_inflight_gate()


@pytest.fixture(autouse=True)
def _hermetic_backends(monkeypatch):
    """C4：把缓存/限流后端钉在进程内，让测试与开发机的 .env 解耦。

    为什么必须钉：若开发者把 .env 切成 `CACHE_BACKEND=redis` / `RATE_LIMIT_BACKEND=redis`，
    测试会连带产生两个问题：
      1. **依赖外部 Redis**（CI/他人机器上没有 → 大面积失败）；
      2. **跨用例污染** —— `reset_caches()` 只清进程内注册表，**不清 Redis**，
         上一轮（甚至上一次运行）的缓存条目会短路本用例的预期路径；
         且 Redis 后端每次反序列化 → 返回**新对象**，断言 `is` 身份的用例会失败。

    需要真实 Redis 的用例自行显式构造（见 test/core/test_ratelimit.py 的集成段）。
    """
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    # 上传文件缓存（B1）同理：钉在进程内，测试不依赖外部 Redis、也不跨用例污染。
    # 需要真后端的用例自行显式构造（见 test/core/test_filestore_backends.py）。
    monkeypatch.setenv("FILE_STORE_BACKEND", "memory")
    # 任务状态表（B1 姊妹项）同理：进程内，不依赖外部 Redis。
    monkeypatch.setenv("JOB_QUEUE_BACKEND", "memory")
    # 检查点同理：默认 memory（不落盘、不依赖 PG）。需要真后端的用例显式构造
    # （见 test/core/test_checkpointer_postgres.py）。
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "memory")
    # 能力开关同理：**开发机 .env 可能把 deep_agent 启用了**（第 3 项上线后的正常状态），
    # 若测试跟着变，"默认候选集 = 5 内置"这类断言就会随环境飘。钉成空 →
    # 一律用 spec 的声明默认值；要测开关的用例自行 monkeypatch.setenv
    # （见 test/orchestration/test_agent_gate.py、test/core/test_registry.py）。
    monkeypatch.setenv("CAPABILITY_ENABLED", "")
    monkeypatch.setenv("CAPABILITY_DISABLED", "")
    # C1：文档索引复用 filestore 后端配置 → 同样是进程级单例，跨用例会互相污染
    # （同一 file_id+session 命中上一用例的索引）。
    from core import doc_index, filestore
    from core.cache import reset_caches

    reset_caches()
    filestore.reset_store()   # 后端是进程级单例，可能已按 .env 构造过
    doc_index.reset_index()
    yield
    reset_caches()
    filestore.reset_store()
    doc_index.reset_index()


@pytest.fixture
def fake_doc_embeddings(monkeypatch):
    """C1 测试用假 embedding：字符计数向量（零网络、确定性）。

    经 monkeypatch ``core.embeddings.get_embeddings`` 注入——``core.doc_index`` 与
    ``agent.doc_context`` 都在函数体内 ``from core.embeddings import get_embeddings``，
    故替换模块属性即对二者同时生效。字符重叠 → 余弦相似，足以验证排序与融合。
    """
    import numpy as np

    import core.embeddings as ce

    vocab = "投标保证金金额评标委员会成员人数招标方式废标条款工期质量验收标准合同价款支付"

    class _FakeEmbeddings:
        def _vec(self, text: str):
            return np.array(
                [float(text.count(ch)) for ch in vocab], dtype=np.float32
            ).tolist()

        def embed_documents(self, texts):
            return [self._vec(t) for t in texts]

        def embed_query(self, text):
            return self._vec(text)

    monkeypatch.setattr(ce, "get_embeddings", lambda settings=None: _FakeEmbeddings())


# ── 测试分层：integration / e2e 默认跳过 ─────────────────────────────
#
# 为什么要默认跳过：标了 marker 的用例仍会被 `pytest test/` 跑到 —— marker 只是
# 标签，不是开关。这些用例需要真实 Milvus / MySQL / 模型隧道，在没起的机器上
# 必然失败，于是"套件红绿取决于本机状态"而不是代码对错。默认跳过把这类噪声
# 从日常运行里摘出去，需要时用 RUN_INTEGRATION=1 放行。
#
# 与 `-m "not integration"` 的区别：`-m` 要每次都记得写；默认跳过是**默认安全**，
# 漏写不会误跑。P1 的网络闸门会在此基础上再把"忘了标记却偷偷联网"堵死。

def pytest_collection_modifyitems(config, items):
    """integration / e2e 用例默认跳过；`RUN_INTEGRATION=1` 放行。"""
    import os

    if os.getenv("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(
        reason="integration/e2e 默认跳过（需真实 Milvus/MySQL/隧道）；设 RUN_INTEGRATION=1 放行"
    )
    for item in items:
        if item.get_closest_marker("integration") or item.get_closest_marker("e2e"):
            item.add_marker(skip)
