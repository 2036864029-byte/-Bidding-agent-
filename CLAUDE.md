# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**招投标智能助手** (Bidding Intelligent Assistant) — an AI Agent for the government procurement & bidding domain. It combines structured MySQL queries, Milvus hybrid RAG over authoritative legal PDFs, and DeepSeek LLM reasoning. Four core capabilities: professional knowledge Q&A, price/bid inquiry, general chat, and document Q&A (placeholder).

## Commands

```bash
# Install dependencies (setuptools must be <70 for pymilvus 2.4.x)
pip install -r requirements.txt

# Single Q&A
python -m agent --question "招标方式有哪些？"

# Interactive session (clear = reset history, quit/exit = stop)
python -m agent --interactive

# Verbose/debug mode
python -m agent --question "..." --verbose

# Initialize the public knowledge base from PDFs (one-time, ~10-20 min)
python -m public_kb --init --pdf-dir raw_pdfs

# Standalone knowledge base Q&A test
python -m public_kb --interactive

# Start infrastructure (from project root)
docker compose -f milvus/docker-compose.yml up -d      # Milvus + etcd + MinIO + Attu
docker compose -f docker/mysql/docker-compose.yml up -d # local MySQL (optional)

# Run tests (pytest-compatible; some also run standalone via __main__)
python -m pytest test/ -v

# Diagnostic tools
python test/explain_sql.py --db ztb_clean --sql "<SQL>"   # EXPLAIN a query
python test/profile_node_price.py                         # profile price_inquiry node end-to-end
python test/db_explorer.py --overview                      # DB table overview
python test/create_fulltext_indexes.py --dry-run           # check FULLTEXT index coverage
```

## Architecture

The system has two independent packages connected by a thin contract:

### `agent/` — LangGraph StateGraph skeleton

- **`registry.py`** + **`nodes/base.py`**: **新增能力的单一登记点（P4 起）**。`CapabilityRegistry` 持有 `CapabilitySpec{id, node, route_desc, route_tool_doc, examples, model_slots, tools, enabled}`；`enabled=False` = 声明但预留（如 echo_demo）。`nodes/base.py` 提供 NodeSpec + `register/get_node_spec` 节点 entry 存储。新增能力 = registry 登记 enabled spec + base 登记 entry，router/graph 自动接入，**不再改 router.py / graph.py**。
- **`router.py`**: LLM-based intent classifier, **candidate set fully generated from the registry** — no hardcoded `RouterIntent`/route tools/prompt. Builds per-registry tools (`route_*`), a dynamic decision model (`Literal` over enabled ids), and the system prompt from each capability's routing copy. Tries `with_structured_output` first; falls back to Tool Calling if the API doesn't support it. Carries the last 3 conversation turns for context-aware routing. All failures → `fallback`.
- **`graph.py`**: Builds and compiles the StateGraph. **Nodes and conditional edges are registered dynamically from `registry.enabled_specs()`** (entries resolved via `nodes.base.get_node_spec`). All business nodes are wrapped in `_with_fallback()` — any unhandled exception returns a friendly degradation message instead of crashing. The `AgentGraph` class is the public entry point.
- **`state.py`**: Single `AgentState(TypedDict)` with three fields: `messages` (Annotated list with `add_messages` reducer for ID dedup), `router_intent` (str; value domain = registry enabled ids), `business_result` (generic dict). All branches share this same state — new branches do NOT add fields.
- **`nodes/`**: Business nodes, each following the signature `(AgentState) → dict`. `price_inquiry/` is now a **thin node + facade** (P3): `node.py` (entry + P0-11/P0-12 guards + guidance + dual timeout + POST_GUARD) lives here; the engine (intent/queries/recall/semantic/sql_builders/enum_norm/schema/models) moved to `price_engine/`. `agent/nodes/answer_templates.py` / `output_templates.py` are mirror shells for `price_engine/rendering/`. The package `__init__.py` re-exports engine symbols via `price_engine`, so `from agent.nodes.price_inquiry import ...` is unchanged for external callers.

### `price_engine/` — 价格/标讯查询引擎（P3 下沉）

