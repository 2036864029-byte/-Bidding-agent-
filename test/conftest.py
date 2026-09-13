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
    # 需要真后端的用例自行显式构造（见 test/api/test_file_store_backends.py）。
    monkeypatch.setenv("FILE_STORE_BACKEND", "memory")
    # 任务状态表（B1 姊妹项）同理：进程内，不依赖外部 Redis。
    monkeypatch.setenv("JOB_QUEUE_BACKEND", "memory")
    # 检查点同理：默认 memory（不落盘、不依赖 PG）。需要真后端的用例显式构造
    # （见 test/core/test_checkpointer_postgres.py）。
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "memory")
    # C1：文档索引复用 file_store 后端配置 → 同样是进程级单例，跨用例会互相污染
    # （同一 file_id+session 命中上一用例的索引）。
    from api import file_store
    from core import doc_index
    from core.cache import reset_caches

    reset_caches()
    file_store.reset_store()   # 后端是进程级单例，可能已按 .env 构造过
    doc_index.reset_index()
    yield
    reset_caches()
    file_store.reset_store()
    doc_index.reset_index()


@pytest.fixture
def fake_doc_embeddings(monkeypatch):
    """C1 测试用假 embedding：字符计数向量（零网络、确定性）。

    经 monkeypatch ``core.embeddings.get_embeddings`` 注入——``core.doc_index`` 与
    ``api.doc_context`` 都在函数体内 ``from core.embeddings import get_embeddings``，
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
