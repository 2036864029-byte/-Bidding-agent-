# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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

- **`graph.py`**: Builds and compiles the StateGraph. All business nodes are wrapped in `_with_fallback()` — any unhandled exception returns a friendly degradation message instead of crashing. The `AgentGraph` class is the public entry point.
- **`state.py`**: Single `AgentState(TypedDict)` with three fields: `messages` (Annotated list with `add_messages` reducer for ID dedup), `router_intent` (str enum), `business_result` (generic dict). All branches share this same state — new branches do NOT add fields.
- **`router.py`**: LLM-based intent classifier. Tries `with_structured_output(RouterDecision)` first; falls back to Tool Calling if the API doesn't support it. Carries the last 3 conversation turns for context-aware routing. All failures → `fallback`.
- **`nodes/`**: Business nodes, each following the signature `(AgentState) → dict`. `price_inquiry/` is a package (split 2026-08-15): `node.py` (entry + guards + guidance), `queries.py` (table-specific query paths), `recall.py` (multi-stage retrieval chain: Milvus semantic → FULLTEXT → LIKE → full scan with descending weights), `sql_builders.py`, `intent.py` (unified intent parsing), `semantic.py` (Milvus semantic collection bootstrap/recall), `enum_norm.py`, `db.py` (connection pool), `schema.py`, `models.py`. The package `__init__.py` re-exports all symbols, so `from agent.nodes.price_inquiry import ...` is unchanged for external callers.
- **`checkpointer.py`**: Factory that returns `MemorySaver` (default, ephemeral). Supports `sqlite`/`postgres`/`redis` backends via the same interface — one-line change, zero business code impact.

### `public_kb/` — RAG engine

- **`rag_engine.py`**: `PublicKnowledgeRAG` class — the public entry point. Methods: `init_knowledge_base(pdf_dir)`, `query(question)`, `clear_kb()`, `add_pdf(path)`. Uses lazy singleton pattern; the knowledge base is read-only after initialization.
- **`qa_chain.py`**: Stable adapter for `build_qa_chain()` and legacy online-chain symbols. Implementation remains in `generation/` and `retrieval/`.
- Pipeline: PDF → `MinerUParser` (MinerU API → Markdown) → `TextCleaner` → `SemanticChunker` (heading-aware, 2000 chars/chunk, 100 char overlap) → `create_embeddings()` (BGE-m3, 1024 dims) → `MilvusStoreManager` → `build_chain()` (LCEL hybrid retrieval chain).
- **`services/`**: Embedding, LLM, Milvus storage, and MinerU parsing implementations.
- **`ingestion/`**: Source, transform, sink, pipeline, and CSV CLI implementations.
- **`retrieval/`**: Milvus dense/sparse search, RRF fusion, adaptive thresholds, fallback, and reranking.
- **`generation/`**: Prompt construction, context formatting, LCEL chain assembly, and citation utilities.
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

When adding a new branch (P4 起，router/graph 由 `agent/registry.py` CapabilityRegistry 数据驱动): (1) create `agent/nodes/new_branch.py` implementing the `(AgentState) -> dict` interface, (2) register its entry via `agent.nodes.base.register()` if not one of the built-in five, (3) register an `enabled` `CapabilitySpec` in `agent/registry.py` with routing copy (`route_desc` / `route_tool_doc` / `examples`). Router/graph auto-adopt; do NOT edit `router.py` / `graph.py`. State definition and other nodes remain untouched.

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

## 本地绿 ≠ CI 绿（改依赖 / 改 CI / 写测试前必读）

> 背景：2026-11-27 首次接入 GitHub Actions，连跑 6 次才全绿；四次失败**没有一次是业务逻辑 bug**，
> 全部是「本地环境替 CI 掩盖了隐式依赖」。完整复盘与判定树见
> **[`docs/踩坑_本地绿CI红.md`](docs/踩坑_本地绿CI红.md)**。

- **R1 解释器版本必须显式钉。** CI 用 `UV_PYTHON: "3.12"`；本地 venv / Dockerfile（`python:3.11-slim`）/ CI
  三处要对齐。注意 `requires-python` 目前仍是 `>=3.11`（**无上界**）—— 任何人直接 `uv sync` 都可能装到
  3.14，而 `tiktoken==0.8.0` 没有 cp314 wheel → 回退源码编译 → PyO3 0.22 上限 3.13 → 失败。
- **R2 导入路径靠配置，不靠调用方式。** `pyproject.toml` 的 `pythonpath = ["."]` 是必需的：
  `pytest`（console script）不会把 CWD 放进 `sys.path`，而 `python -m pytest` 会；差别会导致
  `import core` 命中 `test/core/`（namespace 包）。
- **R3 测试不得依赖本机环境。** 不读 `.env`、不连 `127.0.0.1`、模型客户端一律用 conftest 的占位凭据 + mock。
  这条仓库里早就有出处：`test/conftest.py` 头注释、`test/_support/fakes.py:125`（两个检索入口要一起打桩）、
  `test/conftest.py::_hermetic_backends`。**违反它的典型症状是「服务在时反而绿」**。
- **R4 质量门禁阈值跨平台留容差。** 覆盖率基线在不同 OS / Python 版本上差约 0.09pp；
  `scripts/ci/ratchet.py` 对 `coverage` 有 0.15pp 容差，且必须仍能拦住真实回退。
- **R5 本地不等价，推之前自检**：`python -m pytest` 与 `uv run pytest` **两种调用都跑**；
  把 `.env` 改名后再跑一遍全量测试；最后跑 `scripts/ci/local_ci.py`。
- **R6 GitHub 的 Re-run 只验抖动。** 它固定重跑**当时那个提交**，不能用来验证「代码改了是否通过」——
  必须产生新 run（新 push 或 workflow_dispatch）。

## Reference docs

`docs/project_overview.md` is the comprehensive reference (v2.0, ~1100 lines). It covers architecture, setup, deployment, troubleshooting, technical debt register, and architecture decision records. Consult it for detailed explanations before modifying core flows.

CI / 环境相关的踩坑与判定树：**`docs/踩坑_本地绿CI红.md`**（本地绿、CI 红时先读这个）。

