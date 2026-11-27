# 招投标智能助手 — RAG 链路技术详解

> 文档版本：v1.0　|　编制日期：2026-09-16　|　适用范围：技术汇报 / 架构评审
>
> 本文聚焦项目中的 **RAG（检索增强生成）链路**：给出整体架构图与指标走势图，逐模块拆解实现，并说明每一段采用的算法与其参数。文中所有配置数值取自 `core/config/settings.py`，所有评测数值取自 `test_report/` 下的真实产物（已标注文件名）。

---

## 目录

- [1. 链路总览](#1-链路总览)
- [2. 离线入库链路](#2-离线入库链路)
- [3. 在线检索链路](#3-在线检索链路)
- [4. 多跳召回 L0 / L1 / L2](#4-多跳召回-l0--l1--l2)
- [5. 生成链路](#5-生成链路)
- [6. 引用溯源护栏 R1~R7](#6-引用溯源护栏-r1r7)
- [7. 算法总表](#7-算法总表)
- [8. 实测数据与指标走势](#8-实测数据与指标走势)
- [9. 已知缺口与优化方向](#9-已知缺口与优化方向)

---

## 1. 链路总览

RAG 链路分为**离线入库**与**在线问答**两条通道，二者通过 Milvus 集合 `public_kb` 衔接。

```mermaid
%% 图1 RAG 链路整体架构
```

**链路关键指标（生产口径）**

| 指标 | 数值 | 来源 |
| :--- | :--- | :--- |
| 检索层 hit_rate（全量） | 0.955 | `retrieval_layer_gate_20260910_224225.json` |
| 检索层 recall（全量） | 0.863 | 同上 |
| 多跳双段全中率 | 0.4429 → **0.6000** | 同上（L0 → L1） |
| RAGAS faithfulness | 0.8073 → **0.8860** | `ragas_s1_20260910_144219/summary.json` |
| knowledge_qa 端到端 P95 | 10.0 s | `system_metrics_20260911_065232/summary.json` |
| 引用率 citation.rate | 0.0 → **0.9688** | `agent_trace_20260916_*` |

### 1.1 代码落点

| 链路段 | 主要目录 | 关键入口 |
| :--- | :--- | :--- |
| 离线入库 | `public_kb/ingestion/` | `pipeline.py::FileIngestionPipeline` |
| 检索 | `public_kb/retrieval/` | `retriever.py::HybridRetriever` |
| 生成 | `public_kb/generation/` + `rendering/` | `rendering/prompts.py::build_prompt` |
| 引用护栏 | `public_kb/guardrails/` | `citations_validate.py::CitationValidator` |
| Agent 封装 | `agent/nodes/knowledge_qa/` | `subgraph.py::build_knowledge_qa_subgraph` |

---

## 2. 离线入库链路

### 2.1 格式路由（Source Router）

`ingestion/router.py::FormatRouter` 先按扩展名判定，扩展名缺失或不可信时用 `_sniff_format` 做**内容嗅探**（magic bytes），产出 `SourceFormat`：`pdf` / `csv` / `text` / `image`。图像类走 RapidOCR 转文本后并入文本通道。

### 2.2 PDF 三档路由（核心）

PDF 是本项目最主要的知识源（法规、汇编、实务书）。不同版式的成本差异巨大，因此先做**页面画像**再分档，避免"简单页也送重解析"。

**页面画像**：`normalizers/pdf/pdf_page_profile.py::build_page_profile` → `PageProfile`，采集指标包括：`text_chars`、`n_blocks`、`x_starts`、`two_col_gap` / `split_x` / `has_two_col`、`table_hlines` / `table_vlines` / `table_candidate`、`img_ratio`、`article_count`、`formula_hint`、`fonts`。

**判定规则**：`pdf_legal_page_classifier.py::LegalPageClassifier.classify` → `PageRouteDecision`

| 优先级 | 条件 | 路由 | 解析器 |
| :--- | :--- | :--- | :--- |
| 1 | `text_chars < 80` | Tier C | MinerU（远程 HTTP） |
| 2 | `img_ratio > 0.35` **且** `text_chars < 200` | Tier C | MinerU |
| 3 | `formula_hint` 命中 | Tier C | MinerU |
| 4 | 表格候选且 `hlines ≥ 2` 且 `vlines ≥ 2` | Tier B → **降级 C** | Tier B 未实现 |
| 5 | 双栏且置信度 ≥ 0.80 | Tier A | 本地 PyMuPDF |
| 6 | 其余（单栏） | Tier A | 本地 PyMuPDF |

**双栏置信度公式**（`balance` 为左右栏块数均衡度，`side_min` 为较少一侧的块数）：

```
confidence = max(0.55 + 0.4 × balance, 0.55 + 0.4 × min(1, side_min / 5))
balance    = 1 − |left − right| / n_blocks
```

MinerU 结果按 `md5(源 PDF 字节 | 页码 | parser_version | range_id)` 缓存，避免重复计费。

> 编排入口 `pdf_router.py`，总开关 `pdf_tiered_routing_enabled` **代码默认 false**，但 `.env` 置 true（生产实际开启）。

### 2.3 清洗与类型化分块

**清洗**：`transforms/cleaner.py::TextCleaner` 负责页眉页脚、噪声字符、连续空行等规整。

**分块**：主力是 `transforms/typed_chunker.py::TypedChunker`，采用**两层切分**：

1. **第一层 `HeadingSplitter`**——按 Markdown 标题切分到语义层级，内部用 `_content_level` 维护标题栈，切分时剥离 `#` 号，保留 `chapter` / `hierarchy_path` 元信息（**标题感知**）。
2. **第二层 `OversizeSplitter`**——对仍超阈值的块，依次尝试：次级标题 → 段落 → 句子，逐级降级切分。

**各文档类型阈值**（字符数）：

| 文档类型 | 阈值 | 说明 |
| :--- | :--- | :--- |
| `law`（法规） | 700 | 条款粒度，一条一款 |
| `law_compilation`（法规汇编） | 700 | 同上 |
| `qa`（问答） | 900 | 一问一答成对保留 |
| `book_practical`（实务书） | 1000 | 论述段落较长 |

> **注意**：`TypedChunker` **无 overlap**（overlap=100 属于旧版 `SemanticChunker`，仅在未识别出 `doc_type` 时回落使用）。生产主路径是 TypedChunker。

**表格原子块**：`pdf_structure.py::split_table_blocks` 将连续 ≥ `pdf_min_table_rows`（2）行以 `|` 开头的行聚为**表格原子段**；`TypedChunker.chunk` 中识别为 `is_table` 后直接 `_make_atom(content_type="table")`，**不参与标题切分、不再细分**——表格被整体保留，避免结构被打散。

### 2.4 向量化与写入

- **Embedding**：`core/embeddings/bge_m3.py::create_embeddings` → `_SafeEmbeddings(OpenAIEmbeddings)`，模型 `BAAI/bge-m3`，1024 维，按 `chunk_max_chars` 截断。
- **批量写入**：`milvus_client.py::_batch_insert` 按 `embedding_batch_size=100` 分批，线程池 `embedding_max_workers=4` 并发。

**Milvus 集合 Schema**（集合名默认 `public_kb`）

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | INT64（auto） | 主键 |
| `text` | VARCHAR(65535) | 正文，chinese analyzer |
| `vector` | FLOAT_VECTOR(1024) | 稠密向量，HNSW / COSINE |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | 由 BM25 Function `text_bm25_emb` **服务端自动生成** |
| `chunk_uid` | VARCHAR | `ck-` + md5，引用溯源主键 |
| `doc_id` / `doc_name` / `doc_type` | VARCHAR | 来源标识 |
| `chapter` / `hierarchy_path` | VARCHAR | 章节与层级路径 |
| `chunk_index` / `parent_chunk_id` | — | 序号与父块关联 |
| `chunk_type` / `sequence_in_parent` | — | 块类型与父块内序号 |

**索引**：稠密 HNSW（`M=16`、`efConstruction=128`、查询 `ef=64`）；稀疏 `SPARSE_INVERTED_INDEX`（DAAT_MAXSCORE，`k1=1.2`、`b=0.75`，中文）；另在 `chunk_uid` / `doc_type` / `parent_chunk_id` / `chunk_type` 建 INVERTED 索引。

---

## 3. 在线检索链路

### 3.1 候选漏斗与算法参数

```mermaid
%% 图2 检索候选漏斗
```

一次检索中候选规模的收敛过程与每段算法：

| 阶段 | 候选数 | 算法 | 关键参数 |
| :--- | :--- | :--- | :--- |
| Dense 检索 | 30 | HNSW 近似最近邻（COSINE） | `ef=64`；IVF_FLAT 时用 `nprobe=32` |
| Sparse 检索 | 30 | **Milvus 服务端 BM25** | `k1=1.2`、`b=0.75` |
| RRF 融合 | 30 | 倒数排序融合 | `k=60`，`group_by_field=parent_chunk_id`、`group_size=2` |
| 自适应阈值 | ≤30 | 分段常量阈值 | 0.40 / 0.45 / 0.50 |
| Rerank 精排 | 11 | Cross-Encoder | `BAAI/bge-reranker-v2-m3`，timeout 30 s，重试 2 次 |
| 父块装配 | ≤11 | 父块拼接 | `parent_max_chars=6000` |

### 3.2 稠密检索（Dense）

通过 pymilvus `AnnSearchRequest` 发起：`anns_field="vector"`，`metric_type=COSINE`，`params={"ef": 64}`，`limit=hybrid_dense_limit=30`。索引为 HNSW——在 1024 维、百万级以下规模下，HNSW 的召回率与延迟优于 IVF_FLAT，且无需训练。

### 3.3 稀疏检索（Sparse）

**关键设计**：稀疏向量**不在客户端计算**，而是由 Milvus 服务端通过 `Function`（`text_bm25_emb`）**自动生成**。应用侧只需传原始问题文本：

```
AnnSearchRequest(data=[question 原文], anns_field="sparse_vector",
                 param={"metric_type": "BM25", "params": {}})
```

**BM25 算法**：对查询 `q` 与文档 `d`，

```
score(q, d) = Σ IDF(ti) × [ f(ti, d) × (k1 + 1) ] / [ f(ti, d) + k1 × (1 − b + b × |d| / avgdl) ]
IDF(ti)     = ln( 1 + (N − df(ti) + 0.5) / (df(ti) + 0.5) )
```

其中 `k1=1.2`（词频饱和度）、`b=0.75`（长度归一化强度）。中文场景依赖 analyzer 分词（chinese analyzer），擅长法规条号、专有名词、金额等**字面精确匹配**——这正是纯语义检索的短板。

**收益**：客户端零稀疏计算、无额外模型、无 IDF 词典维护成本。

### 3.4 RRF 融合

稠密与稀疏两路结果用 **Reciprocal Rank Fusion** 融合，规避两路得分不可比的问题（COSINE 相似度与 BM25 分值量纲不同，直接加权需归一化标定）：

```
RRF_score(d) = Σ  1 / (k + rank_i(d))
               i
```

`k = rrf_k = 60`。融合在 Milvus 服务端完成（`RRFRanker(k=60)`），并支持 `group_by_field="parent_chunk_id"` + `group_size=2` 做同源聚合，避免同一父块的多个子块霸占前排。

### 3.5 自适应阈值

`retrieval/strategies.py::adaptive_threshold(top_score)` 采用**分段常量**策略（非均值/分位数，避免分布假设）：

| Top-1 得分 | 阈值 |
| :--- | :--- |
| ≥ 0.75 | 0.40 |
| ≥ 0.50 | 0.45 |
| 其他 | 0.50 |

**设计意图**：当检索整体置信度高（top-1 很相关）时放宽阈值以纳入更多补充证据；当 top-1 本身就很弱时收紧阈值，宁可少给也不给噪声。降级路径统一使用 `similarity_threshold=0.45`。

### 3.6 Rerank 精排

`retrieval/reranker.py::SiliconFlowReranker.rerank` → `POST {base_url}/rerank`：

- 模型 `BAAI/bge-reranker-v2-m3`（Cross-Encoder，query 与 doc 拼接后整体编码，精度显著高于双塔向量）
- `top_n = min(top_k, len(documents))`，**一次请求送全部候选，无分批**
- timeout 30 s，`max_retries=2`，退避 `0.25 × 2^attempt`
- 可重试错误：ConnectionError / TimeoutError / 429 / 5xx
- 取回条数 `_rerank_candidate_k()`：未分组 11；启用父块分组时 11 × 2 = 22

**实测耗时**（`docs/plan1/RAG 检索链路优化方案（修订版·可执行）.md`）：5 条 394 ms、10 条 394 ms、30 条 710 ms、60 条 1029 ms——固定开销约 390 ms，边际约 11 ms/条。这条曲线是"候选数不宜无限放大"的直接依据。

### 3.7 父块装配

`retrieval/parent_assembly.py`：子块命中后回填父块上下文，避免答案只看到被切断的片段。`parent_assembly=True`、`parent_max_chars=6000`。生成侧优先取 `metadata["context_text"]`（父块拼接结果）。

### 3.8 八级降级与容错

| # | 触发条件 | 行为 |
| :--- | :--- | :--- |
| 1 | 无 collection / embeddings | 纯稠密检索 |
| 2 | 集合无 `sparse_vector` 字段 | 纯稠密（`strict_hybrid_validation` 默认 false，不抛错） |
| 3 | hybrid_search 异常 | 纯稠密，标记 `hybrid_error:*` |
| 4 | rerank 调用失败 | 保留 RRF 序，标记 `reranker_failed` |
| 5 | rerank 成功但返回空 | 标记 `reranker_empty`，回落 RRF 序 |
| 6 | pymilvus 检索失败 | 改用 `langchain_milvus` 实现 |
| 7 | 候选为空 | `RetrievalMode.REFUSAL` |
| 8 | 生成侧 docs 为空 | 拒答（不臆造） |

**原则**：任何单点故障都退化为"能力减弱"而非"服务不可用"；且**只有健康结果才写入缓存**，避免污染。

---

## 4. 多跳召回 L0 / L1 / L2

同一问题涉及多个条款/多个来源时，单查询向量检索会漏召回。项目采用三层递进策略：

```mermaid
%% 图3 多跳召回三层
```

| 层 | 策略 | 实现 | 开关 | 成本 |
| :--- | :--- | :--- | :--- | :--- |
| **L0** | 检索窗口放宽（top_k 5 → 11） | 配置变更，已落地 | 无（默认生效） | 0 额外 LLM |
| **L1** | 统一轻量补检（子句拆分并行检索） | `public_kb/tools.py::kb_search_expanded` | `kb_light_decompose`（默认 **True**） | 0 额外 LLM |
| **L2** | Plan-and-Execute 深度推理 | `agent/nodes/deep_agent.py` | `CAPABILITY_ENABLED=deep_agent`（默认关） | 3 次 LLM / 题 |

**L1 算法**：基线检索取 top-11 → `_split_clauses` 按标点切子句（`min_len=8`）→ 每个子句再检索取 `sub_top_k=5` → 与基线取并集 → 去空白重 → 按 score 降序追加 → 封顶 `max_chunks=20`。

**L2 流程**：PLAN（拆解子问题）→ EXECUTE（并行执行）→ REPLAN（判断是否需要补检索）。`deep_agent_max_exec_rounds=2`、`deep_agent_replan_enabled=True`。

> **边界澄清**：本项目检索链路**没有 HyDE，也没有 LLM 查询改写**。`agent/nodes/rewrite.py` 是**多轮追问的指代消解**，发生在检索之前、用于理解用户意图，不属于检索内改写。

---

## 5. 生成链路

### 5.1 上下文组织

`rendering/context.py::format_docs`：

- 块编号格式为 **`【来源{i}】`**（i 从 1 起），与引用标记一一对应
- 每块附带**文档名 + 章节路径 + 相关度百分比**，便于模型判断可信度
- 优先取 `metadata["context_text"]`（父块拼接），否则退回 `page_content`
- **不做截断**，仅在超过 `context_warn_chars=20000` 时告警
- `build_sources` 中的 snippet 截断 200 字符

### 5.2 Prompt 约束

模板唯一来源 `rendering/prompts.py::build_prompt`：

- **System**：`Settings.system_prompt`——"招投标领域专业顾问……严格依据参考资料，不添加资料外信息；资料不足请明确告知无法回答"
- **内联引用指令**：`INLINE_CITATION_INSTRUCTION`——结论句末标注 `【来源N】`，未使用的来源不标
- **重试模板**：`agent/nodes/knowledge_qa/prompts.py::RETRY_USER_TEMPLATE`，在护栏不通过时追加 `{feedback}` 重生成

### 5.3 子图编排与流式

`agent/nodes/knowledge_qa/subgraph.py::build_knowledge_qa_subgraph`：

```
retrieve → _route_after_retrieve → generate → guardrail → _route_after_guardrail
```

- `_route_after_retrieve`：aborted → END；error → `refuse_error`；chunks 空 → `refuse`；否则 → `generate`
- `_route_after_guardrail`：终态已写 → END；否则回 `generate`，**总生成次数 ≤ 2**（`kb_guardrail_retry=True`，`KB_MAX_ATTEMPTS=2`）
- 与引擎的衔接：`_retrieve_node` 直接调 `public_kb.tools.kb_search_expanded` / `kb_search`，**不经** `PublicKnowledgeRAG.query`
- LLM 槽位 `kb_generation`，使用 `.stream` + `emit_token` 实现打字机效果

**输出契约**（`contracts.validate_qa_result` 校验前四项必填）：`answer` / `sources` / `citations` / `citation_validation` / `retrieval_diagnostics`

---

## 6. 引用溯源护栏 R1~R7

护栏位于 `public_kb/guardrails/`（`citations_models.py` / `citations_build.py` / `citations_validate.py`），采用 **fail-soft**（不通过不抛异常，记录规则后交由上层决定是否重生成）。

| 规则 | 内容 |
| :--- | :--- |
| R1 | `chunk_id` 非空 |
| R2 | `chunk_uid` 非空 |
| R3 | `doc_name` / `chapter` 非空且非占位符 |
| R4 | `text` 非空 |
| R5 | 上下文 chunk 与 citations **双向一致**（无遗漏、无凭空引用） |
| R6 | 回答中的 `【来源N】` 标记全部可解析 |
| R7 | 严格模式：上下文全部被引用（开关 `enforce_all_context_cited`，**默认 False**） |

**chunk_uid 生成**（`core/contracts/chunk_ids.py::compute_chunk_uid`）：

```
chunk_uid = "ck-" + md5( doc_name | chapter | chunk_index | md5(normalize(text))[:16] )
```

`normalize` 统一换行并 strip——保证同一内容重复入库时 uid 稳定，是可追溯的前提。

**Citation 字段**：`context_index`（从 1 起）、`chunk_id`、`chunk_uid`、`doc_name`、`chapter`、`chunk_index`、`text`、`score`、`metadata`。

---

## 7. 算法总表

| 算法 | 类别 | 在链路中的作用 | 关键参数 | 位置 |
| :--- | :--- | :--- | :--- | :--- |
| 页面画像 + 规则分类 | 规则 / 启发式 | PDF 解析分档，控制成本 | `text_chars<80`、`img_ratio>0.35`、置信度≥0.80 | `pdf_page_profile.py` / `pdf_legal_page_classifier.py` |
| 双栏切分置信度 | 启发式评分 | 判定是否双栏及可信度 | `0.55+0.4×balance` | 同上 |
| 标题栈切分 | 结构感知分块 | 保持法规条款完整性 | law 700 / compilation 700 / qa 900 / book 1000 | `typed_chunker.py::HeadingSplitter` |
| 超限时降级切分 | 分块 | 处理超长段落 | 次级标题 → 段落 → 句子 | `OversizeSplitter` |
| 表格原子块 | 结构保护 | 表格不被打散 | `pdf_min_table_rows=2` | `pdf_structure.py` |
| BGE-M3 Embedding | 稠密向量 | 语义表示 | 1024 维，batch 100，workers 4 | `core/embeddings/bge_m3.py` |
| HNSW | ANN 索引 | 稠密检索 | `M=16`、`efConstruction=128`、`ef=64` | Milvus |
| BM25（服务端） | 稀疏检索 | 关键词精确匹配 | `k1=1.2`、`b=0.75` | Milvus Function `text_bm25_emb` |
| RRF | 结果融合 | 稠密 + 稀疏融合 | `k=60` | `RRFRanker` |
| 自适应阈值 | 过滤 | 控制噪声 | 0.40 / 0.45 / 0.50 | `strategies.py::adaptive_threshold` |
| Cross-Encoder Rerank | 精排 | 提升 top-k 精度 | `bge-reranker-v2-m3`，top_n=11 | `reranker.py` |
| 父块装配 | 上下文补全 | 避免片段断裂 | `parent_max_chars=6000` | `parent_assembly.py` |
| 子句拆分补检（L1） | 查询扩展 | 多跳召回 | `min_len=8`、`sub_top_k=5`、`max_chunks=20` | `tools.py::kb_search_expanded` |
| Plan-and-Execute（L2） | Agent 推理 | 复杂多跳 | `max_exec_rounds=2` | `deep_agent.py` |
| chunk_uid 摘要 | 溯源 | 引用唯一标识 | md5 复合摘要 | `core/contracts/chunk_ids.py` |
| R1~R7 校验 | 护栏 | 引用真实性 | fail-soft | `guardrails/citations_validate.py` |

---

## 8. 实测数据与指标走势

### 8.1 检索层（holdout 200 条，top_k=11）

来源：`test_report/retrieval_layer_gate_20260910_224225.json`

```mermaid
%% 图4 检索层指标走势
```

| 口径 | 指标 | L0 | L1 | Δ |
| :--- | :--- | :--- | :--- | :--- |
| multi | 双段全中率 | 0.4429 | **0.6000** | +0.1571 |
| multi | recall | 0.6929 | **0.7857** | +0.0928 |
| multi | hit_rate | 0.9429 | **0.9714** | +0.0285 |
| multi | mrr | 0.8390 | 0.8413 | +0.0023 |
| single | recall / hit_rate | 0.9308 | **0.9462** | +0.0154 |
| single | mrr | 0.8442 | 0.8417 | −0.0025 |
| all | hit_rate / recall | 0.935 / 0.8074 | **0.955 / 0.863** | — |

### 8.2 RAGAS（dev_100，judge = qwen3.8-flash）

来源：`test_report/ragas_s1_20260910_144219/summary.json`

```mermaid
%% 图5 RAGAS L0 vs L1
```

| 指标 | L0 | L1 | Δ | 变差 / 不变 / 变好 | 配对 n |
| :--- | :--- | :--- | :--- | :--- | :--- |
| faithfulness | 0.8073 | **0.8860** | +0.0550 | 6 / 17 / 11 | 34 |
| answer_relevancy | 0.7362 | **0.7679** | +0.0317 | 44 / 12 / 44 | 100 |
| context_recall | 0.8603 | **0.8862** | +0.0258 | 3 / 86 / 11 | 100 |

按 `query_type` 分桶：

| 类型 | n | faithfulness | answer_relevancy | context_recall |
| :--- | :--- | :--- | :--- | :--- |
| multi_hop | 35 | 0.8462 → **0.9544** | 0.6206 → 0.6215 | 0.7452 → 0.7814 |
| single_hop | 65 | 0.7958 → **0.8599** | 0.7985 → 0.8468 | 0.9223 → 0.9426 |

> multi_hop 的 faithfulness 提升最显著（+0.108），说明 L1 补检确实把"漏掉的第二段证据"找回来了；而 answer_relevancy 几乎不动——召回变全了，但答案的表述相关性仍受生成侧约束。

### 8.3 策略 A/B 对比（同一批 35 条多跳）

来源：`docs/plan1/评测与可观测性/评测与可观测性执行计划.md`

```mermaid
%% 图6 策略AB对比
```

| 方案 | recall | 双段全中 | MRR | LLM/题 | 耗时/题 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 单查询 top-5（基线） | 0.6714 | 0.3714 | 0.8524 | ~1 | ~2 s |
| `retrieval_top_k` 5 → 11（L0） | 0.7000 | 0.4286 | 0.8524 | ~1 | ~2 s |
| 确定性子句拆分（L1） | 0.6571 | 0.4286 | 0.6406 | ~1 | ~3 s |
| deep_agent 全链路（L2） | **0.7429** | **0.5143** | 0.7660 | 3 | 15.8 s |

**结论**：L0 是性价比最优解（0 成本换 +0.057 双段全中）；L2 效果最好但成本是 L0 的 8 倍——这解释了为什么 L2 默认关闭。

### 8.4 引用率与性能

```mermaid
%% 图7 引用率走势与rerank耗时
```

- **引用率走势**（`agent_trace_*`）：0.0（2026-09-13 22:32）→ 0.3929（09-13 22:33）→ 0.9688（09-16 20:26）→ 0.9062（09-16 20:30）→ 0.9375（09-16 21:09）
- **Rerank 耗时**：5 条 394 ms、10 条 394 ms、30 条 710 ms、60 条 1029 ms
- **系统层**（`system_metrics_20260911_065232`）：knowledge_qa 均值 7602 ms / P50 7375 ms / P95 10000 ms；LLM router 1121/1500/2400 ms；LLM kb_generation 1939.5/1838.2/3812.5 ms
- **Langfuse span 样例**：`POST /chat` 5351 ms、supervisor 1297 ms、`retrieval.kb_search` 2319 ms、llm.call 1674 ms

### 8.5 评测数据集

| 数据集 | 规模 | 用途 |
| :--- | :--- | :--- |
| `DATA/eval_rag/testset_1000_local.jsonl` | 1000 条（650 single / 350 multi） | 路由层与全量评估 |
| `dev_100.jsonl` | 100 条（65 single / 35 multi） | RAGAS 与检索调参 |
| `complex_20.jsonl` | 20 条（≥300 字复合题） | 复杂场景 |
| `holdout` | 200 条（130 single / 70 multi） | 门禁专用，调参不可见 |

---

## 9. 已知缺口与优化方向

| 缺口 | 现状 | 建议 |
| :--- | :--- | :--- |
| NDCG、answer_correctness | 未跑测 | 补充，可更全面评估排序质量 |
| context_precision | 仅 n=2 试跑（L0 0.4583 / L1 0.4062） | 大样本复测，判断 L1 是否引入噪声 |
| R1~R7 逐规则通过率 | 无聚合产物，仅单样本 `all_passed` | 增加规则级埋点，定位引用缺陷来源 |
| 分阶段耗时（embedding / dense / sparse / rrf） | 缺失，仅 rerank 有实测 | 补全 span 埋点，定位延迟瓶颈 |
| Tier B 表格抽取器 | 代码标注"未实现"，降级 Tier C | 实现后可降低复杂表格页的解析成本 |
| multi_hop 路由命中 | 0.6543（路由层，1020 样本） | 文本判据天花板约 73%，需引入结构信号 |
| single_hop 的 MRR 微降（−0.0025） | L1 对单跳无增益 | 按 query_type 分流：单跳走 L0，多跳走 L1 |
