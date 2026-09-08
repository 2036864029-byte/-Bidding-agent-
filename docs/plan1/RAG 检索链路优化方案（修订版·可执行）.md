# RAG 检索链路优化方案（修订版·可执行）

> 修订日期：2026-09-08
> 原方案：`docs/plan1/RAG 检索链路优化方案.md`（同日，基于 Milvus 2.6 分组检索 + 子块打分 + 父块返回）
> 修订依据：全链路审查复核（`docs/plan1/链路审查问题清单（数据清洗-分块-入库）.md`）+ 对本地集合 `public_kb_chunked_v2` 的真实实测（见 §二，全部可复现）+ 细节点复审（`docs/plan1/可以补充确认的细节点（非原则性错误）.md`，并入记录见 §九）
> 关联：`docs/plan1/向量索引重构执行方案（HNSW+BM25）.md`、`docs/plan1/文本分块优化执行方案（修订版）.md`

---

## 一、结论与修订摘要

**方向保留，机制已实证有效**：原方案"召回层按父块去重 + 打分用子块 + 输出用父块"的核心思路成立，实测能把「什么是政府采购？」的 top-5 从"1 个父块的 5 个碎片"变成"5 个不同来源"。

**但原方案的三个前提在当前代码/数据上不成立**，照原文执行会在第一步静默失败：

| # | 原方案断言 | 实测/代码现状 | 修订动作 |
| --- | --- | --- | --- |
| 1 | §2.1「仅需更新 1 个标量字段，无需重新入库/embedding/重建索引」 | `parent_chunk_id` **当前不是声明字段**（集合只声明了 7 个通用标量，`milvus_client.py:134-140`），它落在动态字段 `$meta` 里 | 升为显式声明 + INVERTED；改分块器后 `--recreate` 全量重建（40s 级） |
| 2 | §4.1 `group_by_field="parent_chunk_id"` 直接可用 | 对**动态字段**分组被服务端**静默忽略**（不报错，分组后同一父块仍出现 16 条）；对**声明字段**分组正常（`doc_name` 分组 limit=30 → 30 个不同组） | 必须先做 P0 schema 改造；分组参数经 `hybrid_search` 一处透传 |
| 3 | §2.2 存量修复用 `upsert` 只写 `chunk_uid + parent_chunk_id`，分钟级 | 服务端直接拒绝：`DataNotMatchException: Insert missed an field 'text' to collection without set nullable==true or set default_value`（upsert 是删除+插入，必须全字段） | 放弃原地 upsert；由 P0-1 分块器在源头写对，配合 P0-3 重建 |
| 4 | §4.2 BM25 业务层去重 + §4.3 Python RRF 融合 | 实际是 **Milvus 服务端** `hybrid_search` + `RRFRanker`（`retriever.py:140-147`），融合在服务端完成 | **删除这两节**；分组只需在 `hybrid_search` 一处传参，改动比原文更小 |
| 5 | §2.2 伪代码 `expr="parent_chunk_id == 0"` | 独立块在过滤口径上是 **NULL**：`parent_chunk_id is null` = 11,307 行，`== ""` = 0 行 | 回填口径改 `is null`；回填由 P0-1 保证，不做存量 upsert |
| 6 | §4.5「引用算一条」 | `citations_build.py:61` 引用原文取 `doc.page_content`；`scripts/run_knowledge_citation_eval.py:105` 回表校验 `entity_text != citation.text → text_mismatch` | 拼接文本放 `metadata["context_text"]`，**`page_content` 保持子块原文**；引用契约扩展列 P3 下一期 |
| 7 | §4.5 长度熔断 2000 字符 | 554 个父组中 **158 组（28.5%）拼接 >2000 字符**，5 组 >8000（最大 13,863） | 阈值提至 6000，超限保留命中子块 ±1 邻居 |
| 8 | §六 多样性「~20% → 100%」 | 实测现状：融合 30 条候选只有 **6 个不同父块（20%）**；按父去重后 100% | 指标保留，作为 G3 门禁 |

