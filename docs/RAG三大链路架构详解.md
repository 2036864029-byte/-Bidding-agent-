# RAG 三大链路架构详解

> 范围：`public_kb/` 入库与检索全链路。本文只讲三条链——**① 文本清洗与处理（PDF 为重点）**、**② 文本分块**、**③ 检索与重排序**。
> 事实基准：以当前代码为准（配置已上提至 `core/config/settings.py`，`public_kb/config.py` 已不存在）。
> 生成侧（提示词 / 引用护栏 R1~R7）见《RAG链路技术详解.md》，本文不重复。

---

## 0. 三段链路总览

![三段链路总览](rag3_overview.svg)

三段链路是**两离一线**结构：①② 属于离线入库，只在知识库初始化 / 增量导入时跑；③ 属于在线检索，每次提问都跑。①② 的输出（chunk 与向量）是 ③ 的唯一输入，三者通过 **Milvus 集合 `public_kb`** 解耦。

| 段 | 输入 | 输出 | 触发时机 | 主要代码位置 |
|---|---|---|---|---|
| ① 清洗与处理 | 原始 PDF / CSV / TXT | `out_dir/clean/*.assembled.md` | 入库时一次 | `public_kb/ingestion/normalizers/` |
| ② 文本分块 | 清洗后 markdown | `List[Document]`（含 chunk_uid） | 入库时一次 | `public_kb/ingestion/transforms/` |
| ③ 检索与重排 | 用户 query | `≤11` 个带分文档 | 每次提问 | `public_kb/retrieval/` |

### 三处与旧文档不一致，汇报请以本文为准

| 项 | 旧文档表述 | 代码实际 |
|---|---|---|
| PDF 分档 | "Tier A / B / C 三档" | **Tier B 未实现**，判定为 B 的页统一 `replace(tier="C")`，源码注释 `tier_b_unimplemented_fallback_to_c` |
| 分档总开关 | 未提 | `pdf_tiered_routing_enabled` 默认 **false**，即默认走旧的全量 `MinerUParser` 链路；分档是**显式开启**的能力 |
| 分块阈值 | "700 / 700 / 900 / 1000（含 target 与 min）" | 配置里**只有 `max`**，无 target、无 min；且 TypedChunker **不使用 overlap** |

---

## 1. 链路一：文本清洗与处理（PDF 为重点）

![PDF解析与清洗架构](rag3_clean.svg)

### 1.1 入口：格式路由

`public_kb/ingestion/router.py::FormatRouter`

1. **先按扩展名**：`_EXT_TO_FORMAT` 覆盖 pdf / csv / txt / md / markdown / png / jpg / jpeg / bmp / tif / tiff / webp。
2. **未命中才嗅探**：`_sniff_format()` 读前 **2048** 字节——`%PDF` → pdf；图片 magic → image；首行含逗号且无 `\x00` → csv；否则 text。**不使用 MIME。**
3. `normalizers/__init__.py::build_normalizer(fmt)` 分派到 Text / Csv / Pdf / ImageOcr 四个归一化器。

> 注意：`public_kb/schema_registry.py` **不是** doc_type 注册表，它是 **Milvus 集合 / embedding 版本注册表**（`EXPECTED_EMBEDDING_DIM=1024`、`schema_version="public_kb_v2"`、HNSW `M=16 / efConstruction=128`）。doc_type 识别在分块链（见 §2.2）。

### 1.2 页面画像（Tier 判定的输入）

`normalizers/pdf/pdf_page_profile.py::build_page_profile()`，线程池 `pdf_tiered_profile_max_workers=16`（与 CPU 核数取小）。

| 画像指标 | 含义 | 下游用途 |
|---|---|---|
| `text_chars` / `n_blocks` | 文本层字符数 / 块数 | 扫描件判定 |
| `x_starts` / `two_col_gap` / `two_col_split_x` / `has_two_col` | 版心 x 分布 | 双栏检测 |
| `table_hlines` / `table_vlines` / `table_candidate` | 表格线线索（阈值 `table_min_lines=5`） | 表格页判定 |
| `img_ratio` | 图片面积占比（先剔除覆盖 ≥`0.70` 的背景图） | 扫描件判定 |
| `article_count` / `formula_hint` / `fonts` | 法条计数 / 公式字体特征 / 字号分布 | 公式页判定、标题层级 |

