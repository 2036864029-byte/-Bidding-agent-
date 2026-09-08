# 向量索引重构执行方案（HNSW 语义检索 + BM25 关键词检索）

> 执行日期：2026-09-08
> 用户决策：将 RAG 检索从「IVF_FLAT 纯稠密」正式升级为「HNSW 语义检索 + BM25 关键词检索」混合架构，重建集合。
> 关联：[[ztb-progress-checkpoint-20260908]]、`docs/plan1/文本分块优化计划.md` §4.2、`docs/plan1/文档分块与向量入库执行方案（Milvus 单库版）.md` §5.2。

---

## 一、背景与目标

**现状**：RAG 检索集合 `public_kb_chunked_v2`（本地 Milvus v2.6.23，14,216 块：law 8,964 / qa 2,179 / law_compilation 2,080 / book_practical 993）当前是**纯稠密**：dense 索引 `IVF_FLAT`(COSINE, nlist=256)，无 sparse 字段、无 BM25 Function/索引。检索端 `HybridRetriever` 探测到无 `sparse_vector` 字段后自动降级纯稠密（实测 `DENSE_NATIVE / sparse_vector_field_missing`）。

**为什么重构**：《文档分块与向量入库执行方案（Milvus 单库版）》§5.2 设计期就定了「向量字段索引 HNSW + COSINE」（另加 BM25 稀疏检索），修订版实际执行走了「一期 IVF_FLAT + dense-only」的保守路线（《文本分块优化计划》§4.2 注明"规模扩大可平滑迁移 HNSW"）。本次按用户决策正式迁移。

**目标**：dense 索引 IVF_FLAT→HNSW（COSINE），BM25 默认启用（sparse 字段 + 服务端 BM25 Function + SPARSE_INVERTED_INDEX），重建 `public_kb_chunked_v2` 后混合检索（dense+BM25→RRF→rerank）真正生效。

**范围边界**：只动 RAG chunk 集合链路。`price_engine/semantic.py` 的 price 语义集合独立建索引（硬编码 IVF_FLAT，不读 `settings.milvus_dense_index_type`），不在本次范围。

---

## 二、检索架构（重构后）

```
question
  ├─ dense 语义检索   HNSW(COSINE, M=16, efConstruction=128)  检索 ef=64   → 30 条
  ├─ sparse 关键词   服务端 BM25（text 字段 analyzer=chinese, k1=1.2, b=0.75）
  │                    → SPARSE_INVERTED_INDEX(DAAT_MAXSCORE)                → 30 条
  └─ RRF(k=60) 融合 → 30 条 → bge-reranker-v2-m3 重排 → 阈值过滤 → top-5
```

集合 Schema 增加：`sparse_vector`(SPARSE_FLOAT_VECTOR) + BM25 Function `text_bm25_emb`（`MilvusCollectionContract` 已就绪，字段名/度量契约无需改）。标量索引 `chunk_uid`/`doc_type` INVERTED 保持不变。

---

## 三、代码改动

### 1. `core/config/settings.py`

| 字段 | 现值 | 改为 |
| --- | --- | --- |
| `milvus_dense_index_type` | `"IVF_FLAT"` | `"HNSW"` |
| `enable_bm25` | `False` | `True`（默认开；保留 `ENABLE_MILVUS_BM25` env 覆盖） |
| `nprobe` | `32` | 保留（IVF 兼容 / POC 脚本仍引用，RAG 检索路径改用 ef） |

新增：`milvus_hnsw_m=16`（M∈[4,64]）、`milvus_hnsw_ef_construction=128`（efConstruction∈[8,512]）、`milvus_hnsw_search_ef=64`（检索 ef ≥ 单路候选数 30）。

### 2. 新建 `core/vectorstore/index_params.py` — 索引类型→参数单一分发

- `build_dense_index_params(settings)`：`IVF_FLAT→{"nlist":256}` / `HNSW→{"M","efConstruction"}` / 其他→`{}`。
- `build_dense_search_params(settings)`：`HNSW→{"metric_type":"COSINE","params":{"ef":...}}` / `IVF_FLAT→nprobe` / 其他→`{}`。

### 3. `core/vectorstore/milvus_client.py`

`_build_index_params()` 删内联 IVF 判断，改用 `build_dense_index_params`。`_build_schema()` 的 BM25 分支逻辑不动（默认翻转后自动生成 sparse 字段 + Function）。

### 4. 检索参数接线（两处 nprobe 硬编码）

- `public_kb/retrieval/retriever.py` `_hybrid_retrieve`：dense `AnnSearchRequest.param` 改用 `build_dense_search_params`。
- `public_kb/retrieval/fallback.py` `dense_only_retrieve`：`search_params` 改用 `build_dense_search_params`。

### 5. `public_kb/schema_registry.py`