---

## 二、实测证据（本次复核，可复现）

环境：本地 Milvus v2.6.23（`127.0.0.1:19531`）、pymilvus 3.0.1、集合 `public_kb_chunked_v2`（14,216 行）。

| 验证项 | 方法 | 结果 |
| --- | --- | --- |
| 问题 #1 复现 | 真实链路（bge-m3 embedding + hybrid_search + RRF + bge-reranker）跑「什么是政府采购？」 | top-5 全为 `qa_003\|sec_1` 的 seq 1/2/6/5/7，分数 0.9955~0.9988；融合 30 条仅 6 个不同父块 |
| 方案机制有效性 | 融合 200 → 按 parent 去重保最高分 → 取 30 → rerank | top-5 变为 **5 个不同来源**（book_002、lawcomp_001、qa_003\|sec_63、独立块、qa_003\|sec_1） |
| 动态字段分组 | `search(group_by_field='parent_chunk_id', group_size=1, limit=30)` | **静默忽略**：返回 30 条，同一父块最多 16 条，无报错 |
| 声明字段分组 | `search(group_by_field='doc_name', group_size=1, limit=30)` | **正确**：30 条 = 30 个不同 doc_name；`limit=5` → 5 组；`group_size=3` → 49 条 / 30 组 / 每组 ≤3 |
| 混合检索分组 | `hybrid_search(group_by_field='doc_name', limit=30)` | **支持**：30 条 = 30 个不同组（Milvus 内部自动扩取候选池，无需放大 per-channel limit） |
| 部分字段 upsert | 探针集合上 `upsert([{id, parent_chunk_id}])` | **失败**：`Insert missed an field 'text' ... without set nullable==true or set default_value` |
| 加字段迁移 | `add_collection_field(..., nullable=True)` 于有数据的集合 | **可用**（但新字段对存量行为 NULL，仍须回填） |
| 动态字段过滤 | `query(filter='parent_chunk_id in [...]')` | **可用**，取回 20 行（拼接层批量取子块可行） |
| 数据形态 | 全量统计 | 554 个父组 / 2,909 个子块 / 11,307 个独立块；父组最大 107 子块；拼接长度中位 1,364 / p90 3,323 / p95 4,291 / p99 6,597 / 最大 13,863 |
| 分组执行时机 | pymilvus 协议层 + 行为实测 | `group_by_field`/`group_size`/`strict_group_size` 写入 `request.rank_params`（`pymilvus/client/prepare.py:2032-2039`，与 ranker 的 `limit`/`offset` 同一参数块）→ 属 **ranker/reduce 阶段**参数 → 顺序为「dense+sparse 召回 → RRF 融合 → 对融合结果分组」，即**融合后分组**，非分路分组 |
| rerank 延迟 | 真实 SiliconFlow rerank，同一 query 递增条数 | 5 条 394ms / 10 条 394ms / 30 条 710ms / 60 条 1,029ms → 固定开销约 390ms、边际约 11ms/条；**30→60 条约 +320ms（+45%），非线性翻倍** |
| 字段长度 | 全量 14,216 行 | `chunk_uid` 最长 **35**（`ck-` + md5 32 位，`chunk_ids.py:78`）、`parent_chunk_id` 最长 **20** → VARCHAR(64) 余量 29 字符，够用 |
| 附带发现 | 全量 jsonl 扫描 | `effective_date` / `status` **14,216 行 100% 为空**（`chunk_sources.py:100-106` 调用 `chunker.chunk()` 未传这两个参数）→ 时效过滤即使打开也是空转 |

---

## 三、分阶段执行

### P0 前置改造（必须先行；不做则 P1 静默失效）

#### P0-1 分块器：独立块 parent 指向自身

`public_kb/ingestion/transforms/typed_chunker.py`