**双栏检测算法**（非投影法）：取 `x0` 方向的**最大间隙**，要求间隙 > `gap_ratio=0.15 × 页宽`，且间隙两侧各有 **≥3** 个文本块（`two_col_min_side_blocks=3`）。

### 1.3 前置区跳过（Tier S）

`pdf_router._mark_front_matter_skip()`，开关 `pdf_skip_front_matter=true`。

命中"正文起点"的页之后全部保留，**起点之前的页（封面 / CIP / 序言 / 目录）标 `tier="S"`、`parser="skip"`**，不进入任何解析。正文起点判据（四条同时满足）：

- 命中 `_FRONT_HEADING_RE`（`^第[…]+[编部分章节]` 或 `^第[…]+[条款项]`）；
- 行长 ≤ 40 字；
- 句末标点计数 ≥ 3（短标题，非正文）；
- 页尾裸页码行 < 8 字。

识别失败（boundary 为 None 或 0）则原样返回，由清洗层 `structure.mark_front_matter()` 兜底。

### 1.4 分档分类：判定树

`normalizers/pdf/pdf_legal_page_classifier.py::LegalPageClassifier.classify()`，按**优先级短路**判定：

| 序 | 判据 | 档位 | parser | 置信度 |
|---|---|---|---|---|
| 1 | `text_chars < 80`（`pdf_tiered_min_text_chars`） | **C** | mineru | 0.95 |
| 2 | `img_ratio > 0.35` **且** `text_chars < 200` | **C** | mineru | 0.90 |
| 3 | `formula_hint` | **C** | mineru | 0.80 |
| 4 | 表格候选 **且** `hlines≥2 且 vlines≥2` | **B**（→ 实际降级 C） | table | 0.85 |
| 5 | 表格候选但非规整 | **C** | mineru | 0.85 |
| 6 | 双栏 **且** 列序置信度 ≥ 0.80 | **A** | fast_text | 计算值 |
| 7 | 双栏但置信度不足 | **C** | mineru | 计算值 |
| 8 | 其余 | **A** | fast_text | 0.90 |

**双栏列序置信度公式**（`_two_col_confidence_score`）：

```
confidence = max(0.55 + 0.4 × balance, 0.55 + 0.4 × min(1, side_min / 5))
```

`balance` = 两侧文本块数的平衡度，`side_min` = 弱侧文本块数。取两者较大值：块平衡照顾干净的 2+2 双栏，`side_min` 照顾 OCR 双栏页"一侧被合并成少而大的块"的情形。

> **Tier B 是空转的**：`pdf_router` 在分类后统一执行 `replace(tier="C", parser="mineru", reason="tier_b_unimplemented_fallback_to_c: …")`。即当前**没有本地表格提取路径**，`table_extract.py` 的 `ENGINES` 未接入管线，有框表格一律由 MinerU 处理。

### 1.5 Tier A：本地快路径

`_dispatch_tier_a()`，`ThreadPoolExecutor(max_workers=4)`，每个 worker 独立 `pymupdf.open`（线程间不共享 Document）。

单页处理顺序：

1. `iter_lines` 抽取文本行；
2. `remove_page_numbers` 删页码；
3. `remove_header_lines` 删页眉（候选来自预扫 `detect_header_candidates`）；
4. **双栏防御**：若 `page_label=="two_col_text"` 或满足"行宽中位数 < 0.55×页宽、`x0>0.4×页宽` 行占比 0.2~0.8、且 ≥6 行"，走 `reflow_two_columns_v2(split_x, full_width_ratio=0.7)`；否则 `sort_single_column`；
5. `paragraph_markdown` 段落化 + 标题化。

**段落合并三判据**（`pdf_paragraph.assemble_paragraphs`，行距/版面 metrics 法，非投影法）：

- 缩进：`x0 > left_edge + 1.5 × body_size`；
- 上行未写满：上一行以句末收尾且 `x1 < right - 2 × body_size`；
- 行距：`Δy > 1.5 × line_gap`。

合并后 `_join_cjk` 去除 CJK 字符间多余空格。

**标题层级判定**（`heading_level_layout`）：