`public_kb` 登记条目 `dense_index_type→"HNSW"` + `hnsw_m`/`hnsw_ef_construction`；`price` 条目保持 `IVF_FLAT`。新增 `INDEX_HNSW_M`/`INDEX_HNSW_EF_CONSTRUCTION` 常量。

### 6. 测试

`test/test_milvus_store_offline.py`：增强 `test_builds_bm25_schema_and_indexes` 断言 `vector index_type=HNSW` + M/efConstruction 参数；新增 `build_dense_index_params`/`build_dense_search_params` 分发用例 + 默认 HNSW+BM25 断言。

### 7. `scripts/ingest_all_jsonl.py` 加 `--recreate`

`--recreate` 时先 `manager.clear_collection()`（实验前缀内可安全删，受保护名 `public_kb` 会被拒绝）再走既有重建链路（集合不存在时 `add_documents` 自动按新 settings 建 schema+索引）。

### 8. 文档

本方案落档 + `docs/plan1/文本分块优化计划.md` §4.2 + `docs/project_overview.md` §5.2 索引描述更新。

---

## 四、重建与验证

```bash
# 前置：.env 增加 ENABLE_MILVUS_BM25=true
./.venv/Scripts/python.exe -m pytest test/ -q                     # G5 全量回归

./.venv/Scripts/python.exe scripts/ingest_all_jsonl.py DATA/work/chunks \
    --collection public_kb_chunked_v2 --recreate                  # 重建（drop→新 schema→全量重灌）

./.venv/Scripts/python.exe scripts/diag_collection_inspect.py --collection public_kb_chunked_v2
./.venv/Scripts/python.exe scripts/diag_retrieval_smoke.py "政府采购公开招标的公告期限是多少天？"
```

**门禁**：
- G1 结构：`vector` 索引 `HNSW`（Finished, 14,216/14,216）、`sparse_vector` 字段 + BM25 Function 存在、`sparse_vector` 索引 `SPARSE_INVERTED_INDEX` Finished。
- G2 行数：`row_count` 与 `count(*)` 均 14,216。
- G3 检索模式：冒烟 `retrieval_mode=HYBRID_RRF`，`fallback_reason` 为空。
- G4 幂等：再跑 `ingest_all_jsonl`（无 --recreate）写入 0，行数不变。
- G5 回归：pytest 全量 passed。

---

## 五、执行记录

2026-09-08 执行完毕，**五道门禁全绿**：

- **G1 集合结构** ✅ 重建后 `vector` 索引 `HNSW`（COSINE, M=16, efConstruction=128, 14,216/14,216 Finished）；`sparse_vector` 字段 + BM25 Function `text_bm25_emb`（text→sparse_vector）存在；`sparse_vector` 索引 `SPARSE_INVERTED_INDEX`（DAAT_MAXSCORE, k1=1.2, b=0.75, Finished）；`chunk_uid`/`doc_type` INVERTED Finished。
- **G2 行数** ✅ `get_collection_stats.row_count` 与 `query count(*)` 均 **14,216**；doc_type 分布 law 8,964 / qa 2,179 / law_compilation 2,080 / book_practical 993（与重建前一致，零增零损）。
- **G3 检索模式** ✅ 冒烟 `retrieval_mode=HYBRID_RERANK`、`fallback_reason=None`（此前纯稠密为 `DENSE_NATIVE / sparse_vector_field_missing`）。"政府采购公开招标的公告期限是多少天？" top-1 直接命中《1200问》第 73 问「公开招标公告的公告期限为 5 个工作日」（score 0.9951，纯稠密时代仅 0.73）。
- **G4 幂等** ✅ 无 `--recreate` 重跑 0 写入、行数不变。
- **G5 回归** ✅ pytest 全量 **806 passed / 10 skipped**（基线 803 + 新增索引参数分发用例 3）。

**过程坑（已修）**：`--recreate` 首跑 0 写入——残留 `*.progress.json` 进度文件让断点续传跳过全部行、集合未被重建。修复：`--recreate` 时同步删除 `chunks_dir` 下进度文件（`scripts/ingest_all_jsonl.py`）。

**执行命令**：
```bash
./.venv/Scripts/python.exe scripts/ingest_all_jsonl.py DATA/work/chunks \
    --collection public_kb_chunked_v2 --recreate
# 完成: 352 文件, 14216 行, 本次写入 14216 条；集合总行数约 14216
```

---

## 六、风险与回滚

- 重建丢弃现集合：数据可 40s 级从 `DATA/work/chunks/*.documents.jsonl` 重建，无持久性风险。
- `enable_bm25` 默认翻转：检索端对无 sparse 集合自动降级纯稠密；`add_documents` 对旧 dense-only 集合会因缺 sparse 字段/索引显式报错（fail-loud）。
- HNSW + COSINE：Milvus v2.6.23 原生支持。
- 回滚：代码 `git checkout` 回退；集合用旧代码 + 无 `--recreate` 重跑回 IVF_FLAT 纯稠密（需 `enable_bm25` 设回 False）。