- 在 `_build_doc`（`:487` 计算 `chunk_uid` 之后）追加：

  ```python
  meta["chunk_uid"] = compute_chunk_uid(page_content, meta)
  if not meta.get("parent_chunk_id"):
      meta["parent_chunk_id"] = meta["chunk_uid"]
  ```

- 一处改动同时覆盖两个 atom 出口（`:439` 普通原子块、`:502` 表格原子块），子块路径（`:457`）不受影响。
- **关键**：`parent_chunk_id` 不参与 `compute_chunk_uid` 的哈希输入（`chunk_ids.py:61-78` 只用 `doc_name|chapter|chunk_index|text`），因此**重建后 uid 与现集合逐行一致**，不产生 uid 漂移、不影响幂等判重与引用测评基线。
- **测试影响面**：`test/ingestion/test_typed_chunker.py:57` 断言独立块 `parent_chunk_id == ""`，需改为 `== chunk_uid`；同时新增"独立块 parent == 自身 uid、子块 parent 仍为合成 id"用例。

#### P0-2 Schema 与契约校验

`core/vectorstore/milvus_client.py`

| 位置 | 改动 |
| --- | --- |
| `:134-140` | 新增声明 `parent_chunk_id` VARCHAR(64)、`chunk_type` VARCHAR(16)、`sequence_in_parent` INT64（值域与现有写入一致，`_build_records:367-369` 不改） |
| `:177-184` | 新增 `parent_chunk_id`、`chunk_type` 的 INVERTED 标量索引 |
| `:187-220` | `_validate_collection_contract` 把上述字段与索引纳入 `required`（顺带补 `chunk_uid`/`doc_type` 索引校验，收口审查清单 #14——本次实测的"静默忽略"正是这类缺位校验放过去的） |
| `:359-360` | 顺带加长度守卫：`chapter`/`hierarchy_path` 超 512 字符截断并告警（收口清单 #7）；同一处对 `parent_chunk_id` 加 ≤64 长度断言（实测最长 20，属提前规避） |

#### P0-3 重新分块 → 重建集合（两步，顺序不可颠倒）

> **⚠️ 顺序陷阱**：`DATA/work/chunks/*.documents.jsonl` 是**旧分块器**的产物（独立块 `parent_chunk_id=""`）。若只跑 `--recreate`，等于用旧 jsonl 灌库，P0-1 的改动完全无效。**必须先重跑分块。**

```bash
# 1) 重新分块（349 法规 + 3 书；源目录与《文本分块优化执行方案（修订版）》阶段 3 一致）
./.venv/Scripts/python.exe -m public_kb.ingestion.chunk_sources \
    --law-dir DATA/raw_data/cleaned_v1/markdown \
    --book-dir DATA/raw_data/handoff_v3/clean \
    --out-dir DATA/work/chunks

# 2) 重建集合
./.venv/Scripts/python.exe scripts/ingest_all_jsonl.py DATA/work/chunks \
    --collection public_kb_chunked_v2 --recreate
```

预期：分块仍 14,216 块（同代码同输入，`chunk_sources.py:81` 的 `files = sorted(...)` 保证 doc_id 稳定）；`parent_chunk_id` 非空率 100%（独立块 = 自身 uid，子块 = 合成父 id）；uid 与重建前逐行一致。

### P1 分组召回（召回层去重）