| 判据 | 阈值 |
|---|---|
| 长度 | > 60 字不判标题 |
| 字号证 | 仅当 `size_spread ≤ 6% × body` 时启用，`big = size ≥ 1.10 × body` |
| 粗体短行 | ≤ 40 字 |
| 编号粗体 | ≤ 60 字 |
| 居中 | 居中且 ≥ 1.2× → H1 / H2 |
| 兜底 | `_pattern_level` + `_regulation_name_level` |

### 1.6 Tier C：MinerU 远程解析

`pdf_mineru_router.MinerURouter.parse_ranges()`：

1. `aggregate_complex_ranges`：连续 Tier C 页聚合为区间，**前后各扩 1 页**（`pdf_tiered_expand_boundary_pages=1`）并合并重叠；
2. `write_subpdf`：`doc.select()` 抽取子 PDF，`save(garbage=4, deflate=True, clean=True)`；
3. `compute_cache_key = md5(md5(源PDF字节) | pages | parser_version | range_id)` → 命中缓存直接复用；
4. `MinerUApiParser.parse()`：`POST {base}/parse` multipart（`file` + `page_range`），`Authorization: Bearer`，**`mineru_api_timeout=1800s`**，health 探针 5s，版本 memo TTL 300s。

返回**纯 markdown，无 layout json**；空 markdown 抛 `RuntimeError`。**未见轮询与重试循环**——失败即 fail-fast，仅当 `pdf_tiered_allow_partial=true` 才跳过该区间并写 warning。

> 本地 `MinerUParser`（`services/mineru_parser.py`）走 `subprocess.run(["magic-pdf", …])`，`timeout=3600s`，是**关闭分档时的默认路径**（`pdf_tiered_routing_enabled=false`）。

### 1.7 装配与清洗

**装配**（`pdf_markdown_assembler.assemble_pages`）：按 `page_idx` 升序拼接，插入 `<!-- page: N -->` 标记；`_join_page_boundary` 做跨页续段（末行 <8 字或非句末时不合并，跳过页标记与 `①` 脚注行）；再 `dedup_repeated_lines(min_count=5, max_len=80)`；最后 `clean_assembled_document`。

**清洗**（`transforms/cleaner.py::TextCleaner.clean`）：

| 序 | 规则 | 判据 |
|---|---|---|
| 1 | 删重复页眉页脚 | 频次 ≥5、长度 <80 且 ≤40、不含 `。！？…；`、不匹配法条正则 |
| 2 | 删页标记与图片链接 | `^<!-- page: N -->$`、`^!\[…\]\(…\)$` |
| 3 | 删独立页码 | `^\d{1,4}$` |
| 4 | 删无效短行 | `<10` 字**且无汉字**（`#`/`---`/`|`/法条豁免） |
| 5 | LaTeX 归一 | `\times→×`、`\textcircled{X}→(X)`、NBSP→空格、剥 `boldsymbol/mathrm/textbf…{}`、`$…$` 剥壳、`(\d)\?\?→\1%` |
| 6 | 空行压缩 | 最多连续 2 行 |

**结构治理**：

- `relayout.py::relayout_pipeline` = R1 目录残标题剔除（正则 `^\d{1,4}\s*[.、．]\s*\S.{0,28}?\s+\d{1,4}$`，≤40 字、无句读）+ R2 引用误标题降级（>20 字、无【】、`^第…条` 开头、命中引用提示词 → 去掉 `#`）。
- `structure.py` 行级重写：`normalize_heading_levels_full`（编1/部分1/章2/节3/条4/款5/项6、`一、`4、`（一）5`）、`split_inline_articles`、`normalize_external_md`、`repair_structure(max_rounds=3)`。

### 1.8 产物、旁路与控制

| 机制 | 实现 | 说明 |
|---|---|---|
| 中间产物 | `out_dir/raw/*.raw.md`、`out_dir/clean/*.assembled.md`（兼文件级缓存）、`out_dir/chunks/*.documents.jsonl` | handoff 落盘 |
| 路由 manifest | `DATA/raw_data/_pdf_tiered_manifest/{stem}.manifest.json`（先写 `.tmp` 再 `os.replace` 原子替换） | 含 elapsed_sec / ranges / skipped pages |
| MinerU 缓存 | `DATA/raw_data/_mineru_api_cache/{md5}.md` + `.json` sidecar | 子 PDF 在 `_mineru_subs/` |
| 去重 | `ledger.py::IngestLedger` → `ingest_ledger.jsonl`，**主判据是 source_hash（字节 MD5，跨文件名）** | simhash 仅登记不参与判定 |
| 断点续跑 | `checkpoint.py::StageCheckpoint` → `run_manifest.json`，`--resume` / `--rerun-from=B` | Stage A markdown 落 `artifacts/{md5(绝对路径)}.md` |
| 质量门禁 | `quality.py::QualityGate`，阈值 0.8 | **注意**：`PdfNormalizer` 硬写 `quality_score=1.0`，PDF 主链路上实际不触发旁路 |

