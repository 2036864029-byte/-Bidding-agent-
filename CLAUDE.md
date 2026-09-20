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
# 注：`integration`/`e2e` 用例**默认跳过**（需真实 Milvus/MySQL/模型隧道）。
# 放行：RUN_INTEGRATION=1 python -m pytest test/ -v

# 工程门禁：一条命令跑完 lint / test / contract / frontend（~2.5 min）
# 入口是 scripts/ci/local_ci.py（本地 CI 运行器）——本仓库无远端，
# .github/workflows/ci.yml 处于休眠状态，**日常入口是这条命令**。
.venv/Scripts/python.exe scripts/ci/local_ci.py
.venv/Scripts/python.exe scripts/ci/local_ci.py lint test   # 只跑指定门禁
.venv/Scripts/python.exe scripts/ci/local_ci.py --list      # 列出门禁

# 风格类棘轮：E501/E731 告警数与覆盖率，只允许比基线好不允许差
# （基线在 scripts/ci/baselines.json，入库；变好时用 --update 固化）
.venv/Scripts/python.exe scripts/ci/ratchet.py lint
.venv/Scripts/python.exe scripts/ci/ratchet.py coverage --report coverage.json

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
- **`nodes/`**: Business nodes, each following the signature `(AgentState) → dict`. `price_inquiry/` is now a **thin node + facade** (P3): `node.py` (entry + P0-11/P0-12 guards + guidance + dual timeout + POST_GUARD) lives here; the engine (intent/queries/recall/semantic/sql_builders/enum_norm/schema/models) moved to `price_engine/`. 曾经的镜像壳 `agent/nodes/answer_templates.py` / `output_templates.py` **已随 P6 结构收敛删除** —— 价格模板只存在于 `price_engine/rendering/`（`test/core/test_legacy_path_allowlist.py` 守卫防回潮）。The package `__init__.py` re-exports engine symbols via `price_engine`, so `from agent.nodes.price_inquiry import ...` is unchanged for external callers.

### `price_engine/` — 价格/标讯查询引擎（P3 下沉）