- `public_kb/retrieval/milvus_search.py:54-84`：`hybrid_search_with_full_fields` 增加 `group_by_field: str | None = None`、`group_size: int | None = None` 形参并透传（为 None 时行为与现在完全一致）。
- `public_kb/retrieval/retriever.py:140-147`：`_hybrid_retrieve` 在 `settings.retrieval_group_by_parent` 为真时传 `group_by_field="parent_chunk_id"`、`group_size=settings.retrieval_group_size`。
- **参数语义变更**：`hybrid_fusion_limit=30` 此时表示"30 个**父组**"，每组最多 `group_size` 个子块（默认 2）→ 最多 60 条进 rerank。Milvus 内部自动扩取候选池，**不需要**放大 `hybrid_dense_limit`/`hybrid_sparse_limit`（已实测）。
- **执行时机已确认（融合后分组）**：`group_by_field` 等参数落在 `request.rank_params`（`pymilvus/client/prepare.py:2032-2039`），由 ranker 阶段生效 → 顺序为「dense+sparse 召回 → RRF 融合 → 对融合结果分组取 top 组」，与本方案"融合后按父去重"的语义一致，无偏差。
- **两条路径统一**：分组参数只作用于 hybrid 路径（Milvus 参数）；**同父去重 + 父块拼接统一放在 `retrieve()` 出口**（`retriever.py:65-111`），使 `_dense_fallback` 降级路径同样受益。检索入口只有 `generation/chain.py:69` 与 `tools.py:154` 两处，出口改造即全覆盖。
- `public_kb/retrieval/retriever.py:191-220` `_rerank_result`：阈值过滤后按 `parent_chunk_id` 去重（保留得分最高者），再取 `retrieval_top_k`。**该兜底无条件保留**（不因 `group_size=1` 而关闭）——分组静默失效时它是唯一护栏，成本仅一个 set。
- **`group_size` 的取舍（实测）**：`group_size` 不影响内容完整性（P2 拼接反正取回全部子块），只影响**父块排序分**（父块得分 = 代表子块的 rerank 分）。实测 rerank 30 条 710ms / 60 条 1,029ms，即 `group_size=2` 比 `1` 多约 +320ms（+45%）。默认取 2（排序质量优先），延迟敏感场景设 1。

### P2 父块拼接（上下文层补全）

- 新增 `public_kb/retrieval/parent_assembly.py`：

  1. 输入：rerank 后的 top-K 文档（含 `chunk_type`/`parent_chunk_id`/`sequence_in_parent`）。
  2. 仅对 `chunk_type == "child"` 的条目批量取子块：`query(filter='parent_chunk_id in [...]', output_fields=['chunk_uid','parent_chunk_id','sequence_in_parent','text'])`（实测可用）。
  3. 按 `sequence_in_parent` 升序拼接；QA 前缀「问：xxx？\n\n」只保留首个，其余子块剥前缀。
  4. 长度熔断 `retrieval_parent_max_chars`（默认 6000），确定行为：
     - 优先保留「命中子块 + 前后各 1 块」；命中块位于首/末时，缺失的一侧不取（不补、不回绕）；
     - 若取满 3 块后仍超阈值，则**仅保留命中子块本身**；
     - 两种降级都打日志（含 `parent_chunk_id`、原始长度、保留块数），供 G7 埋点统计。
  5. 结果写入 `doc.metadata["context_text"]`，**不改 `page_content`**；在 `HybridRetriever.retrieve()` 出口对 `RetrievalResult.docs` 就地调用（hybrid 与 dense-fallback 两条路径统一）。
  6. 独立块（`chunk_type == "atom"`，`parent_chunk_id == 自身 uid`）直接透传。

- `public_kb/rendering/context.py:30`：`format_docs` 改用 `doc.metadata.get("context_text") or doc.page_content`。
- 开关 `settings.retrieval_parent_assembly`（默认 True）关闭时完全跳过。

### P3 引用契约（本期最小化）

- 本期**不动引用原文口径**：`citations_build.py:61` 仍取子块 `page_content` → 回表校验（`text_mismatch`）继续通过。
- 仅在引用元数据补 `parent_chunk_id`（`citations_models.py:31` 附近 + `citations_build.py:46` 白名单），便于溯源"这条引用属于哪个父块"。
- **下一期（不在本次范围）**：若要"一条引用 = 一个完整父块"，需同步改 `citations_models`/`citations_validate` R1/R2/R4 与 `run_knowledge_citation_eval.py` 的回表逻辑（改为校验"拼接文本 == 子块按序拼接"），属契约级变更，单独排期。