---

## 2. 链路二：文本分块

![文本分块架构](rag3_chunk.svg)

### 2.1 入口

- 主入口 `chunk_sources.py::chunk_directory`（L86 建 `TypedChunker(settings)`）；
- 兼容入口 `handoff.py::chunk_cleaned_markdown`（L213）：`doc_type is not None` → `TypedChunker`，否则回落旧 `SemanticChunker`；
- 管线入口 `pipeline.py::_chunk_ir`（L210）：`.md` 才 `detect_doc_type_or_none(path)`。

**输入是清洗后的 markdown 字符串**（不是 IR 对象），**输出是 `List[Document]`**。

### 2.2 doc_type 识别

`transforms/typed_chunker.py::DocType` 共 **4 类**，识别走 `TYPE_ROUTING` **路径 / 文件名关键词**顺序匹配（无内容规则，`force_doc_type` 可手工指定）：

| 序 | 匹配规则 | doc_type | `chunk_max_chars` |
|---|---|---|---|
| 1 | 路径含 `cleaned_v1/markdown` | `law` | **700** |
| 2 | 文件名含 `1200问` / `问答` / `答疑` / `问题` | `qa` | **900** |
| 3 | 文件名含 `实务` / `手册` / `指南` / `教程` / `解读` | `book_practical` | **1000** |
| 4 | 文件名含 `法规全书` / `汇编` | `law_compilation` | **700** |
| 5 | 路径含 `handoff` | `book_practical` | **1000** |
| — | 未命中 | `book_practical`（兜底） | **1000** |

> 配置里**只有 `max_chars`，没有 target、没有 min**。全仓也没有 chunk 级最小长度参数——唯一的过滤是 `_has_body()`（纯标题无正文不成块）。

### 2.3 两层切分

**前置**：`split_inline_articles`（拆行内多条款）→ `strip_toc_noise`（`enable_toc_filter`）→ `split_table_blocks` 分流。

**第 0 层：表格原子块。** `pdf_structure.py::split_table_blocks`，行正则 `^\s*\|.*\|\s*$`，**连续 ≥2 行**（`pdf_min_table_rows=2`）才成表格段 → 直接 `_make_atom(content_type="table")`，**不进第一层、不进第二层**。不足 2 行按普通文本处理。

**第一层：`HeadingSplitter.split`。** 正则 `^(#{1,6})\s+(.+)$`，**每个标题都是切点**。层级取 `structure.py::_content_level`（编/部分=1、章=2、节=3、条=4、款=5、项=6、`一、`=4、`（一）`=5、数字编号=4；非模式标题取 `min(最近锚级+1, 6)`）。**标题栈感知**：`_update_heading_stack` 维护栈，`_hierarchy_path` 以 `" / "` 拼接写入 `chapter` / `hierarchy_path`。

**第二层：`OversizeSplitter.split`。** 仅当 `len(block_text) > max_chars` 触发，按 doc_type 分支：

| doc_type | 策略 |
|---|---|
| `law` / `law_compilation` | `_split_by_subheadings(_ITEM_RE)` → 段数>1 则返回，否则 `_split_by_sentence` |
| `qa` | 只切答案正文：前缀 `问：{question}\n\n`，`budget = max(1, max_chars - len(prefix))`，再 `_paragraph_then_sentence` |
| `book_practical` | `_split_by_subheadings(_BOOK_SUB_RE)` → 否则 `_paragraph_then_sentence` |

句子边界 ` _SENTENCE_SPLIT_RE = (?<=[。！？；])`（零宽后顾，保留标点）。**超长句硬切**：`sentence[i:i+max_chars]` 滑窗。

