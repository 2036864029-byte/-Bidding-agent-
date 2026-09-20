"""共用假件 —— 消除"每个用例各写一份 mock"。

**为什么要集中**（2026-09-20 实测）：测试套件里曾同时存在 **5 种**假 embedder 写法
（conftest 的字符计数版、`test_inquiry_reference.py` 里内联 4 遍的全零版、
`test_supervisor_router.py` 的哈希版、`test_citation_tracing.py` 的 `_MockEmbeddings`、
`test_qa_chain_offline.py` 的计数版）。重复手抄正是漏 mock 那类 bug 的温床 ——
`test_no_hits_above_threshold` 就是因为"忘了抄兄弟用例的 embedder patch"而真的发了
网络请求（详见 `test/_support/hermetic.py`）。

它们**语义各不相同，不能合并成一个**，故本模块按语义提供规范集，各测试按需选：

    ZeroEmbeddings        全零    —— 相似度无关紧要，只需走完流程
    CharCountEmbeddings   字符计数 —— 需要"文本重叠越大越相似"的排序语义
    HashEmbeddings        哈希 one-hot —— 需要"同文=1.0 / 异文=0.0"的精确判定

三者都继承 `FakeEmbeddings`，自带 `query_calls` / `document_calls` 计数，
可直接用于"断言调了几次"的场景（原 `test_qa_chain_offline` 的计数版用途）。

**新增测试请优先选这里已有的**；确有新语义再加一个类，别在用例里内联 lambda ——
内联版无法被复用、无法被检索到，是重复的源头。
"""

from __future__ import annotations

import hashlib
from typing import Any, List, Sequence

__all__ = [
    "FakeEmbeddings",
    "ZeroEmbeddings",
    "CharCountEmbeddings",
    "HashEmbeddings",
    "DEFAULT_VOCAB",
    "KB_SEARCH_ENTRY_POINTS",
    "patch_embeddings",
    "patch_kb_search",
]

#: 字符计数版默认词表 —— 取自招投标语料的判别性字（用于让"同主题文本"余弦更高）。
DEFAULT_VOCAB = "投标保证金金额评标委员会成员人数招标方式废标条款工期质量验收标准合同价款支付"


class FakeEmbeddings:
    """假 embedder 基类：记录调用次数，向量由子类的 `_vec` 决定。"""

    def __init__(self) -> None:
        self.query_calls = 0
        self.document_calls = 0

    def _vec(self, text: str) -> List[float]:
        raise NotImplementedError

    def embed_query(self, text: str) -> List[float]:
        self.query_calls += 1
        return self._vec(text)

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        self.document_calls += len(texts)
        return [self._vec(t) for t in texts]


class ZeroEmbeddings(FakeEmbeddings):
    """全零向量（默认 1024 维，对齐 BGE-m3）。

    用于"相似度无关紧要"的场景 —— 例如只想验证 Milvus 检索的阈值过滤/金额换算/
    排序分支，向量的值不影响被测逻辑。**最省事，优先选它。**
    """

    def __init__(self, dim: int = 1024) -> None:
        super().__init__()
        self.dim = dim

    def _vec(self, text: str) -> List[float]:
        return [0.0] * self.dim


class CharCountEmbeddings(FakeEmbeddings):
    """字符计数向量 —— 文本对词表的字符重叠越多，余弦越高。

    用于需要"排序有语义"的场景（如融合/重排后谁在前）。**零网络、确定性。**
    """

    def __init__(self, vocab: str = DEFAULT_VOCAB) -> None:
        super().__init__()
        self.vocab = vocab

    def _vec(self, text: str) -> List[float]:
        return [float(text.count(ch)) for ch in self.vocab]


class HashEmbeddings(FakeEmbeddings):
    """哈希 one-hot —— **同文向量相同（余弦 1.0），异文正交（余弦 0.0）**。

    用于需要"精确命中/不命中"判定的场景（如 L2 语义路由的阈值比较）。
    注意它的语义是"要么 1 要么 0"，**没有中间值** —— 需要渐变相似度请用
    `CharCountEmbeddings`。
    """

    def __init__(self, dim: int = 32) -> None:
        super().__init__()
        self.dim = dim

    def _vec(self, text: str) -> List[float]:
        digest = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)
        vec = [0.0] * self.dim
        vec[digest % self.dim] = 1.0
        return vec


def patch_embeddings(monkeypatch: Any, fake: FakeEmbeddings, module: Any) -> FakeEmbeddings:
    """把 `module.get_embeddings` 换成返回 `fake` 的桩（便捷函数）。

    调用方模块若在**函数体内** `from core.embeddings import get_embeddings`
    （本项目多处如此），替换模块属性即对其生效。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture。
        fake: 要注入的假件。
        module: 持有 `get_embeddings` 的模块对象（如 `price_engine.reference`）。
    """
    monkeypatch.setattr(module, "get_embeddings", lambda settings=None: fake)
    return fake


#: knowledge_qa 子图的**两个**检索入口。`_retrieve_node` 按
#: `settings.kb_light_decompose` 二选一（默认 True → 走 `kb_search_expanded`）。
KB_SEARCH_ENTRY_POINTS = ("kb_search", "kb_search_expanded")


def patch_kb_search(monkeypatch: Any, module: Any, result: Any) -> None:
    """把 knowledge_qa 子图的检索入口**全部**换成返回 `result` 的桩。

    **为什么必须一次补两个**（本坑已实测踩两次，2026-09-20）：
    `agent/nodes/knowledge_qa/subgraph.py::_retrieve_node` 是二选一调用的 ——

        retrieval = kb_search_expanded(question) if settings.kb_light_decompose
                    else kb_search(question)

    `kb_light_decompose` 默认 **True**（core/config/settings.py），所以只补
    `kb_search` **静默失效**、真跑检索。危害不只是"多一次网络调用"：

      - `test_subgraph_refusal_never_triggers_retry`：真检索恰好返回空 →
        拒答路径成立 → 断言通过。**服务不可用时反而绿，服务一起来就转红** ——
        方向完全相反的环境依赖。
      - `test_real_compiled_subgraph_through_supervisor`：同样失效，表现为
        "本机跑隧道就绿"。

    故此处封装"该补哪些入口"这一知识：新增检索入口时只改本模块，
    调用方不必知道。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture。
        module: 持有检索入口的模块（如 `agent.nodes.knowledge_qa.subgraph`）。
        result: 两个入口都返回它（通常是 `RetrievalResult(...)`，可为空实例）。
    """
    for name in KB_SEARCH_ENTRY_POINTS:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, lambda q, _r=result: _r)