- **引擎 8 文件**（intent / queries / sql_builders / recall / semantic / enum_norm / schema / models）+ **rendering/**（answer/output 模板）自 `agent/nodes` 下沉；`.db` 引用统一走 `core.rdb.mysql_pool`。
- **召回链（D-6）**：`recall.py` 的 5 级链（Milvus 语义→FULLTEXT→LIKE→逐词拆分→全表扫描）经 `recall_stages.py`（`_SqlRecallStage`/`_SplitKeywordStage`/`build_recall_stages`）接入 `core/retrieval.RecallPipeline` 熔断状态机。**FULL_SCAN 默认禁入**（`PRICE_ALLOW_FULL_SCAN=true` 才放行），全局 deadline=`PRICE_RECALL_DEADLINE_MS`（默认 3000ms）逐条 SQL 钳制。
- **配置开关**：`price_allow_full_scan` / `price_recall_deadline_ms`（`core/config/settings.py`）。
- `price_engine/__init__.py` 引擎门面（re-export 106 符号 + 连接池）。

### `core/retrieval/` — 共享召回熔断状态机（P3 D-1/D-6）

- `base.py`（`RecallStage` 协议 + `RecallOutcome`）+ `pipeline.py`（`RecallPipeline`：阶段短路 / deadline 熔断 / 超时熔断 / `allow_scan` 门控）。`stages.py` 为通用占位——price 特有阶段适配在 `price_engine/recall_stages.py`（避免 core→price_engine 反向依赖）。
- **`checkpointer.py`**: Factory that returns `MemorySaver` (default, ephemeral). Supports `sqlite`/`postgres`/`redis` backends via the same interface — one-line change, zero business code impact.

### `public_kb/` — RAG engine

- **`engine.py`**（由 `rag_engine.py` 更名，**旧名 compat 壳已随 P6 删除，勿再引用旧路径**）: `PublicKnowledgeRAG` class — the public entry point. Methods: `init_knowledge_base(pdf_dir)`, `query(question)`, `clear_kb()`, `add_pdf(path)`. Uses lazy singleton pattern; the knowledge base is read-only after initialization.
- Pipeline: PDF → `MinerUParser` (MinerU API → Markdown) → `TextCleaner` → `SemanticChunker` (heading-aware, 2000 chars/chunk, 100 char overlap) → `create_embeddings()` (BGE-m3, 1024 dims，`core/embeddings/bge_m3.py`) → `MilvusStoreManager` (`core/vectorstore/milvus_client.py`) → `build_chain()` (LCEL hybrid retrieval chain，`public_kb/generation/chain.py`；旧名 `build_qa_chain` / `qa_chain.py` 已随 P6 删除）。
- **配置**：`Settings` dataclass 是全局单一配置源，已上提到 **`core/config/settings.py`**（`public_kb/config.py` 兼容壳已随 P6 删除）。自动加载根 `.env`，所有模块共享。
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

**测试分层的写法约定（新增用例必读）**：默认 hermetic，`integration`/`e2e` 显式 opt-in。
- **不标 marker = 必须离线**：`test/_support/hermetic.py` 会拦掉所有网络出口
  （`socket.connect` / `create_connection`），联网即抛 `NetworkBlockedByTest`，
  提示告诉你是补 mock 还是该标 marker。**别靠"本机跑通了"判断**——这类用例的
  典型形态是"联网失败后走降级、断言恰好成立"，本地绿、换台机器红。
- 需要真实服务（Milvus/MySQL/模型隧道）→ 加 `@pytest.mark.integration`（或 `e2e`），
  默认跳过，`RUN_INTEGRATION=1` 放行。
- **假件优先从 `test/_support/fakes.py` 取**（`ZeroEmbeddings` / `CharCountEmbeddings` /
  `HashEmbeddings` / `patch_kb_search` …），不要在每个用例里内联 lambda ——
  重复手抄正是"漏 mock"类 bug 的温床。
- 会话结束会汇总"哪些非 live 用例试图联网"（含降级后碰巧通过的），是补充信号。

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

**Pytest-compatible tests** (hermetic by default — no live DB/LLM needed):
- `test/_support/` — 测试共用基建：`hermetic.py`（网络闸门）+ `fakes.py`（假件），见上方「测试分层的写法约定」
- `test/test_hermetic_gate.py` — 网络闸门自身的元测试（保证闸门没被改坏 —— 它静默失效的表现是"测试全绿但又在偷偷联网"）
- `test/contract/` — **契约测试**（出口协议 `ReplyType` / 前后端 reply_type / price 列 key）。
  三组断言的"防什么"见文件 docstring；其中最有价值的是把"registry 登记"与
  "出口协议登记"锚在一起（漏登记会静默落 fallback，不报错）
- `test/test_recall_optimization.py` — recall stage logic tests
- `test/test_bug_repairs.py` — regression tests for fixed bugs
- `test/test_p0_*.py` — P0 bug fix verification tests
- `test/test_citation_tracing.py` — citation tracing tests (chunk_uid, standardized citations, validation rules R1-R7)
- ⚠️ `test/test_sub_route.py` — **多数用例需要真实 LLM/MySQL**（类 docstring 有说明），
  已按语义标 `e2e`/`integration`、默认跳过；只有纯逻辑的那几个类默认运行

**Knowledge-base citation evaluation** (requires live Milvus + LLM):
- `python scripts/run_knowledge_citation_eval.py` — full citation traceability eval over `testset_knowledge.jsonl` (~106 legal questions), verifies per-result citation completeness and Milvus back-check association; writes `test_report/knowledge_citation_results.jsonl` + summary report

Run all with: `python -m pytest test/ -v`

## Reference docs

`docs/project_overview.md` is the comprehensive reference (v2.0, ~1100 lines). It covers architecture, setup, deployment, troubleshooting, technical debt register, and architecture decision records. Consult it for detailed explanations before modifying core flows.