**关于 overlap**：`TypedChunker` **不使用 overlap**，代码中无 overlap 字样。替代机制是**父子块**（`parent_chunk_id` + 检索期 `retrieval_parent_assembly`，见 §3.4）。旧 `SemanticChunker` 的 2000/100 只是**未识别 doc_type 时的回落路径**（`handoff.py` L229-231 显式传入）。

### 2.4 元数据与 chunk_uid

每个 chunk 的 metadata 字段：

`doc_id, doc_name, doc_type, source_format, effective_date, status, embedding_model, schema_version, chunk_index, chapter, hierarchy_path, chunk_type, parent_chunk_id, sequence_in_parent, article_number, question, content_type, chunk_uid`

- **`chunk_uid`**（`core/contracts/chunk_ids.py::compute_chunk_uid`）：
  `ck-` + `md5(f"{doc_name}|{chapter}|{chunk_index}|{md5(text)[:16]}")`，其中 text 先 `normalize_chunk_text`（strip + 换行归一）。**不含 doc_id、不含页码。**
- `parent_chunk_id` 为空时回填 `chunk_uid`（L488-490，避免 Milvus 分组检索把独立块塌缩成一组）；父标识为合成值 `f"{doc_id}|sec_{start_index}"`。
- `doc_id` = `{law|lawcomp|qa|book}_{seq:03d}`。
- **无页码、无 token/char 长度字段。**

**写入前校验**：`core/contracts/contracts.py::validate_ingestion_documents`——`page_content` 非空、必含 `doc_name/chapter/chunk_index`、`chunk_index` 为非负 int，**违规抛 `IngestionContractError`（非静默丢弃）**。写入去重靠 `MilvusSink` 的 **chunk_uid 幂等判重**（批内去重 + `_query_existing_uids` 跳过已存在 uid），开关 `enable_dedup`（默认 true）。

### 2.5 CSV 路径的差异

`csv_loader.py::CsvLoader` **不识别 doc_type、不走 TypedChunker**：逐行 content → 列名归一（`_COLUMN_ALIAS_MAP`）→ 结构增强（`structure_plain_text`：章/节/条/款/项前插换行并转 markdown 标题，含否定后顾防空引用误切）→ `TextCleaner.clean` → **`SemanticChunker(2000, 100)`**。额外字段 `title/source_file/source_url/publish_date/imple_time/effective_date/_line_num/content_type`，且**只有 CSV 路径有 `page_number`**。

---

## 3. 链路三：检索与重排序

![检索与重排序架构](rag3_retrieval.svg)

### 3.1 调用链

```
PublicKnowledgeRAG.query()                       engine.py:102
  └─ _qa_chain.invoke()  ← build_chain()        generation/chain.py:26
       ├─ RunnableLambda(_retrieve)
       │    └─ HybridRetriever.retrieve()        retrieval/retriever.py:68   （先查 kb 结果缓存）
       │         └─ _retrieve_impl()                                    :93
       │              ├─ _hybrid_retrieve()                            :205
       │              └─ _dense_fallback()                             :327
       │         └─ _attach_parent_context()                           :161  （回程统一走）
       └─ RunnableLambda(_decide_and_answer) → format_docs / build_sources / build_citations
```

### 3.2 各阶段

| 阶段 | 实现 | 关键参数 |
|---|---|---|
| 查询预处理 | `validate_question()`：类型校验 + `strip()`，**无改写 / 扩展 / 去噪** | 空串抛错 |
| 时效过滤 | `_effective_expr()` → `effective_date is null or <= today` | `enable_effective_filter=**false**`（默认关） |
| Dense | `AnnSearchRequest(vector)` | **COSINE · HNSW · ef=64 · limit=30** |
| Sparse | `AnnSearchRequest(question 原文)` | **服务端 BM25 · limit=30**（客户端零计算） |
| 融合 | pymilvus `RRFRanker(k=60)` | `limit=30`，`group_by_field=parent_chunk_id`、`group_size=2` → 候选最多 60 |
| 重排 | `SiliconFlowReranker.rerank()` | `BAAI/bge-reranker-v2-m3` · `top_n=22` · timeout 30s · retries 2 |
| 阈值 | `adaptive_threshold()` | 见 §3.3 |
| 截断 | `retrieval_top_k` | **11** |
| 父块装配 | `assemble_parent_contexts()` | `max_chars=6000`，超限降级 ±1 邻居 |