- **引擎 8 文件**（intent / queries / sql_builders / recall / semantic / enum_norm / schema / models）+ **rendering/**（answer/output 模板）自 `agent/nodes` 下沉；`.db` 引用统一走 `core.rdb.mysql_pool`。
- **召回链（D-6）**：`recall.py` 的 5 级链（Milvus 语义→FULLTEXT→LIKE→逐词拆分→全表扫描）经 `recall_stages.py`（`_SqlRecallStage`/`_SplitKeywordStage`/`build_recall_stages`）接入 `core/retrieval.RecallPipeline` 熔断状态机。**FULL_SCAN 默认禁入**（`PRICE_ALLOW_FULL_SCAN=true` 才放行），全局 deadline=`PRICE_RECALL_DEADLINE_MS`（默认 3000ms）逐条 SQL 钳制。
- **配置开关**：`price_allow_full_scan` / `price_recall_deadline_ms`（`core/config/settings.py`）。
- `price_engine/__init__.py` 引擎门面（re-export 106 符号 + 连接池）。

### `core/retrieval/` — 共享召回熔断状态机（P3 D-1/D-6）

- `base.py`（`RecallStage` 协议 + `RecallOutcome`）+ `pipeline.py`（`RecallPipeline`：阶段短路 / deadline 熔断 / 超时熔断 / `allow_scan` 门控）。`stages.py` 为通用占位——price 特有阶段适配在 `price_engine/recall_stages.py`（避免 core→price_engine 反向依赖）。
- **`checkpointer.py`**: Factory that returns `MemorySaver` (default, ephemeral). Supports `sqlite`/`postgres`/`redis` backends via the same interface — one-line change, zero business code impact.

### `public_kb/` — RAG engine

- **`rag_engine.py`**: `PublicKnowledgeRAG` class — the public entry point. Methods: `init_knowledge_base(pdf_dir)`, `query(question)`, `clear_kb()`, `add_pdf(path)`. Uses lazy singleton pattern; the knowledge base is read-only after initialization.
- Pipeline: PDF → `MinerUParser` (MinerU API → Markdown) → `TextCleaner` → `SemanticChunker` (heading-aware, 2000 chars/chunk, 100 char overlap) → `create_embeddings()` (BGE-m3, 1024 dims) → `MilvusStoreManager` → `build_qa_chain()` (LCEL hybrid retrieval chain).
- **`config.py`**: `Settings` dataclass — single source of truth for all parameters. Loads from `.env` automatically. All modules share this config.
- Hybrid retrieval: dense (BGE-m3) + sparse → RRF fusion (k=60) → BGE-reranker-v2-m3 Cross-Encoder rerank → top-K (5). Adaptive similarity threshold with 0.45 floor.

### Data sources

- **MySQL `ztb_clean`**: Cleaned structured data (company info, penalties, bid projects, products). Module-level connection pool with reuse (no fixed cap). Uses FULLTEXT indexes with ngram parser (ngram_token_size=2) for Chinese text search.
- **Milvus**: Two collections — `public_kb` (law/regulation chunks) and `mysql_price_semantic` (structured data semantic recall). IVF_FLAT index, COSINE metric, nlist=128, nprobe=32.

### Node interface contract

Every business node follows this signature (defined in `agent/nodes/`):

```python
def node_xxx(state: AgentState) -> dict:
    """Returns {"business_result": {"branch": str, "answer": str, "data": ...},
                "messages": [AIMessage(content=str)]}"""
```

When adding a new branch (P4 起，router/graph 已由 `agent/registry.py` CapabilityRegistry 数据驱动，零改动): (1) create `agent/nodes/new_branch.py` implementing the `(AgentState) -> dict` interface, (2) register its entry via `agent.nodes.base.register()` if not one of the built-in five, (3) register an `enabled` `CapabilitySpec` in `agent/registry.py` with its routing copy (`route_desc` / `route_tool_doc` / `examples`). The router candidate set, `route_*` tools, system prompt, graph nodes, and conditional edges are all generated from the registry — do NOT edit `router.py` / `graph.py`. State definition and other nodes remain untouched.

## Key constraints

- `setuptools` **must be <70**. pymilvus 2.4.x depends on the removed `pkg_resources` module.
- `public_kb` is **read-only** after initialization; only batch import or admin `clear_kb()`.
- All SQL queries prioritize indexable exact matches (`=`, `>=`, `<=`) and FULLTEXT over `LIKE '%...%'`.
- Router LLM uses `temperature=0` for deterministic classification.
- The project `.env` contains real API keys — never commit it.

## Tests

The `test/` directory contains both diagnostic scripts and pytest-compatible tests:

**Diagnostic/profiling scripts:**
- `test/db_explorer.py` — database overview (table counts, sizes)
- `test/explain_sql.py` — run EXPLAIN on a query to detect full table scans
- `test/profile_node_price.py` — benchmark price_inquiry node end-to-end
- `test/create_fulltext_indexes.py` — auto-create or dry-run FULLTEXT indexes
- `test/_diag_common.py` — shared MySQL connection helper for the active diag scripts

**Archived one-off scripts**（历史数据准备/迁移工具，冻结保留）:
- `archive/` — migrate_milvus_cloud.py, rebuild_and_verify.py
- `scripts/archive/` — run_evaluation.py, generate_report.py, csv_to_mysql.py
- `test/legacy/` — _step* 迁移步骤、scan_tables / inspect_price_dbs / export_samples 等数据诊断工具

**Pytest-compatible tests** (test internal functions with mocks, no live DB/LLM needed):
- `test/test_recall_optimization.py` — recall stage logic tests
- `test/test_sub_route.py` — sub-route classification tests
- `test/test_bug_repairs.py` — regression tests for fixed bugs
- `test/test_p0_*.py` — P0 bug fix verification tests
- `test/test_citation_tracing.py` — citation tracing tests (chunk_uid, standardized citations, validation rules R1-R7)

**Knowledge-base citation evaluation** (requires live Milvus + LLM):
- `python scripts/run_knowledge_citation_eval.py` — full citation traceability eval over `testset_knowledge.jsonl` (~106 legal questions), verifies per-result citation completeness and Milvus back-check association; writes `test_report/knowledge_citation_results.jsonl` + summary report

Run all with: `python -m pytest test/ -v`

## Reference docs

`docs/project_overview.md` is the comprehensive reference (v2.0, ~1100 lines). It covers architecture, setup, deployment, troubleshooting, technical debt register, and architecture decision records. Consult it for detailed explanations before modifying core flows.