---

## 四、参数与开关（`core/config/settings.py`）

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `retrieval_group_by_parent` | `True` | 检索侧按父块分组（关 → 回到当前行为） |
| `retrieval_group_size` | `2` | 每组保留子块数。**不影响内容完整性**（拼接阶段取回全部子块），只影响父块排序分；实测 rerank 30 条 710ms / 60 条 1,029ms，设 1 可省约 320ms |
| `retrieval_parent_assembly` | `True` | 父块拼接开关 |
| `retrieval_parent_max_chars` | `6000` | 拼接长度熔断（实测 28.5% 父组 >2000，故不用原方案的 2000） |
| `hybrid_fusion_limit` | `30` | **语义变更**：启用分组后表示"30 个父组" |

---

## 五、验收门禁

| 门禁 | 内容 | 判定 |
| --- | --- | --- |
| G1 结构 | `parent_chunk_id`/`chunk_type`/`sequence_in_parent` 为声明字段；`parent_chunk_id`/`chunk_type` INVERTED 索引 Finished | `scripts/diag_collection_inspect.py` |
| G2 数据 | 行数 14,216；`parent_chunk_id is null` = 0；child 2,909 / atom 11,307；uid 与重建前逐行一致 | 全量对比 |
| G3 分组生效 | 「什么是政府采购？」top-5 的 `parent_chunk_id` 互不相同（改造前为 1 个） | `scripts/diag_retrieval_smoke.py` |
| G4 无召回损失 | 独立块用例「政府采购公开招标的公告期限是多少天？」top-1 仍命中《1200问》第 73 问（5 个工作日） | 冒烟脚本 |
| G5 引用回归 | `scripts/run_knowledge_citation_eval.py` 回表校验 0 `text_mismatch`（与改造前基线一致） | 引用测评 |
| G6 全量回归 | `python -m pytest test/ -q` 全绿（基线 806 passed / 10 skipped） | pytest |
| G7 延迟与上下文埋点 | 每轮记录：rerank 候选数（≤60）、端到端检索耗时、top-K 拼接后总字符数、熔断触发条数；总长 >20,000 字符告警（**只告警不截断**） | 日志 / 冒烟输出 |

---

## 六、风险与回滚

- **静默失效风险（最高）**：分组依赖声明字段，若迁移未完成就开开关，服务端不报错但分组无效。兜底：`_validate_collection_contract` 新增字段/索引校验（fail-loud）+ `_rerank_result` 的同父去重兜底。
- **上下文膨胀**：父块拼接长度中位 1,364 / p95 4,291 / p99 6,597 / 最大 13,863 字符，靠 `retrieval_parent_max_chars=6000` 熔断（约影响 5% 父组）；极端情况 top-5 全为最长父块时总长 53,981 字符、中位情况约 6,820。本期**只加埋点不截断**（G7），观察到真实分布后再决定是否上硬预算。
- **延迟**：拼接层每轮多 1 次 Milvus query（本集合规模下 ~10-30ms）；rerank 是当前最重的一步（30 条 ~710ms、60 条 ~1,029ms，远程 API 主导），`group_size` 是其主要放大因子。
- **回滚**：`retrieval_group_by_parent=False` + `retrieval_parent_assembly=False` 即回到当前行为（无需回退代码）；集合可从 `DATA/work/chunks/*.documents.jsonl` 40s 级重建。

---

## 七、排期

| 阶段 | 内容 | 预估 |
| --- | --- | --- |
| P0 | 分块器 + 测试断言 + schema + 契约校验 + **重跑分块** + 重建 | 0.5 天 |
| P1 | 分组召回 + 同父去重兜底 | 0.5 天 |
| P2 | 父块拼接模块 | 1 天 |
| P3 | 引用元数据补充 + G1~G6 门禁 | 0.5 天 |