**Embedding**：`core/embeddings/bge_m3.py::create_embeddings` → `OpenAIEmbeddings` 子类（超长截断到 `chunk_max_chars=2000`），model `BAAI/bge-m3`、dim **1024**、timeout 30、retries 1、入库 batch **100**。

**Dense / Sparse 两路的关键差异**：dense 传向量、走向量索引；sparse **传原文字符串**——Milvus 通过 Schema 上声明的 `Function(name="text_bm25_emb", FunctionType.BM25)` 在服务端自动生成稀疏向量（analyzer `chinese`、k1=1.2、b=0.75、`DAAT_MAXSCORE`）。这是本项目性价比最高的设计之一：省掉 IDF 词典维护与一套稀疏模型，同时补上纯语义检索在**条号、专名、金额**上的短板。

### 3.3 自适应阈值

`retrieval/strategies.py::adaptive_threshold(top_score, settings)`，输入是 **rerank 后的 top1 分**：

| top1 分数 | 阈值 | 语义 |
|---|---|---|
| ≥ 0.75 | **0.40** | 高置信，放宽以补充上下文 |
| ≥ 0.50 | **0.45** | 中等置信 |
| < 0.50 | **0.50** | 低置信，收紧（由 `_decide_and_answer` 判断是否拒答） |

校验：`high ≤ medium ≤ low` 且均 ∈ [0,1]，否则 `ValueError`。**这是分段常量，不是统计值**——不假设得分分布，行为可预测、易调参。

### 3.4 父块装配

`parent_assembly.py::assemble_parent_contexts()`，仅处理 `chunk_type=="child"`：

1. `_fetch_children()` 批量回表：`query(filter="parent_chunk_id in [...]", limit=16384)`；
2. 按 `sequence_in_parent` 升序拼接，结果写入 `metadata["context_text"]`（**不改 `page_content`**）；
3. 长度熔断（`:110`）：全文 ≤ **6000** 字直接用；超限降级为命中块 **±1 邻居**；仍超限则只保留命中子块。

去重：`is_duplicate_parent()` 按 `parent_chunk_id` 保留首次出现（=高分者），在 rerank 后与 fallback 两处生效。

### 3.5 降级矩阵

| 触发条件 | 行为 | `fallback_reason` |
|---|---|---|
| collection / embeddings 缺失 | `_dense_fallback()` | `native_collection_or_embeddings_missing` |
| Schema 无 `sparse_vector` 字段 | 降级 dense；`strict_hybrid_validation=true` 时抛 `HybridRetrievalError` | `sparse_vector_field_missing` |
| 混合检索任意异常 | 降级 dense | `hybrid_error:{异常类名}` |
| Reranker 抛错 / 超时 | **保留 RRF 原序，不构造虚假分数** | `reranker_failed` |
| Reranker 成功但返回空 | 保留 RRF 原序 | `reranker_empty` |
| 候选为空 | 拒答 | `RetrievalMode.REFUSAL` |

`dense_only_retrieve()` 自身还有两级：① pymilvus 原生 `search_with_full_fields`（limit=30）→ 按 `similarity_threshold=0.45` 过滤 → `dedupe_by_parent` → `[:11]`；② 抛异常则 `similarity_search_with_score(k=30)` 同逻辑；③ 再失败返回 `[]`。

> **无 LIKE 兜底、无全量扫描**（`price_allow_full_scan` 属价格链，不在本链路）。

### 3.6 多跳召回 L0 / L1 / L2

不是检索包内的循环，而是**外层对检索的 N 次调用**：

| 层 | 实现 | 开关 / 参数 | 成本 |
|---|---|---|---|
| **L0** | `retrieval_top_k` 由 5 提到 11 | 已落地 | 零 LLM |
| **L1** | `public_kb/tools.py::kb_search_expanded`（:297）：基线 top-11 + 按 `[？?。；;，,\n]` 切子句（≥8 字）→ 每子句检索 `sub_top_k=5` → 按 `_norm_key` 去重并集，基线原序保持、新块按 score 降序追加，`max_chunks=20` | **`kb_light_decompose=true`**，在 `agent/nodes/knowledge_qa/subgraph.py:162` 按开关路由 | 1 次基线 + N 子句 |
| **L2** | deep_agent EXECUTE 并行化（ThreadPool ≤4） | `deep_agent_max_exec_rounds=2`、`deep_agent_replan_enabled=true` | 3 次 LLM / ~15.8s 每题 |

---

## 4. 参数速查

| 类别 | 参数 | 值 |
|---|---|---|
| PDF 路由 | `pdf_tiered_routing_enabled` | **false**（默认） |
| | `pdf_tiered_min_text_chars` / `img_min_text_chars` | 80 / 200 |
| | `pdf_tiered_image_area_ratio` / `background_img_ratio` | 0.35 / 0.70 |
| | `pdf_tiered_two_col_confidence` | 0.80 |
| | `pdf_tiered_expand_boundary_pages` | 1 |
| | `pdf_tiered_fast_max_workers` / `profile_max_workers` | 4 / 16 |
| | `pdf_tiered_book_timeout_sec` / `mineru_api_timeout` | 7200 / 1800 |
| | `pdf_skip_front_matter` | true |
| 清洗 | `pdf_min_table_rows` | 2 |
| | `enable_pdf_toc_filter` / `enable_external_md_normalize` | true / true |
| 分块 | `chunk_max_chars_law` / `_law_compilation` / `_qa` / `_book_practical` | 700 / 700 / 900 / 1000 |
| | `chunk_max_chars`（回落路径） | 2000 |
| 向量 | `embedding_model` / `embedding_dim` / `batch_size` | BAAI/bge-m3 / 1024 / 100 |
| 索引 | `milvus_dense_index_type` / `hnsw_m` / `ef_construction` / `search_ef` | HNSW / 16 / 128 / 64 |
| | `milvus_sparse_index_type` / `bm25_k1` / `bm25_b` | SPARSE_INVERTED_INDEX / 1.2 / 0.75 |
| 检索 | `retrieval_top_k` | **11** |
| | `hybrid_dense_limit` / `hybrid_sparse_limit` / `hybrid_fusion_limit` | 30 / 30 / 30 |
| | `rrf_k` | 60 |
| | `retrieval_group_by_parent` / `group_size` | true / 2 |
| | `retrieval_parent_max_chars` | 6000 |
| | `similarity_threshold` | 0.45 |
| 重排 | `reranker_model` / `timeout` / `max_retries` / `backoff` | BAAI/bge-reranker-v2-m3 / 30s / 2 / 0.25s |
| | `rerank_high/medium/low_confidence_threshold` | 0.40 / 0.45 / 0.50 |
| | `rerank_high/medium_confidence_score` | 0.75 / 0.50 |
| 其他 | `kb_light_decompose` / `enable_effective_filter` / `strict_hybrid_validation` | true / false / false |
| | `cache_enabled` / `cache_maxsize` / `cache_kb_ttl_seconds` | true / 512 / 3600 |

---

## 5. 现状与风险提示

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | **分档路由默认关闭**（`pdf_tiered_routing_enabled=false`） | 默认仍是全量 MinerU 链路，分档的工程收益未默认生效 | 汇报时明确"分档是显式开启能力"；若已验证稳定，建议翻转默认值 |
| 2 | **Tier B 空转** | 有框表格全部走远程 MinerU，成本与耗时高 | 接入 `table_extract.ENGINES` 或明确放弃 B 档并简化判定树 |
| 3 | **超大表无切分** | 表格原子块不细分，整表入库（VARCHAR 65535） | 统计实际长度分布，必要时为大表加切分或摘要 |
| 4 | **分块无 overlap 且无最小长度** | 碎块可能进入检索；父子块是主要补偿手段 | 已有 `parent_assembly` 兜底，可补 chunk 级 min_chars |
| 5 | **doc_type 靠文件名** | 文件名不规范即回落 `book_practical`（1000 字），法规类可能被过粗切分 | 增加内容规则或入库时显式声明 |
| 6 | Milvus 服务端 `enable_bm25` 生效状态 | 代码默认 true，需实机 `describe_collection` 核对 | 部署检查项 |
| 7 | `quality_score` 硬写 1.0 | PDF 质量门禁形同虚设 | 接入真实质量分或移除该字段 |

---

*本文事实核对于 2026-09-17，基准为当前工作区代码。*