前置依赖：无（不依赖表格提取路线）。

---

## 八、本期边界（不做）

- **审查 #2 表格原子块超限**：当前 0 表格块（MinerU 未输出管道表格），表格路线仍处暂停（《表格提取试点报告_20260905》）。**表格提取重启前必修**，本期不排。
- **审查 #3 chunk_uid 去 `chunk_index`**：实测按 `(doc_name, chapter, text)` 聚合有 30 个键/43 行仅靠 `chunk_index` 区分，直接剔除会误判重；需另设计位置无关键，单独排期。
- **`effective_date`/`status` 全空**（附带发现）：时效过滤（`enable_effective_filter`）当前打开也是空转。需单独决策：从法规元数据回填施行日期，或明确放弃该能力。
- 审查 #5/#6/#8/#10~#13：提示级，按原清单排期。

---

## 九、细节点复审并入记录（2026-09-08）

来源：`docs/plan1/可以补充确认的细节点（非原则性错误）.md`（5 条，均属非原则性收口细节）。逐条结论与并入位置：

| 建议 | 结论 | 并入位置 / 修正 |
| --- | --- | --- |
| 1. `group_size=2` 的性能权衡 | 采纳，数字修正 | §二（延迟实测）、§三 P1、§四。实测 30→60 条为 **+320ms（+45%）**，非线性翻倍；"`group_size=1` 时可关掉兜底去重"**不采纳**——兜底去重是分组静默失效时的唯一护栏，无条件保留 |
| 2. 熔断 ±1 邻居边界 | 采纳 | §三 P2 步骤 4：缺边不取；仍超阈值则仅保留命中子块本身；降级打日志 |
| 3. `hybrid_search` 分组时机 | 采纳（确认为融合后分组） | §二（协议层证据）、§三 P1：参数落在 `rank_params`，由 ranker 阶段生效 |
| 4. 字段长度匹配 | 采纳，结论修正 | §三 P0-2：实测 uid 35 字符、parent 20 字符，VARCHAR(64) **已够**（非"需放宽到 128"），顺手加长度断言 |
| 5. 总上下文预算观察项 | 采纳 | §五 G7（埋点 + 20,000 字符软告警）、§六（数据支撑） |

---

## 十、执行记录（2026-09-08，P0~P3 全落地）

**状态：P0/P1/P2/P3 全部完成，G1~G7 全绿**（未提交）。

### 代码改动

| 文件 | 改动 |
| --- | --- |
| `public_kb/ingestion/transforms/typed_chunker.py:487-491` | `_build_doc` 中空 `parent_chunk_id` 回填为 `chunk_uid`（一处覆盖两个 atom 出口） |
| `core/vectorstore/milvus_client.py:134-153` | 声明 `parent_chunk_id`(64)/`chunk_type`(16)/`sequence_in_parent` 三个字段 |
| `core/vectorstore/milvus_client.py:186-198` | 新增 `parent_chunk_id`/`chunk_type` INVERTED 索引 |
| `core/vectorstore/milvus_client.py:206-236` | 契约校验纳入 10 个标量字段与 5 个索引（收口审查 #14） |
| `core/vectorstore/milvus_client.py:33-48, 375-400` | `_truncate_field` 守卫（chapter/hierarchy_path 超 512 截断+告警，收口 #7）+ `parent_chunk_id` 超 64 报错 |
| `core/config/settings.py:355-362` | 新增 `retrieval_group_by_parent` / `retrieval_group_size` / `retrieval_parent_assembly` / `retrieval_parent_max_chars` / `retrieval_context_warn_chars` |
| `public_kb/retrieval/milvus_search.py:54-90` | `hybrid_search_with_full_fields` 透传 `group_by_field`/`group_size` |
| `public_kb/retrieval/retriever.py:65-72, 121-189` | `retrieve()` 包装 + 分组参数 + 同父去重（rerank 与 RRF 两条路径）+ 父块拼接调用 + G7 埋点 |
| `public_kb/retrieval/parent_assembly.py`（新增） | 父块拼接：批量取子块 → 按 sequence 拼接 → QA 前缀去重 → ±1 邻居降级 → 仅命中子块；另提供 `is_duplicate_parent`/`dedupe_by_parent` 供两条路径共用 |
| `public_kb/retrieval/fallback.py:58-68, 84-90` | dense 降级路径（pymilvus 与 langchain 两个分支）在同父去重后再截断 top_k，使降级路径同样受益 |
| `public_kb/rendering/context.py:28-36` | `format_docs` 优先 `context_text`，回退 `page_content` |
| `test/ingestion/test_typed_chunker.py` | 改 1 处断言 + 新增父子块不变量用例 |
| `test/test_milvus_store_offline.py` | 改 2 处 schema/索引断言 + 新增截断/超长报错用例 |
| `test/test_parent_assembly.py`（新增 10 例） | 拼接/前缀去重/熔断降级/atom 透传/候选数扩展/去重开关/引用元数据/降级去重 |

**P3 结论修正**：`citations_build` 的元数据透传已覆盖 `parent_chunk_id`（白名单只排除固定字段），**无需改代码**，仅补回归测试锁定。

### 门禁结果

- **G1 结构** ✅ 三字段为声明字段（`parent_chunk_id` max_length=64）；`parent_chunk_id`/`chunk_type` INVERTED 索引 Finished（14,216/14,216）。
- **G2 数据** ✅ 14,216 行；`parent_chunk_id` 为空 **0** 条；atom 11,307（全部 parent==自身 uid）/ child 2,909（全部合成父标识）；**uid 与重建前零漂移**（新增 0/缺失 0）；doc_type 分布 law 8,964 / qa 2,179 / law_compilation 2,080 / book_practical 993 与改造前一致；最长 `parent_chunk_id` 35 字符。
- **G3 分组生效** ✅ 「什么是政府采购？」top-5 的 `parent_chunk_id` 由改造前的 **1 个** 变为 **5 个不同来源**（法规 / 实务书 / 1200问 / 办法 / 法规全书）。
- **G4 无召回损失** ✅ 「政府采购公开招标的公告期限是多少天？」top-1 仍为《1200问》第 73 问「公开招标公告的公告期限为5个工作日」（score 0.9951）。
- **G5 引用回归** ✅ 6 个问题 30 条引用回表校验全部通过（`text_mismatch` 0）。**注**：原脚本依赖的 `testset_knowledge.jsonl` 已不在仓库，改用等效校验（复用脚本内 `_AssociationChecker` + 真实 `kb_search` 结果）。
- **G6 全量回归** ✅ **819 passed / 10 skipped**（基线 806 + 新增 13）。
- **G7 埋点** ✅ 输出示例：`检索上下文埋点: 候选=36 条, 返回=5 条, 拼接父块=3 条, 总字符=4003, 耗时=1190ms`。

### 执行顺序（实际）

1. 改分块器 + 测试 → 离线测试 26 passed → 全量 809 passed；
2. 抓重建前 uid 基线（14,216 唯一 uid）→ `chunk_sources` 重跑分块（352 文件 / 14,216 块 / 0 失败）→ 校验 uid 零漂移、parent 不变量 100%；
3. `ingest_all_jsonl --recreate` 重建（写入 14,216 条）→ G1/G2；
4. P1/P2/P3 代码 + 新增 9 例测试 → G3~G7。

### 回滚

`retrieval_group_by_parent=False` + `retrieval_parent_assembly=False` 即回到改造前行为；集合可从 `DATA/work/chunks/*.documents.jsonl` 40s 级重建（当前 jsonl 已是新分块器产物）。


