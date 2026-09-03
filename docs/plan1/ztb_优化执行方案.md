# ztb_demo 代码优化执行方案

> **版本**: v1.3（2026-09-02 对抗性自审修正：① `general_chat` 节点实为纯静态回复、不调 LLM（`general_chat.py:43`），模型槽位改为 3 个实际调用点 + general_chat 仅预留；② `price_inquiry/db.py:25-26` 的 `os.getenv` 直读 + 硬编码 MySQL 默认值纳入 P0 配置收口；③ P3 node.py"薄化"明确保留项清单；④ 新增 B-12 同步更新既有布局守卫。v1.1 取消独立基线阶段改为"即时快照"、§3 框架改标注式、新增附录 A/B；v1.2 新增附录 C 并修正 R12 定性——answer/output_templates 实为 price 域渲染引擎，改列 P3 随引擎下沉）
> **依据**: `docs/plan1/项目架构评估结果及改进方案.md`（方案二）为执行主干；`docs/plan1/ztb_architecture_report.md`（方案一）仅作 API 契约 / Supervisor / 评估指标的参考素材
> **已核实代码事实**: 本方案所有论断均已逐条对照代码核实（见各 Phase 的"现状证据"），未核实的表述已修正
> **终局目标**: FastAPI 前后端交互 + RAG/价格能力工具化 + 多 Agent（Supervisor）架构

---

## 0. 总体策略：小步迭代七原则

1. **每 Step 一个 commit，每 Phase 一个可验收里程碑**。任何 Step 完成后 `python -m pytest test/ -v` 必须全绿。
2. **re-export 门面保兼容**。所有文件移动沿用项目已验证的手法（`price_inquiry` 拆包、`qa_chain.py` 兼容壳即先例）：旧路径 `__init__.py` 保留 re-export + `DeprecationWarning`，至少保留一个 Phase 周期。
3. **动手前先拍"现状快照"（不是先建评估体系）**。只在会改动链路的 Phase 启动前，用现有工具花 ≤ 半天跑一遍现状归档（kb 复用现有 106 题脚本，成本≈0；router/price 到 P3/P4 动手前再补题）。快照是"优化前的照片"——没有它，链路优化后无法证明行为没变坏。正式 eval/ 体系建设推迟到工程化阶段（见 §2）。
4. **新旧路径并行，开关切换**。涉及行为变更的重构（如 knowledge_qa 节点重写、Supervisor 上线）用配置开关并行运行新旧实现，eval 对比后切流，旧实现延迟一个 Phase 删除。
5. **配置单一中心**。全项目配置收口于 `core/config/settings.py`（由 `public_kb/config.py` 上提合并），禁止各包各自维护配置（方案一拆三包导致配置分散的教训）。
6. **文档同步**。每个 Phase 验收时同步更新 `CLAUDE.md`、`docs/project_overview.md`、`组件工作机制.md` 中受影响章节。
7. **滚动式细化（rolling-wave）**。只对最近的 Phase 给出文件级任务清单（当前为 P0/P1/P2，见附录 A/B/C）；后续 Phase 保持步骤级。每个 Phase 启动前先把该 Phase 展开成文件级清单、评审后再动手——一次性写完全部 7 个 Phase 的文件级细节，执行到后期必然过时。

### 遵守现有项目约束

- `setuptools < 70`（pymilvus 2.4.x 依赖 `pkg_resources`）；一律使用项目 `.venv` + uv，禁用 conda/系统 Python。
- `public_kb` 初始化后只读；SQL 优先 `=`/`>=`/`<=`/FULLTEXT，避免 `LIKE '%...%'`。
- Router / 意图解析 LLM 一律 `temperature=0`。
- `.env` 永不提交；commit message 沿用现有 conventional 风格（`feat/refactor/perf/chore/docs/test(scope): ...`）。

---

## 1. 现状问题与代码证据速查

| ID | 问题 | 代码证据 | 解决 Phase |
|---|---|---|---|
| R1 | 基础设施（llm/embeddings/config）寄生 `public_kb/`，agent 反向依赖 | `agent/graph.py:23-24`、`agent/nodes/price_inquiry/intent.py:15`、`semantic.py:16-17`、`db.py:12` | P0 |
| R2 | `qa_chain` 检索+生成焊死，节点无法干预中间过程 | `public_kb/generation/chain.py:121-127`（LCEL 一体链）；`agent/nodes/knowledge_qa.py:61` 只能 `rag.query()` 取终态 | P1 |
| R3 | 模型无抽象/无任务槽位（是工厂+单配置，非字面硬编码） | `public_kb/services/llm.py:19-39` 全部调用共享同一 Settings 模型，仅 temperature 可覆盖 | P0 |
| R4 | RAG 引擎无锁全局单例，并发竞态 | `agent/nodes/knowledge_qa.py:19-31` 模块级 `_rag_engine` 惰性初始化无锁 | P1 |
| R5 | price_inquiry 引擎逻辑（8 文件）混居 nodes 层 | `agent/nodes/price_inquiry/{recall,queries,sql_builders,semantic,intent,db,schema,models,enum_norm}.py` | P3 |
| R6 | 意图 taxonomy 双处维护 | `agent/router.py:112-130`（全局 5 分类）vs `intent.py:40-115`（sub_route 二级分类；实为一次 LLM 调用同时做分类+槽位抽取） | P3 |
| R7 | ingestion 无显式三段式、无断点续跑、csv 走文本管道 | `ingestion/pipeline.py`（source→validate→sink 线性）；chunk 级去重已有（`pipeline.py:82` M2），文档级解析前去重缺失；`sources/csv_source.py` 文本序列化 | P5 |
| R8 | AgentState 单 TypedDict 承载异构产物 | `agent/state.py:19-39`，`business_result` 注释列 5 种异构结构 | P4 |
| R9 | RouterIntent 硬编码，新增分支改 5 处 | `agent/router.py:29-35` 枚举 + 5 个 tool + map + prompt + `graph.py:152-162` 条件边 | P4 |
| R10 | Milvus 双用途无 schema/embedding 版本治理 | `public_kb` + `mysql_price_semantic`（`semantic.py:26`） | P0 |
| R11 | 召回全表扫描兜底无熔断（有单 SQL 超时） | `recall.py:271` FULL_SCAN；`_execute_sql_with_timeout` 已有 | P3 |
| R12 | ~~跨节点输出协议放 nodes 内部~~ **定性修正**：两文件实为 **price 域渲染引擎**（query_type 模板 + 字段筛选，绑定 ztb_clean 表结构），仅被 `price_inquiry/node.py:12-13` 引用 | `agent/nodes/answer_templates.py`、`output_templates.py`（附录 C-0 核实修正） | P3（随引擎下沉） |
| R13 | 9 个 `pdf_*` 文件平铺 transforms/ | `public_kb/ingestion/transforms/pdf_*.py` ×9 | P5 |
| R14 | 无 API 层 / 无可观测性 | 全项目仅 `deploy/mineru/service/app.py` 用 FastAPI；无 LangSmith/指标 | P2 |

---

## 2. 验证体系（回答：只做单元测试够不够？）

**结论：只做单元测试不够，必须增加逐步验证环节。** 但不需要重型 CI，用轻量"金标基线 + 每 Phase 验收门禁"即可。

**为什么单测不够**：本次重构最大的风险不是逻辑错误（单测擅长抓这个），而是**行为漂移**——prompt 搬家、LCEL 链重装配、模型槽位切换、召回路径改动、节点重写之后，`pytest test/`（全部 mock 掉 LLM/Milvus/MySQL）依然全绿，但线上回答质量、引用合法性、路由准确率已经悄悄劣化。mock 测的是"代码能跑"，测不了"行为没变"。

### 四层验证模型

| 层 | 名称 | 内容 | 运行时机 | 成本 |
|---|---|---|---|---|
| L1 | 单元测试 | 现有 350+ mock 单测 + 每 Step 新增 | 每 Step（commit 前） | 秒级 |
| L2 | 契约/兼容守卫 | ① re-export import 兼容测试（旧路径仍可 import）；② AST 布局守卫（沿用 `test/test_public_kb_layout.py` 模式）；③ 工具契约测试（`kb_search` 脱离 agent 独立调用） | 每 Step | 秒级 |
| L3 | 行为基线回归（金标） | 每个 Phase 动手**前**冻结基线，完成**后**重跑对比，指标不降才验收 | 每 Phase 首尾 | 分钟级 |
| L4 | 端到端冒烟（live） | 真实 Milvus/MySQL/LLM 链路跑 5~10 个代表问题，人工过一眼回答 | 每 Phase 验收 | 十分钟级 |

### L3 现状快照与金标基线（即时、轻量、不单独占工期）

**快照规则**：每个会改动链路的 Phase 启动前，用 ≤ 半天完成对应快照并归档 `eval/baseline/`（先入 git 的简单 json，正式目录结构随工程化阶段再补）：

| 时点 | 快照内容 | 工具/成本 |
|---|---|---|
| P0 启动前 | router 意图 10~20 题手工快拍（记录分类结果）+ kb 106 题 citation 摘要 | 复用 `scripts/run_knowledge_citation_eval.py`（106 题集 `test/testset_knowledge.jsonl` 已存在，成本≈0） |
| P1 启动前 | kb 106 题复跑（P0 已跑且未动检索链路则直接沿用） | 同上 |
| P3 启动前 | price 典型查询 20~30 题，记录 sub_route/query_type/结果行数/首行关键字段 | 半天内手写 jsonl + 一个对比脚本 |
| P4 启动前 | router 意图集补齐到 30~50 题（P4 要动 router，此时才需要正式数据集） | 半天 |

**快照要点**：
- 只记**摘要指标**（准确率、引用完整率、校验通过率、行数一致率），不录全量回答文本——避免 DeepSeek 非确定性导致的噪声。
- price 快照记"行数 + 首行关键字段"而非全量行，降低对业务数据变化的敏感度。

**正式 eval/ 体系建设**（datasets/runners 自动化、CI 门禁）推迟到 P2 之后的工程化阶段，与可观测性一起做——届时再把快照期的 jsonl 固化成数据集。

### 各 Phase 验证门禁汇总

| Phase | L1 单测 | L2 契约守卫 | L3 基线回归 | L4 冒烟 |
|---|---|---|---|---|
| P0 基础设施上提 | 全绿 + 双 provider structured_output 一致性 | `grep -rn "public_kb.services" agent/` 为空（除 re-export 壳） | router 意图集不降 | 3 个 CLI 问题走通 |
| P1 RAG 工具化 | 全绿 + kb_search 独立单测 | `PublicKnowledgeRAG` 旧入口可用；guardrail 独立 import | 106 题 citation 指标不降 | 5 个 kb 问题人工过目 |
| P2 FastAPI | 全绿 + schemas 校验测试 | OpenAPI 文档生成；`/health` 200 | 不涉及行为变更 | 并发 10 请求无串扰、SSE 流式正常 |
| P3 price 下沉 | 全绿 + 熔断测试（mock 超时禁全表扫描） | `from agent.nodes.price_inquiry import ...` 旧路径可用 | price golden 一致率不降 | 3 类 price 查询走通 |
| P4 接口预留 | 全绿 + echo capability 零改接入演示 | registry 候选集 == 现有 5 分支 | router 意图集不降 | 全分支各 1 题 |
| P5 ingestion 重构 | 全绿 + 幂等/续跑测试 | chunk 契约（contracts.py）不变 | 同一批 PDF 两次入库向量数相同 | 杀进程 `--resume` 续跑成功 |
| P6 多 Agent | 全绿 | 新 agent 接入 ≤2 文件 | 单图 vs Supervisor 双跑 eval 对比 | 跨 agent handoff 端到端 1 例 |

---

## 3. 优化后的目标项目框架（终态）

> 到达路径：并非一次建成，各目录由对应 Phase 落地（括号内标注 Phase）。`test/`、`scripts/`、`docs/`、`milvus/`、`docker/`、`deploy/`、`archive/` 保留现状不搬。

```
ztb_demo/
│
├── core/                                # ★(P0 新建) 全局基础设施 + 协议层 —— 解 R1/R3/R12
│   ├── config/
│   │   ├── settings.py                  # 单一配置中心（public_kb/config.py 整体上提，dataclass + .env 加载方式不变）
│   │   └── profiles/
│   │       └── models.yaml              # ★ 模型槽位：3 个实际调用点 router / kb_generation / price_intent（general_chat 不调 LLM，general_chat.py:43——槽位仅预留），改 yaml 即换模型
│   ├── llm/
│   │   ├── base.py                      # LLMProvider ABC：chat / structured_output / stream（router 依赖 structured_output，必须进 ABC）
│   │   ├── registry.py                  # 注册表 + 工厂：按槽位创建
│   │   └── deepseek.py                  # ← public_kb/services/llm.py 迁移（create_llm 逻辑包进 Provider）
│   ├── embeddings/
│   │   ├── base.py                      # Embedder ABC + model_id/version 属性（version 供 schema_registry 校验）
│   │   └── bge_m3.py                    # ← services/embeddings.py 迁移
│   ├── vectorstore/
│   │   ├── milvus_client.py             # ← services/milvus_store.py 迁移（仅连接/索引管理，逻辑不改）
│   │   └── namespace.py                 # ★ 集合命名集中治理：public_kb / mysql_price_semantic（现散在 semantic.py:26 等处）—— 解 R10
│   ├── rdb/
│   │   └── mysql_pool.py                # ← price_inquiry/db.py 上提（全局连接池，db.py 留 re-export）
│   ├── retrieval/                       # ★(P3 新建) 共享召回降级状态机
│   │   ├── pipeline.py                  # RecallPipeline：stages 编排 + deadline 熔断（默认禁全表扫描）—— 解 R11
│   │   └── stages.py                    # VectorStage / FulltextStage / LikeStage / ScanStage
│   ├── protocol/                        # ★(P2 新建) 对外回复薄协议（API/CLI 出口统一模型）
│   │   ├── reply.py                     # AgentReply（Pydantic）—— 新建，不含 price 模板（模板实为 price 域资产，见附录 C-0）
│   │   └── renderers/                   # cli / api 渲染器（CLI 收编 __main__._render_business_data 逻辑）
│   └── observability/                   # ★(P2 起步) logger / metrics / tracer（LangSmith 留钩子）
│
├── public_kb/                           # RAG 能力包（离线+在线同包保留 —— D1：契约共享，伸缩用部署边界解决）
│   ├── contracts.py / chunk_ids.py      # 保留：chunk 契约单一来源（不动）
│   ├── schema_registry.py               # ★(P0 新增) collection schema + embedding 版本注册，启动校验防换模型翻车
│   ├── engine.py                        # ← rag_engine.py 改名重构(P1)：构造注入去单例；rag_engine.py 留 import 壳
│   ├── tools.py                         # ★(P1 新增) kb_search()→RetrievalResult 三元组 + kb_search_as_tool() —— 解 R2
│   ├── retrieval/                       # 保留：密疏检索/RRF/重排/降级（企业级水准，不动）
│   ├── rendering/                       # ★(P1) ← generation/context.py（chunks→prompt 渲染，依赖 chunk 契约故留 RAG 侧）
│   ├── guardrails/                      # ★(P1) ← generation/citations_*（R1-R7 护栏独立，多 Agent 复用）
│   └── ingestion/                       # (P5) 三段式重构 —— 解 R7/R13
│       ├── router.py                    #   Stage A：FormatRouter（路由前文档级 MD5/SimHash 去重）
│       ├── normalizers/                 #   Stage B：text / csv(双 IR) / pdf（9 个 pdf_* 收敛于此）
│       ├── transforms/                  #   Stage C：cleaner / dedup(chunk_uid 幂等) / chunker
│       ├── checkpoint.py                #   阶段化 checkpoint：--resume / --rerun-from
│       ├── models.py                    #   NormalizedDocument IR（parser 版本 + 质量分）
│       └── sources/  sinks/             #   保留；milvus_sink 改 upsert by chunk_uid（重跑幂等）
│
├── price_engine/                        # ★(P3 新建) 价格/标讯引擎 —— 解 R5（nodes 层下沉）
│   ├── __init__.py                      # re-export 门面（沿用 price_inquiry 拆包已验证范式）
│   ├── intent.py                        # 槽位抽取/查询改写（sub_route 保留为内部分流，文档化边界 —— 解 R6）
│   ├── queries.py / sql_builders.py / recall.py   # recall 接入 core/retrieval 熔断状态机
│   ├── semantic.py / enum_norm.py / schema.py / models.py
│   ├── rendering/                       # ← nodes/answer_templates.py + output_templates.py（price 域渲染引擎，随引擎下沉 —— 修正 R12）
│   └── tools.py                         # ★ price_query() + price_query_as_tool()
│
├── agent/                               # 编排层（P4 前保持单图，P6 子图化）
│   ├── state.py                         # (P4) 分层：messages + intent + handoff(预留) + result 联合类型 —— 解 R8
│   ├── registry.py                      # ★(P4 新增) CapabilityRegistry：能力自描述，router 候选集动态生成 —— 解 R9
│   ├── router.py                        # (P4) 只管分支路由；硬编码枚举/工具/prompt 三处改由 registry 驱动
│   ├── graph.py                         # (P4) 按 registry 动态加边；_with_fallback 保留
│   ├── checkpointer.py                  # 保留原位（4 后端工厂；搬迁无收益）
│   ├── guardrail_hooks.py               # ★(P1 新增) with_guardrails(node, [...]) 装饰器
│   └── nodes/
│       ├── base.py                      # ★(P4 新增) NodeSpec 协议（name/entry/state_keys/fallback）
│       ├── knowledge_qa/                # ★(P1 拆包) node.py(薄) + prompts.py + chain.py（生成编排归 agent —— D2）
│       ├── price_inquiry/node.py        # (P3) 薄化：仅 intent/queries/recall/semantic/rendering 下沉引擎；P0-11/P0-12 守卫、能力边界、引导语、双超时降级、POST_GUARD 全部留在节点侧
│       └── general_chat.py / doc_qa.py / fallback.py   # 保留
│
├── api/                                 # ★(P2 新建) 对外接口层 —— 解 R14
│   ├── server.py                        # FastAPI：POST /chat（SSE，复用 AgentGraph.stream）、GET /health
│   ├── schemas.py                       # ChatRequest/ChatResponse（参考方案一附录 9.1，裁剪暂不需要的字段）
│   ├── deps.py                          # AgentGraph 进程级单例注入（P1 去全局单例后线程安全）
│   └── cli.py                           # 收编 agent/__main__.py 交互逻辑（python -m agent 保留转发）
│
├── orchestration/                       # ★(P6 新建) 多 Agent 主图
│   ├── supervisor.py                    # 三级路由（规则→语义→LLM）+ handoff 协议（参考方案一 Step 3.3）
│   └── main_graph.py
│
├── eval/                                # 快照期仅存 baseline/*.json；正式 datasets/runners 随工程化阶段补（见 §2）
│
├── test/                                # 保留现状（350+ 单测 + 诊断脚本；新单测继续放这里，不搬迁 —— 搬迁是纯 churn）
├── scripts/  docs/  milvus/  docker/  deploy/  archive/   # 保留现状
└── .env.example  requirements.txt  CLAUDE.md  ...
```

**与方案二框架的三处有意偏离**（结构其余部分与方案二 2.1 一致）：
1. 不新建 `tests/` 三层目录——现有 `test/` 已承载 350+ 单测与诊断脚本且被 CLAUDE.md 文档化，搬迁是纯 churn；正式评估集放独立 `eval/`。
2. `checkpointer.py` 保留 `agent/` 原位（方案二原案亦注明"二选一，保留原位减少迁移面"）。
3. `api/` 提前到 P2 落地（方案二原排 P5），`eval/` 正式建设推迟到工程化阶段——与用户目标（前后端交互优先）对齐。

### 关键设计决策（沿用方案二，已结合代码核实）

| 决策 | 内容 | 理由 |
|---|---|---|
| D1 | public_kb 不拆离线/在线两个包 | contracts.py 契约共享；伸缩用部署边界（worker/服务镜像）解决；方案一物理拆包对当前规模是过度工程 |
| D2 | generation/ 三分：prompts+chain→agent 节点包；context→public_kb/rendering；citations→public_kb/guardrails | 生成策略归 agent（可自由换 prompt），契约渲染归 RAG，护栏平台化多 Agent 复用；与实际文件划分精确对应 |
| D3 | 基础设施上提 core/，能力包构造注入 | 解 R1/R3；插拔在装配层完成 |
| D4 | embedding 不做运行时热插拔，做"版本化集合 + 离线重索引 + 灰度切流" | 换 embedding = 换向量空间，必须重建索引；schema_registry 启动校验版本一致 |
| D5 | 节点薄化、引擎下沉、能力包暴露 tools.py 门面 | knowledge_qa 薄节点模式推广；tool 形态是 P6 多 Agent 复用前提 |
| D6 | FastAPI 提前到 P2（方案二原排 P5） | 用户目标以前后端交互为先；P1 完成后 agent 已具备干净调用面，成本最低 |

---

## 4. 迁移映射表（旧路径 → 新路径）

| 旧路径 | 新路径 | 动作 | Phase |
|---|---|---|---|
| `public_kb/services/llm.py` | `core/llm/deepseek.py` | 移动 + 抽象基类 | P0 |
| `public_kb/services/embeddings.py` | `core/embeddings/bge_m3.py` | 移动 + version 属性 | P0 |
| `public_kb/services/milvus_store.py` | `core/vectorstore/milvus_client.py` | 移动 | P0 |
| `public_kb/config.py` | `core/config/settings.py` | 上提合并，旧路径 re-export | P0 |
| `agent/nodes/price_inquiry/db.py` | `core/rdb/mysql_pool.py` | 上提 | P0 |
| `public_kb/generation/context.py` | `public_kb/rendering/context.py` | 移动 | P1 |
| `public_kb/generation/citations_*.py` | `public_kb/guardrails/` | 移动 | P1 |
| `public_kb/generation/prompts.py` + `chain.py` | `agent/nodes/knowledge_qa/` | 移动（生成编排归 agent） | P1 |
| `public_kb/rag_engine.py` | `public_kb/engine.py` | 去单例、构造注入；`PublicKnowledgeRAG` 门面保留 | P1 |
| `public_kb/qa_chain.py` | 拆除：检索→`tools.py`，生成→`knowledge_qa/chain.py`；旧签名保留委托一个周期 | 拆分 | P1 |
| `agent/nodes/price_inquiry/` 8 个引擎文件 | `price_engine/` | 下沉 + re-export | P3 |
| `agent/nodes/answer_templates.py` `output_templates.py` | `price_engine/rendering/` | 随引擎**下沉**（非上提；R12 定性修正见附录 C-0） | P3 |
| `public_kb/ingestion/transforms/pdf_*.py` ×9 | `ingestion/normalizers/pdf/` | 收敛 | P5 |
| `agent/__main__.py` CLI 逻辑 | `api/cli.py` | 收编，原入口转发 | P2 |

---

## 5. 分阶段执行

### Phase 0：core/ 基础设施上提与模型槽位（P0，约 1.5~2 周）

**目标**：解 R1/R3/R10。改配置即换模型；agent 不再 import `public_kb.services`。

**步骤**：
1. **建 core/ 骨架**：`core/config/`、`core/llm/`、`core/embeddings/`、`core/vectorstore/`、`core/rdb/`。
2. **抽象基类**（只覆盖现有差异点，不过度设计）：
   - `LLMProvider`：`chat(messages)` / `structured_output(messages, schema)` / `stream(messages)`——router 和 intent.py 依赖 structured_output，必须进 ABC。
   - `Embedder`：`embed_documents` / `embed_query` + `model_id` + `version`。
3. **迁移 + 注册表**：`services/llm.py`→`core/llm/deepseek.py`；`embeddings.py`→`core/embeddings/bge_m3.py`；`milvus_store.py`→`core/vectorstore/milvus_client.py`；`db.py`→`core/rdb/mysql_pool.py`。旧路径全部 re-export + DeprecationWarning。
4. **配置收口**：`public_kb/config.py` 合并入 `core/config/settings.py`（保持 `@dataclass Settings` + `.env` 加载方式不变，降低迁移风险）；新增 `profiles/models.yaml` 定义模型槽位——**3 个实际调用点**（router / kb_generation / price_intent，均 temperature 区分；general_chat 纯静态不调 LLM，`general_chat.py:43`，仅预留不入槽位，与现状行为一致）。
5. **schema_registry**：`public_kb/schema_registry.py` 登记 `public_kb` 与 `mysql_price_semantic` 两集合的 schema、索引参数、embedding version；ingestion 与 retrieval 启动时校验，不一致即报错拒绝服务。
6. **调用点切换**：`agent/graph.py:23-24`、`price_inquiry/intent.py:15`、`semantic.py:16-17` 改为从 core 装配（经 registry 按槽位取 LLM）。

**验收**：
- L1：全绿 + 新增 registry 单测（含 fake provider 验证 structured_output 一致性）。
- L2：`grep -rn "public_kb.services" agent/` 结果为空（`public_kb/services/` 仅剩 re-export 壳）。
- L3：router 意图集指标不降。
- L4：CLI 3 个问题走通。
- 配置验证：改 `models.yaml` 任一槽位 model 名，零代码改动生效（日志确认）。

**回滚**：core/ 为纯新增 + re-export，回滚 = git revert。

---

### Phase 1：RAG 工具化边界改造（P0，约 2 周）

**目标**：解 R2/R4。RAG 变成"可被 agent 当工具调用、返回 chunks+引用元数据+渲染上下文"的能力包；knowledge_qa 节点拿到中间过程控制权。

**步骤**：
1. **冻结基线**：跑 106 题 citation eval 归档 `eval/baseline/p1_kb_citation_before.json`。
2. **定义工具契约**（`public_kb/tools.py`）：
   ```python
   class RetrievalResult(BaseModel):
       chunks: list[Chunk]                 # 精排后文本块
       citations_meta: list[CitationMeta]  # chunk_uid/来源/页码/分数
       rendered_context: str               # rendering/context.py 渲染好的参考文本
       recall_trace: RecallTrace           # 命中策略/诊断（复用现有 retrieval_diagnostics）

   def kb_search(query: str, top_k: int = 8) -> RetrievalResult: ...
   def kb_search_as_tool() -> BaseTool: ...   # langchain tool 包装，P6 直接挂 agent
   ```
   实现：从 `generation/chain.py:53-69` 抽出 HybridRetriever 装配逻辑；`kb_search` 内部走"retriever.retrieve → format_docs/build_sources/build_citations"，**不调 LLM**。
3. **generation/ 三分迁移**（按映射表，全部 re-export）：`context.py`→`rendering/`；`citations_*`→`guardrails/`；`prompts.py`+生成段→`agent/nodes/knowledge_qa/`。
4. **knowledge_qa 拆包 + 节点重写**：
   ```python
   # agent/nodes/knowledge_qa/node.py
   def node_knowledge_qa(state, config):
       r = kb_search(question)                          # ① 工具调用，拿到中间产物
       prompt = build_qa_prompt(state, r.rendered_context)  # ② agent 侧 prompt，可自由改
       answer = llm_slot("kb_generation").chat(prompt)  # ③ 槽位模型
       ok, report = citations_validate(answer, r.citations_meta)  # ④ 护栏
       if not ok: ...                                    # ⑤ 降级/重试路径
   ```
   同时**删掉 `knowledge_qa.py:19-31` 的无锁全局单例**：引擎/检索器实例由装配层（registry 或 deps）持有并注入。
5. **guardrail_hooks.py**：`with_guardrails(node, [citations_r1_r7])` 装饰器，供后续所有 agent 复用。
6. **兼容层**：`PublicKnowledgeRAG.query()`、`build_qa_chain()` 签名保留，内部改为组合新路径（tools + 生成链），一个 Phase 后标记删除。

**验收**：
- L1：全绿 + `kb_search` 脱离 agent 独立单测（mock retriever）。
- L2：`from public_kb import PublicKnowledgeRAG` 旧入口可用；`public_kb/guardrails` 可独立 import 不依赖 agent。
- L3：106 题 citation 指标（引用完整率/校验通过率）不降。
- L4：5 个 kb 问题人工过目，回答与引用正常；演示"不改 public_kb 仅改 agent 侧 prompt"一次。

**回滚**：开关 `USE_TOOL_KB=false` 走旧 `rag.query()` 路径（保留一个 Phase）。

---

### Phase 2：FastAPI 服务层 + 协议层（P0，约 1~1.5 周，自方案二 P5 提前）

**目标**：解 R14/R12。前后端可用 HTTP/SSE 交互；输出协议从 nodes 内部上提。

**步骤**：
1. **core/protocol/reply.py**（新建薄协议，详见附录 C-0 修正）：`AgentReply` Pydantic 模型（reply_type/answer/citations/data/trace）+ `to_reply(business_result)` 出口适配；**不迁入** `answer_templates.py`/`output_templates.py`（经核实其为 price 域渲染引擎，改列 P3 随引擎下沉到 `price_engine/rendering/`）。节点内部结构本 Phase 不动，协议转换只在 API/CLI 出口层做。
2. **api/schemas.py**：`ChatRequest{question, session_id, stream}` / `ChatResponse{answer, intent, citations, latency_ms}`（参考方案一附录 9.1，**裁剪** tenant_id/token_usage 等暂不需要的字段）。
3. **api/deps.py**：`AgentGraph` 进程级单例（启动时构建，非每请求构建）；确认 P1 之后 knowledge_qa 已无全局竞态；`create_checkpointer` 后端走环境变量。
4. **api/server.py**：`POST /chat`（`stream=true` 时 SSE，直接复用 `AgentGraph.stream()`，`graph.py:237` 已具备）、`GET /health`。uvicorn 启动入口 `python -m api.server`。
5. **api/cli.py**：收编 `agent/__main__.py` 交互逻辑；原 `python -m agent` 入口保留转发。
6. **observability 起步**：`core/observability/metrics.py` 埋 3 个最小指标（各分支耗时、guardrail 失败率、召回降级命中层），先 log 输出，LangSmith 接入留钩子。

**验收**：
- L1：全绿 + schemas 边界测试（空问题/超长输入/非法 stream 值）。
- L2：`/openapi.json` 可生成；`python -m agent` 旧 CLI 行为一致（走 `api/cli.py` 转发后）。
- L4 冒烟：并发 10 请求 session 隔离无串扰；SSE 逐 token 到达；`/health` 200。

**回滚**：api/ 纯新增，不影响 CLI。

---

### Phase 3：price_engine 下沉 + 意图契约 + 召回熔断（P1，约 2 周）

**目标**：解 R5/R6/R11。nodes 层只剩薄节点；召回链有熔断；意图 taxonomy 单点化。

**步骤**：
1. **冻结基线**：price golden 归档。
2. **下沉**：`price_inquiry/` 8 个引擎文件迁 `price_engine/`，`__init__.py` re-export 保 import 路径不变（项目已验证范式）；`agent/nodes/answer_templates.py`、`output_templates.py` 迁 `price_engine/rendering/`（附录 C-0 修正后的归属），旧路径 re-export；`nodes/price_inquiry/node.py` 薄化为"守卫 + 调 price_engine + 状态读写"。**薄化保留项清单（node.py 现为 496 行，逐段核实）**——以下节点层策略**留在节点侧**，不得随引擎下沉：P0-11 三层查询实体守卫与统一引导语（`node.py:104/:128`）、P0-12 项目编号纠偏与注入（`:216/:230`）、`product_query` 能力边界拦截（`:50`）、LLM 60% 超时降级（`:275`）与 SQL 线程池超时（`:303`）、POST_GUARD 召回后校验（`:343` bidding_query、`:376` company_query）；**下沉项**仅 intent.py / queries.py / sql_builders.py / recall.py / semantic.py / enum_norm.py / schema.py / models.py 及 rendering 两模板。
3. **core/retrieval/ 共享降级状态机**：
   ```python
   class RecallPipeline:
       def __init__(self, stages: list[RecallStage], deadline_ms: int = 3000,
                    allow_scan: bool = False): ...
   ```
   `price_engine/recall.py` 的 5 级链（`recall.py:36` 阶段权重表）改造为 stage 实现接入；**默认 `allow_scan=False`**——deadline 到或前序阶段全失败时熔断返回空+降级文案，不再裸进 FULL_SCAN（`recall.py:271` 现状）；全表扫描仅在显式配置下放行。
4. **意图契约单点化**：全局意图枚举单点定义于 `agent/registry.py`；`price_engine/intent.py` 文档化重命名为"槽位抽取/查询改写"（sub_route 保留为引擎内部分流，**不删逻辑**——它是一次 LLM 调用同时产出，删除反而增加调用）；`组件工作机制.md` 补两级边界说明。
5. **tools 门面**：`price_engine/tools.py` 暴露 `price_query()` / `price_query_as_tool()`。

**验收**：
- L1：全绿 + 熔断测试（mock Milvus/FULLTEXT 超时，断言不触发全表扫描）。
- L2：`from agent.nodes.price_inquiry import ...` 旧路径可用。
- L3：price golden 的 sub_route 一致率 / 行数一致率不降。
- L4：企业详情/中标历史/项目编号 3 类查询走通。

**回滚**：`PRICE_ENGINE_LEGACY=true` 走旧包路径（re-export 天然支持）。

---

### Phase 4：多 Agent 接口预留（P1，约 1~1.5 周）

**目标**：解 R8/R9。新增能力零改 router/graph；为 P6 子图化铺路。

**步骤**：
1. **NodeSpec 协议**（`agent/nodes/base.py`）：`name / entry / state_keys / fallback` 四元组；现有 5 节点全部登记。
2. **State 分层**：`AgentState` 改为 `messages + intent + handoff(预留) + result(Union[KBResult, PriceResult, ChatResult, ...])`；`business_result` 保留一个 Phase 的适配器（旧读法自动映射到新 result）。
3. **CapabilityRegistry**（`agent/registry.py`）：`CapabilitySpec{id, description, node, model_slots, tools}`；`router.py` 的候选集与 prompt 由 registry 动态生成（替换 `router.py:29-130` 的硬编码枚举+tool+prompt 三处）；`graph.py` 按 registry 动态注册节点与条件边。
4. **演示验证**：新增 echo demo capability，仅写一个 spec 文件完成接入，router/graph 零修改。

**验收**：L1 全绿；L3 router 意图集不降；echo 演示通过；全分支各 1 题冒烟。

**回滚**：registry 数据驱动，摘除 spec 即回退。

> **P4 执行状态（2026-09-03，完成）**：
> - ✅ P4-1 NodeSpec 协议 + P4-3 声明层（2026-09-02，见上一行）。
> - ✅ **P4 动态化接线（router/graph，字节等价 + L3 验证，09-03）**：CapabilitySpec 增
>   `route_desc / route_tool_doc / examples / enabled`（路由原文下沉为 registry 数据，
>   `enabled=False`=预留防误路由）；`router.py` 删 RouterIntent/5 个 route_* tool/
>   `_TOOL_INTENT_MAP`/`ROUTER_SYSTEM_PROMPT`，改由 `build_system_prompt /
>   build_route_tools / build_decision_model(动态 Literal) / build_router_node(llm, registry=)`
>   生成；`graph.py` 按 `registry.enabled_specs()` 动态建节点与条件边（base.py 增
>   `register/get_node_spec` 节点存储）。echo_demo 登记 enabled + 注册 entry 即接入
>   （test_p4_dynamic_wiring 6 用例离线证明零改 router/graph）。
> - ✅ **L1 434 passed**；✅ **L2 门禁**：默认 enabled 候选集 == 现有 5 分支。
> - ✅ **L3**：`test/core/test_router_byte_equivalence.py` 断言生成器输出 == 改动前冻结
>   真值 `test/golden/`（系统提示 + 5 tool 逐字节）；live 33 题 before/after 逐题一致
>   100%，accuracy 93.94% 持平（数据集 `eval/router_intent_set.json`）。
> - ✅ L4 冒烟：general_chat / knowledge_qa 走通（price 分支待实库，见 P3）。
> - ⏸️ **P4-2 State 分层推迟 P6**：与 state.py 明示设计原则（共享 TypedDict 不加分支
>   字段）相悖，且属 §7 中风险（checkpointer 历史会话）；R8 随 P6 子图化自然引入分层。

---

### Phase 5：ingestion 三段式流水线重构（P2，约 2~3 周，可与 P3/P4 并行）

**目标**：解 R7/R13。幂等、可断点续跑、csv 表格语义保留。

**步骤**：
1. **IR 建模**：`NormalizedDocument{doc_id, source_format, ir_format: markdown|table, content, metadata(parser 版本/质量分)}`。
2. **Stage A FormatRouter**：扩展名+内容嗅探分发；**路由前文档级去重**（MD5+SimHash 登记 ingest_ledger，避免重复烧 MinerU 算力——对接现有 `pdf_mineru_router.py:41` 的缓存 key 机制）。
3. **Stage B normalizers**：`Normalizer ABC → normalize(source) -> NormalizedDocument`；pdf/ 子包收敛 9 个 `pdf_*` 文件 + mineru 解析器；csv 双 IR 输出（table 摘要供 SQL 路 + 行块语义供向量路）；text 直通。
4. **Stage C CommonPipeline**：clean → chunk 级 dedup（沿用 M2 chunk_uid，`pipeline.py:82`）→ chunk → embed → sink；`milvus_sink` 改 **upsert by chunk_uid**。
5. **checkpoint**：每 stage 落盘中间产物 + `run_manifest.json`；`--resume` / `--rerun-from=B`（解析器升级只重跑 B+C）。
6. **质量关卡**：quality_score 低于阈值进 review_queue 旁路输出，不直接入库。

**验收**：L1 全绿；同一批文件跑两遍 Milvus 向量数不变；杀进程后 `--resume` 成功；csv table IR 可被 price 侧消费示例。

**回滚**：旧 pipeline 保留为 `pipeline_legacy.py`，CLI 加 `--legacy` 开关。

---

### Phase 6：多 Agent 正式落地（P2，约 3~4 周）

**目标**：Supervisor 形态上线，与单图模式灰度并行。

**步骤**：
1. **子图化**：knowledge_qa 按 NodeSpec 把 entry 换成 compiled subgraph（内部 search→prompt→llm→guardrail→retry 循环）；price_inquiry 同样子图化。
2. **Supervisor 主图**（`orchestration/`）：三级路由（规则快路径→语义相似度→LLM 分类）；复合意图分解与拓扑并行调度参考方案一 Step 3.3；handoff 协议 `HandoffMeta{from, to, reason, payload}`。
3. **工具化复用验证**：新增 doc_agent（文档问答补全），直接挂 `kb_search_as_tool()` + `price_query_as_tool()` + 自有 prompts，验证能力包双形态复用。
4. **灰度**：`AGENT_MODE=single|supervisor` 开关并行，eval 双跑对比（router 集 + 复合意图集）后切流。
5. **记忆**：checkpointer 按 thread_id 隔离（已具备），handoff 上下文经 messages 传递。

**验收**：跨 agent handoff（price 查询需政策解释→转 knowledge）端到端通过；新 agent 接入 ≤1 spec + 1 子图目录；双跑 eval 指标不降。

**回滚**：`AGENT_MODE=single` 立即回单图。

---

## 6. 排期与验收汇总

| Phase | 内容 | 优先级 | 预计工期 | 硬验收（门禁） |
|---|---|---|---|---|
| 0 | core/ 上提 + 模型槽位 | 🔴 P0 | 1.5~2 周 | grep 无反向依赖；改 yaml 换模型；router 快拍不降 |
| 1 | RAG 工具化 | 🔴 P0 | 2 周 | kb_search 独立单测；106 题 citation 不降 |
| 2 | FastAPI + 协议层 | 🔴 P0 | 1~1.5 周 | 并发无串扰；SSE 流式；/health 200 |
| 3 | price_engine + 熔断 | 🟡 P1 | 2 周 | 熔断测试；price golden 不降 |
| 4 | 多 Agent 接口预留 | 🟡 P1 | 1~1.5 周 | echo 零改接入 |
| 5 | ingestion 三段式 | 🟢 P2 | 2~3 周（可与 3/4 并行） | 重跑幂等；断点续跑 |
| 6 | Supervisor 多 Agent | 🟢 P2 | 3~4 周 | handoff 端到端；双跑 eval 不降 |

**依赖关系**：`0 → 1 → 2 →（3 ∥ 4 ∥ 5）→ 6`。FastAPI（P2）依赖 P1 的干净调用面；多 Agent（P6）依赖 P4 的接口预留与 P1/P3 的 tools 门面。

**总计**：约 10~15 周（单人）；P3/P4/P5 若并行可压缩至 8~12 周。

---

## 7. 风险登记与应对

| 风险 | 等级 | 应对 |
|---|---|---|
| 重构期行为漂移（prompt/链重装配） | 高 | §2 四层验证，L3 基线门禁不豁免 |
| DeepSeek 非确定性导致基线波动 | 中 | 基线只记摘要指标 + 跑 2 次取一致值；router/intent 均 temperature=0 |
| price golden 对业务数据变化敏感 | 中 | 只记行数+首行关键字段；数据大批量变更时重建基线并记录原因 |
| State 分层（P4）破坏 checkpointer 历史会话 | 中 | MemorySaver 默认无历史负担；若届时已用持久后端，写一次性 migrate 脚本（参考方案一附录 9.3） |
| Phase 跨度内需求插入 | 低 | 每 Phase 独立可停；任何 Phase 验收点都是可发布状态 |
| 单人执行并行 Phase 冲突 | 低 | P5（离线）与 P3/P4（在线）文件零交集，可安全并行 |

---

## 附录 A：Phase 0 文件级任务清单

> 每项 = 一个 commit 粒度。顺序即依赖顺序。"源"指代码来源文件。

> **P0 执行状态（2026-09-02）**：A-1~A-7、A-9~A-13 已实现；test/core 新增 20 用例；全量回归 **376 passed**（基线 356 + 20，`--ignore=test/test_cloud_sync.py`）。
> - ✅ A-1~A-7、A-9、A-12（graph/intent/semantic 调用点切 core）、A-13（新增 test/core/ 4 文件：registry/import_compat/no_reverse_dep/schema_registry）。
> - ✅ A-10 上提为 `core/rdb/mysql_pool.py`，`price_inquiry/db.py` 留壳 re-export；MYSQL_* 并入 Settings（v1.3 A-10 修正已实现，硬编码内网地址移除）。
> - ✅ A-11 落地为 `public_kb/schema_registry.py`（登记两集合 + embedding 版本 + 校验函数）；**启动期强校验接线推迟 P3**（集合生命周期统一到 core 后），避免 P0 扩 ingestion/retrieval 行为面。
> - ⚠️ **A-8 延后**：`services/milvus_store.py` 深度耦合 `public_kb.chunk_ids/contracts`（:20-28），原样迁 core 会制造 core→public_kb 反向依赖；待 chunk 契约上提 core 后再迁。无 agent 直接依赖，不影响 R1 验收。
> - ✂️ **A-3/A-7 范围收紧（忠于现状）**：LLM/Embedding 运行时对象必须保持 LangChain Runnable（router 依赖 with_structured_output/bind_tools、generation 依赖 `|`），故 ABC 只抽象*构造*差异点（`LLMProvider.build()`）；chat/structured_output/stream 门面推迟 P6 工具化。`core/embeddings/base.py` 未建（bge_m3.py 提供 model_id/version 常量供 A-11）。
> - ℹ️ 新增 `test/core/test_import_compat.py` 含双路径 Settings 字段全等断言（A-1 验证）。
> - ⚠️ `test/test_cloud_sync.py` 为预先存在的损坏（import 不存在的 `cloud_sync.*`，unittest 遗留），pytest 收集即中止；本地验证用 `--ignore`。与本次改动无关，待用户处置。

| # | 目标文件 | 动作 | 要点 / 验证 |
|---|---|---|---|
| A-1 | `core/config/settings.py` | 新建（源：`public_kb/config.py` 364 行整体复制） | 保持 `@dataclass Settings` + `os.getenv` + 根 `.env` 加载方式不变，只改包路径；`public_kb/config.py` 改为 `from core.config.settings import Settings` re-export + DeprecationWarning。验证：两条 import 路径构造出的 Settings 字段全等 |
| A-2 | `core/config/profiles/models.yaml` | 新建 | **3 个实际调用点**复刻**现状行为**：router(temperature=0.0)、kb_generation(默认温度)、price_intent(0.0)，provider/model 全部指向当前 deepseek 配置；`general_chat` 节点为纯静态回复不调 LLM（`general_chat.py:43`），其槽位仅在 yaml 中预留、不接线——本 Phase 只把隐式覆盖显式化，不改任何行为 |
| A-3 | `core/llm/base.py` | 新建（~40 行） | `LLMProvider` ABC：`chat()` / `structured_output(schema)` / `stream()`；structured_output 必须进 ABC（router.py:181、intent.py 依赖） |
| A-4 | `core/llm/deepseek.py` | 迁移（源：`public_kb/services/llm.py:19-39`） | `create_llm` 逻辑包进 `DeepSeekProvider(LLMProvider)`；保留模块级 `create_llm(settings, temperature=)` 兼容签名 |
| A-5 | `core/llm/registry.py` | 新建（~30 行） | `register(name, cls)` / `create(slot)`：读 models.yaml 槽位 → provider → 实例 |
| A-6 | `public_kb/services/llm.py` | 改 re-export 壳 | `from core.llm.deepseek import create_llm` + DeprecationWarning |
| A-7 | `core/embeddings/{base,bge_m3}.py` | 同 A-3/A-4 模式（源：`services/embeddings.py`） | `Embedder` ABC 加 `model_id` / `version` 属性（version 供 A-11 校验）；旧路径 re-export |
| A-8 | `core/vectorstore/milvus_client.py` | 迁移（源：`services/milvus_store.py`） | `MilvusStoreManager` 原样移动，不改逻辑；旧路径 re-export |
| A-9 | `core/vectorstore/namespace.py` | 新建 | 集中 collection 名常量：`KB_PUBLIC="public_kb"`、`PRICE_SEMANTIC="mysql_price_semantic"`（现散在 `semantic.py:26` 等）；`semantic.py` 改为 import 常量 |
| A-10 | `core/rdb/mysql_pool.py` | 迁移（源：`price_inquiry/db.py` 连接池部分） | 池逻辑原样上提；`db.py` 保留 `_get_settings` 等并 re-export 池。**配置收口**：`db.py:25-26` 绕过 Settings 直读 `os.getenv("MYSQL_HOST"/"MYSQL_USER")` 且默认值硬编码内网 IP `192.168.10.120` / 用户 `iflytek`，`:35` 直读 `MYSQL_CLEAN_DB`——上提时全部改为从 Settings 读取（settings 中新增对应字段，默认值留空，缺失时报错提示补 `.env`，不再硬编码内网地址） |
| A-11 | `public_kb/schema_registry.py` | 新建 | 登记两集合的 schema 字段/索引参数（IVF_FLAT nlist=128 COSINE）/embedding version；ingestion 与 retriever 启动时 `validate()`，不一致抛错拒绝服务 |
| A-12 | 调用点切换 | 改 3 处 import 来源，不改行为 | ① `agent/graph.py:23-24` → core.config + registry 槽位；② `price_inquiry/intent.py:15` → slot `price_intent`；③ `semantic.py:16-17` → core.config + core.embeddings。**general_chat 无迁移项**：纯静态回复不调 LLM（`general_chat.py:43`），预留槽位仅注册不接线 |
| A-13 | 测试 | 新增 3 个测试文件 | `test/core/test_llm_registry.py`（fake provider 注册/创建、structured_output 委托）；`test/core/test_import_compat.py`（全部旧路径 import 可用）；`test/core/test_no_reverse_dep.py`（扫描 agent/*.py 断言不含 `public_kb.services`，把 grep 守卫固化成测试） |
| A-14 | 验收演示 | 手动 | 改 models.yaml 某槽位 model → 日志确认生效 → 改回；router 快拍（§2 表）指标不降 |

**P0 完成态判据**：`grep -rn "public_kb.services" agent/` 为空（services/ 仅剩壳）；`pytest test/` 全绿；CLI 3 个问题走通。

---

## 附录 B：Phase 1 文件级任务清单

> **P1 执行状态（2026-09-02，离线安全步骤先行）**：Milvus 基线（106 题 live eval）待恢复（本地/远端 19530 均不可达）期间，先做零行为变更步骤。
> - ✅ B-2：`generation/context.py` → `public_kb/rendering/context.py`（chain.py 改引新路径；旧路径 re-export 壳）。
> - ✅ B-3：citations_models/build/validate git mv → `public_kb/guardrails/`（整包搬移，内部相对 import 零改动）；`generation/citations.py` 保留门面改指 guardrails；test_citations_split 与白名单条目随迁更新。
> - ✅ B-1（脚手架）：`public_kb/tools.py`（Chunk/RetrievalResult 三元组契约 + 带锁惰性 retriever 工厂，`_get_retriever` 可注入测试；不接线）+ `test/test_kb_search.py` 离线单测。
> - ✅ B-4（拆包 + 开关）：`agent/nodes/knowledge_qa/` 包——node.py(开关分发) / legacy.py(旧实现含单例) / chain.py(工具编排) / prompts.py(生成模板)；`USE_TOOL_KB`(Settings.enable_kb_tool_path) **默认 false**（比计划默认 true 更稳：先零行为变更，live 验证后再切 true，见偏差记录）；新增 `test/test_knowledge_qa_tool.py` 6 用例；`test_citation_tracing` 旧节点单例打桩改指 legacy；`test_no_reverse_dep` 守卫更新为 B-4 分层（node 入口零 public_kb、chain 仅经 tools/guardrails 门面）。
> - ✅ B-7：`agent/guardrail_hooks.py`（with_guardrails 装饰器，护栏失败附注/可选抛错）+ 5 用例。
> - ✅ **L3 基线替代（106 题 testset_knowledge.jsonl 已丢失无法找回，用户拍板）**：新增 `scripts/run_knowledge_parity_smoke.py`——本地单书(book2 850chunks 已入库本机 Milvus public_kb) 上 legacy vs 工具链路结构对拍，快照 test_report/，作为 B-5 等行为重构的前后回归护栏；实跑 6/6 一致。
> - ✅ B-5：`rag_engine.py` → `engine.py`（git mv + rag_engine.py 留壳）；`PublicKnowledgeRAG.__init__` 构造注入化（llm/embeddings/store_manager 可选）；`query()` 暂仍走 LCEL 链（组合 kb_search 推迟到工具链路完全替换旧路径后）。全量 393 passed，harness 4/4 一致。
> - ✅ **B-11 本地验证完成（部署时再切）**：harness 12/12 一致；CLI 冒烟 legacy 与 `USE_TOOL_KB=true` 工具链路在真实图上均走通（router→knowledge_qa(tool)→kb_search→kb_generation→护栏全过）。**默认保持 false**——因本地仅单书+纯稠密降级（本地集合无稀疏字段，`ENABLE_MILVUS_BM25` 默认关），未到全量混合检索/生产验证；上线时置 `USE_TOOL_KB=true` 即可，一行 env 变更。
> - 🔒 未做：B-6 qa_chain 委托新组合（随 query 改造）、B-8 graph 装配注入（当前工具链路节点自装配惰性单例）。106 题质量基线永久由本地对拍 harness 替代。

| # | 目标文件 | 动作 | 要点 / 验证 |
|---|---|---|---|
| B-1 | `public_kb/tools.py` | 新建 | `RetrievalResult{chunks, citations_meta, rendered_context, recall_trace}`；`kb_search(query, top_k=8)` 内部惰性构建 HybridRetriever（装配逻辑抽自 `generation/chain.py:53-66`），走 retrieve → format_docs/build_sources/build_citations，**不调 LLM**；retriever 实例用**带锁工厂**持有（解 R4 竞态）；`kb_search_as_tool()` 只建不接线（P6 用） |
| B-2 | `public_kb/rendering/context.py` | 迁移（源：`generation/context.py`） | `format_docs` / `build_sources` 原样移动；旧路径 re-export |
| B-3 | `public_kb/guardrails/` | 迁移（源：`generation/citations_models/citations_build/citations_validate`） | `generation/citations.py` 保留门面（`agent/__main__.py:25` 引用 `format_citations`），内部改指 guardrails |
| B-4 | `agent/nodes/knowledge_qa/` 拆包 | 新建 4 文件 | `__init__.py` re-export `node_knowledge_qa`（`nodes/__init__` import 不变）；`prompts.py` ← `generation/prompts.py`；`chain.py` = `generation/chain.py:71-119` 中"检索以后"的生成编排（prompt\|llm\|parser + 校验 + 拒答分支）；`node.py` 重写为 kb_search → prompt → 槽位 `kb_generation` → 护栏 → 降级，**删除旧文件 19-31 行全局单例** |
| B-5 | `public_kb/engine.py` | 改名重构（源：`rag_engine.py`） | 构造注入（settings/embedder/store/llm 均可传）；`init_knowledge_base/ensure_loaded/load_existing/add_pdf/clear_kb` 签名不变；`query()` 内部改为组合 kb_search + 生成链（行为等价）；`rag_engine.py` 留 `from .engine import PublicKnowledgeRAG` |
| B-6 | `public_kb/qa_chain.py` | 保留兼容壳 | `build_qa_chain` 签名不变，内部委托新组合；活一个 Phase 后标记删除 |
| B-7 | `agent/guardrail_hooks.py` | 新建 | `with_guardrails(node_fn, validators)` 装饰器；knowledge_qa 先行接入 |
| B-8 | `agent/graph.py` 装配 | 改 | build_graph 时构建一次 kb 检索器/engine，以闭包工厂注入 knowledge_qa 节点——沿用 `build_router_node(llm)` 既有模式（`router.py:190`），不引入新框架 |
| B-9 | 测试 | 新增 3 个测试文件 | `test/public_kb/test_kb_search.py`（mock retriever：三元组字段齐全、top_k 传递、空结果不触 LLM）；`test/public_kb/test_guardrails_independent.py`（guardrails 独立 import 不依赖 agent）；`test/agent/test_knowledge_qa_node.py`（mock kb_search + mock llm：prompt 组装正确、护栏失败走降级、无全局单例）；另验证 `PublicKnowledgeRAG().query` 返回键不变（answer/sources/citations/citation_validation/retrieval_diagnostics） |
| B-10 | 基线对比 | 手动 | 106 题 citation eval 复跑：引用完整率/校验通过率 ≥ P0 前快照 |
| B-11 | 开关 | `USE_TOOL_KB`（默认 true） | false 时节点走旧 `rag.query()` 路径；旧路径保留到 P2 验收后删除 |
| B-12 | `test/test_public_kb_layout.py` | 同步更新既有布局守卫 | 该文件已有三处会与 P1 改动冲突的守卫，须与 B-4/B-5/B-6 **同一 commit** 更新，否则 pytest 红：① `:147-172` AST 禁 import 守卫——`node_knowledge_qa` 所在文件禁止 import `public_kb.{qa_chain,rag_engine,retrieval,generation,ingestion}`，B-4 新 node.py 走 `public_kb.tools`（不在禁区）即可满足；禁区清单随迁移同步扩展；② `:112-117` 断言 `build_qa_chain` 签名严格等于 `["vector_store","llm","settings","collection","embeddings"]`——B-6 兼容壳必须原样保签名；③ `:67-72` 断言 `PublicKnowledgeRAG.query` 非协程——将来异步化前须先改此断言。另**新增** `public_kb.tools` 与 knowledge_qa 拆包后的布局断言（防新路径回潮） |

**P1 完成态判据**：演示"不改 public_kb、仅改 agent 侧 prompts.py 即改变回答风格"一次；`pytest test/` 全绿；5 个 kb 问题人工过目正常。

---

## 附录 C：Phase 2 文件级任务清单（FastAPI 服务层 + 薄协议）

> **C-0 前置修正（R12 定性）**：编写本附录时逐文件核实，`answer_templates.py`（query_type 自然语言模板，绑定 `ztb_clean.company_info/company_penalty/bid_project` 表）与 `output_templates.py`（`FieldDescriptor` 字段注册表 + `_apply_output_template` 筛选引擎）**仅被 `agent/nodes/price_inquiry/node.py:12-13` 引用**（另有一些测试与文档引用）。它们是 price 域渲染引擎，不是跨节点输出协议。方案二 R12"位置偏低的跨节点协议"定性不准确，正确归属是 **P3 随 price 引擎下沉到 `price_engine/rendering/`**。P2 的 `core/protocol/` 只新建 AgentReply 薄协议（全新文件，非两模板升级）。§1/§3/§4/§5 已同步修正。

| # | 目标文件 | 动作 | 要点 / 验证 |
|---|---|---|---|
| C-1 | `core/protocol/reply.py` | 新建（薄协议，~80 行） | `AgentReply(BaseModel)`：`reply_type: Literal["kb_answer","price_records","chat","doc_qa","fallback"]`、`answer: str`、`citations: list[dict]`、`data: dict \| None`、`trace: {intent, latency_ms, guardrail?}`；`to_reply(business_result: dict, intent: str, latency_ms) -> AgentReply` 纯映射函数。**不改任何节点内部返回结构**——转换只发生在出口层 |
| C-2 | `core/protocol/renderers/{cli,api}.py` | 新建 | cli.py 收编 `agent/__main__.py:86-105` 的 `_render_business_data` 逻辑（citations 块走 `format_citations`，否则 sources/records 计数）；api.py 把 AgentReply → `ChatResponse` dict |
| C-3 | `api/schemas.py` | 新建 | `ChatRequest{question: str (1~2000 字), session_id: str \| None, stream: bool = False}`；`ChatResponse{answer, intent, citations, data, latency_ms}`。**裁剪**方案一附录 9.1 的 `tenant_id`/`token_usage`/`user_id`——当前无对应实现，加了是死字段 |
| C-4 | `api/deps.py` | 新建 | `get_agent()`：threading.Lock 保护的 AgentGraph 进程级懒加载单例；checkpointer 后端读 env `CHECKPOINTER_BACKEND`（默认 memory）。**文档化限制**：uvicorn 须 `--workers 1`（MemorySaver 不跨进程共享；多 worker 需先切 sqlite/postgres 后端） |
| C-5 | `api/server.py` | 新建 | `create_app()` 工厂；`POST /chat`：stream=false → 调 `AgentGraph.invoke` 返回 ChatResponse；stream=true → SSE（`text/event-stream`），复用 `AgentGraph.stream()`（`graph.py:237`）把图事件逐块转发；`GET /health` 返回 agent 就绪标志；CORS 中间件（origin 走 env）；入口 `python -m api.server`（uvicorn，host/port 走 env，默认 127.0.0.1:8000） |
| C-6 | `api/cli.py` + `agent/__main__.py` | 收编 + 薄转发 | argparse/interactive/run_single 逻辑移入 `api/cli.py`，渲染改用 renderers/cli；`agent/__main__.py` 改为 `from api.cli import main; main()`——`python -m agent` 命令行为完全不变 |
| C-7 | `core/observability/metrics.py` | 新建（最小集） | 3 个指标：分支耗时、guardrail 校验失败计数、召回降级命中层；先 log 输出。埋点 2 处：`graph._with_fallback`（`graph.py:51`）和 knowledge_qa 护栏分支。LangSmith 不接线，仅确认 `LANGCHAIN_TRACING_V2` env 钩子可用 |
| C-8 | 各节点 | **不改** | business_result 结构保持原样（避免行为变更）；协议转换只在 C-1 的 to_reply。此决策保证 P2 零行为漂移，无需 L3 基线对比 |
| C-9 | 测试 | 新增 3 文件 + 1 脚本 | `test/api/test_schemas.py`（空/超长 question → 422，默认值）；`test/api/test_chat_endpoint.py`（FastAPI TestClient + mock AgentGraph：/chat 200 且结构合法、session_id 映射为 thread_id、/health 200）；`test/core/test_reply_protocol.py`（5 个分支的 business_result → AgentReply 映射）；`test/api/smoke_concurrent.py`（live 手动：10 并发不同 session 无串扰） |
| C-10 | 验收 | 手动 | `uvicorn` 启动；`/openapi.json` 可生成；前端/curl 验证 SSE 逐块到达；`python -m agent -q "..."` 与 `python -m api.cli` 行为一致 |

> **P2 执行状态（2026-09-02）**：✅ C-1 AgentReply 薄协议 + to_reply（不改节点）；C-2 renderers cli/api（cli 自含通用引用渲染，**不反向依赖 public_kb**——偏差：原拟复用 public_kb.generation.citations.format_citations，违反 core 分层，改自含实现）；C-3 schemas；C-4 deps（进程级单例 + CHECKPOINTER_BACKEND + --workers 1 限制）；C-5 server（invoke/SSE/health/CORS/uvicorn 入口）；C-6 CLI 收编 api/cli + agent/__main__ 薄转发（双入口行为一致）；C-7 最小指标（metrics 3 指标 + _with_fallback 计时/护栏失败两处埋点）；C-9 测试（protocol 8 + api schemas/chat_endpoint 9 + 并发冒烟脚本）；C-10 live 验收通过（/health、/chat、SSE done、openapi、并发 10/10）。C-8 节点零改动保持。**依赖新增**：fastapi/uvicorn/httpx（requirements.txt）。全量 410 passed。

**P2 完成态判据**：`pytest test/` 全绿；并发 smoke 无串扰；CLI 双入口行为一致；前端可对接 `/chat`（含 SSE）。

---

---

> **维护说明**：本方案每完成一个 Phase 更新一次（标记完成状态 + 偏差记录）。若执行中发现与代码事实不符之处，以代码为准修订本文件。后续 Phase（P2~P6）的文件级清单按滚动式细化原则（§0-7）在各 Phase 启动前补充。

---

## 附录 D：Phase 3 文件级任务清单（price_engine 下沉 + 熔断，滚动式展开 2026-09-02）

> **P3 执行前提/风险**：price 链路查询 `ztb_clean`（MySQL，MYSQL_HOST 为远端/本地不可达），**price golden 无法在本机实跑**——故引擎下沉等行为改动须在"有 MySQL 的实库环境"验证，或用离线单测 + 结构对拍兜底。以下任务分「离线可做」「需实库」。

| # | 目标文件 | 动作 | 要点 / 验证 |
|---|---|---|---|
| D-1 | `core/retrieval/{base,pipeline,stages}.py` | 新建（离线安全，先做） | `RecallStage` 协议 + `RecallPipeline(stages, deadline_ms=3000, allow_scan=False)` 熔断状态机：任一阶段超时/deadline 到 → 熔断返回"空+降级原因"，不再裸进 FULL_SCAN；`allow_scan` 默认 False。单测用 fake stage（不触库）验证 deadline/全失败熔断/放行语义 |
| D-2 | `price_engine/__init__.py` | 新建（离线） | 能力包门面（re-export，沿用 price_inquiry 拆包范式）；包 docstring 注明边界 |
| D-3 | `price_engine/{intent,queries,sql_builders,recall,semantic,enum_norm,schema,models}.py` | git mv（源：`price_inquiry/` 同名 8 文件） | 整包搬移后内部相对 import 不变；**仅 `.db` 引用改 `core.rdb.mysql_pool`**（`_get_connection/_release_connection/_CLEAN_DB/_get_settings`）；`agent/nodes/price_inquiry/__init__.py` 改为 re-export price_engine 保 `from agent.nodes.price_inquiry import X` 兼容（L2） |
| D-4 | `price_engine/rendering/` | 迁移（源：`agent/nodes/answer_templates.py` + `output_templates.py`，C-0 归属） | 两模板迁 `price_engine/rendering/`（旧路径 re-export 壳）；node 渲染引用改 price_engine |
| D-5 | `agent/nodes/price_inquiry/node.py` | 薄化 | **保留项（v1.3 清单，不得下沉）**：P0-11 守卫/引导语（:104/:128）、P0-12 纠偏注入（:216/:230）、product_query 能力边界（:50）、LLM60% 超时（:275）+ SQL 线程池超时（:303）、POST_GUARD（:343/:376）；引擎调用改 `from price_engine import ...`（绝对导入） |
| D-6 | `core/retrieval` 接入 `price_engine/recall.py` | 改 | 5 级召回链（recall.py:36 权重表）改造为 RecallStage 接入 RecallPipeline；默认 allow_scan=False（FULL_SCAN 仅显式放行，需实库验证召回不降） |
| D-7 | `agent/registry.py`（预留）+ intent 边界文档 | 新建/文档 | 全局意图枚举单点化属 P4；P3 只给 `price_engine/intent.py` 文档化"槽位抽取/查询改写"边界（sub_route 保留为内部分流）；`组件工作机制.md` 补两级边界 |
| D-8 | `price_engine/tools.py` | 新建 | `price_query()` + `price_query_as_tool()`（P6 多 Agent 复用；本 Phase 不接线） |
| D-9 | 测试 | 新增 | `test/price_engine/test_recall_pipeline.py`（熔断离线）；`test/core/test_price_engine_import.py`（旧 price_inquiry 路径 import 兼容，L2）；golden（sub_route/行数一致，**需实库**，L3） |
| D-10 | 验收 | 手动（需实库） | 企业详情 / 中标历史 / 项目编号 3 类查询走通（L4） |

**P3 完成态判据**：`pytest` 全绿；L2 旧路径兼容；L3/L4 在实库环境验证；PRICE_ENGINE_LEGACY 回滚可用（re-export 天然支持）。

> **P3 执行状态（2026-09-03，本地实库已就绪后推进）**：
> - ✅ D-1（09-02）：core/retrieval 熔断状态机 + 离线单测。
> - ✅ D-2~D-4（09-03）：引擎 8 文件 git mv → price_engine（.db 引用改
>   core.rdb.mysql_pool）+ 渲染两模板 → price_engine/rendering（旧路径镜像壳）；
>   price_engine 门面 + price_inquiry 兼容门面；node.py 改 absolute import。
>   验证：pytest 434 passed；price golden 13 题 before/after **0/91 字段差异**。
> - ✅ D-5：node.py 保留守卫/引导/超时/POST_GUARD，引擎调用改 `from price_engine import`。
> - ✅ D-6（2026-09-03）：5 级链改造为 RecallStage（price_engine/recall_stages.py）接入
>   RecallPipeline ——**实现偏差**：阶段适配放 price_engine 侧（非 core/retrieval/stages.py），
>   因阶段强依赖 SearchIntent/sql_builders，放 core 会制造 core→price_engine 反向依赖；
>   core/stages.py 留占位说明。熔断默认 allow_scan=False（FULL_SCAN 仅
>   PRICE_ALLOW_FULL_SCAN=true 放行），deadline=PRICE_RECALL_DEADLINE_MS 默认 3000ms
>   且逐条 SQL 超时钳制到剩余预算。验证：离线集成测试 8 用例（扫描门控/短路/deadline
>   熔断/FULLTEXT 缺失降级）；全量 447 passed；**live 确认 full_scan:skip(full_scan_disabled)**；
>   price golden before/after **0/104 字段差异**（13 题无一依赖全表扫描，召回不降）。
> - ✅ D-8（2026-09-03）：price_engine/tools.py `price_query()`/`price_query_as_tool()`
>   （引擎直调无 agent 反向依赖，product 边界一致；不接线）+ 离线单测 5 用例。
> - ✅ D-7（2026-09-03）：price_engine/intent.py docstring 补"槽位抽取/查询改写"边界
>   （sub_route 为引擎内部分流，一级意图归 registry，P4 已单点化）。
> - ✅ D-10（2026-09-03）：L4 冒烟通过——企业详情 / 中标历史 / 项目编号三类查询
>   全图 live 走通（[350001]FJGGZY[GK]2024013 → project_detail 1 条，含熔断 trace）。

---

## 附录 E：Phase 5 文件级任务清单（ingestion 三段式重构，滚动式展开 2026-09-03）

> **P5 目标**：解 R7（无显式三段式/无断点续跑/csv 走文本管道）与 R13（9 个 pdf_* 平铺
> transforms/）。终态目录见 §3 框架；关键约束：`contracts.py` / `chunk_ids.py` 的 chunk
> 契约**不动**（稳定边界）；`pipeline.py` 旧行为保留为 `pipeline_legacy.py` + CLI `--legacy`
> 开关（回滚）；csv 双 IR 输出（table 摘要供 SQL 路 + 行块语义供向量路）。
> 全部离线安全，与 P6 无文件交集。每项一个 commit，全绿过门。

| # | 目标文件 | 动作 | 要点 / 验证 |
|---|---|---|---|
| E-1 | `public_kb/ingestion/ir.py` | 新建 | `NormalizedDocument{doc_id, source_format, ir_format: markdown\|table, content, metadata(parser_version/quality_score/...)}`；`compute_source_hash(path)`（文件字节 MD5）+ `simhash(text)`（文档级去重用）。纯 dataclass + 纯函数，无依赖 |
| E-2 | `public_kb/ingestion/router.py` | 新建 | `FormatRouter.route(path) -> NormalizedDocument`：按扩展名（.txt/.md→text、.csv→csv、.pdf→pdf）+ 内容嗅探分发 |
| E-3 | `public_kb/ingestion/ledger.py` | 新建 | `IngestLedger`：文档级解析前去重登记（`ingest_ledger.jsonl`：doc_id → source MD5/SimHash → status）；`should_process()`/`mark_done()`。**对接** `pdf_mineru_router.compute_cache_key`（字节哈希口径一致），避免重复烧 MinerU |
| E-4 | `public_kb/ingestion/normalizers/base.py` | 新建 | `Normalizer ABC: normalize(source) -> NormalizedDocument`；quality_score 约定（默认 1.0，解析失败降级） |
| E-5 | `public_kb/ingestion/normalizers/text.py` | 新建 | 文本直通 → markdown IR（clean 后原样） |
| E-6 | `public_kb/ingestion/normalizers/csv.py` | 新建（源：`sources/csv_loader.py` 逻辑） | **双 IR**：table 摘要（表头+行块，供 price 侧 SQL 消费示例）+ 行块语义（现行 CsvLoader 行为）；reuse `_COLUMN_ALIAS_MAP`/`structure_plain_text` |
| E-7 | `public_kb/ingestion/normalizers/pdf/` | 收敛（源：`transforms/pdf_*.py` ×9） | 9 文件 git mv → `normalizers/pdf/`（内部相对 import 不变）；`transforms/pdf_*.py` 旧路径镜像壳（沿用 price_engine 下沉范式，L2 import 兼容）；`pdf.py` Normalizer 入口（PdfRouter/parser_factory 装配）。**P6 结构收敛：镜像壳已于 2026-09-03 删除**（调用方 engine/handoff/parser_factory/脚本/测试全切 normalizers/pdf 真身），`transforms/` 仅保留共享 cleaner/chunker |
| E-8 | `public_kb/ingestion/pipeline.py` | 重构 | 显式三段：`normalize → clean/dedup/chunk → embed/sink`；`IngestionPipeline` 兼容入口保留；旧逻辑 `pipeline_legacy.py` |
| E-9 | `public_kb/ingestion/sinks/milvus_sink.py` | 改 | **upsert by chunk_uid**（pk 为 auto_id → 先按 chunk_uid 查重/删旧再插，或依赖 M2 跳过；实现时核 `MilvusStoreManager.add_documents` 现状决定） |
| E-10 | `public_kb/ingestion/checkpoint.py` | 新建 | 每 stage 落盘中间产物 + `run_manifest.json`；`--resume`（从断点 stage 续）/ `--rerun-from=B`（解析器升级只重跑 B+C） |
| E-11 | `public_kb/ingestion/quality.py` | 新建 | quality_score 低于阈值 → `review_queue/` 旁路输出，不入库 |
| E-12 | `public_kb/ingestion/cli.py` | 改 | 增 `--resume/--rerun-from/--ledger/--legacy`；`--legacy` 走 `pipeline_legacy` 保旧行为 |
| E-13 | 测试 | 新增 | `test/ingestion/test_ir_models.py`（IR 构造/哈希/SimHash）；`test_router_ledger.py`（分发 + 文档级去重幂等）；`test_normalizers_text_csv.py`（双 IR 输出、price 侧 table 摘要消费示例）；`test_pipeline_stages.py`（三段式 + 幂等：同批文件跑两遍向量数不变）；`test_checkpoint_resume.py`（杀进程模拟 --resume 成功） |

**P5 完成态判据**：`pytest` 全绿；同一批文件两次入库 Milvus 向量数不变；杀进程后
`--resume` 成功；csv table IR 可被 price 侧消费示例；旧 `pipeline_legacy` + `--legacy`
回滚可用。

> **P5 执行状态（2026-09-03，完成）**：✅ E-1~E-3（IR/FormatRouter/IngestLedger）；✅ E-4~E-6
> （Normalizer ABC + text + csv 双 IR）；✅ E-7（9 pdf_* 收敛 normalizers/pdf/ + PdfNormalizer）；
> ✅ E-8（pipeline 三段式 FileIngestionPipeline，旧流 pipeline_legacy）；✅ E-9（MilvusSink
> upsert by chunk_uid 语义固化——add_documents 已实现 chunk_uid 幂等判重，跳过式等价 upsert）；
> ✅ E-10（StageCheckpoint 断点续跑 run_manifest + A 产物复用）；✅ E-11（QualityGate 低质量旁路
> review_queue）；✅ E-12（CLI --files/--ledger/--work-dir/--resume/--rerun-from/--quality-threshold/
> --legacy）；✅ E-13（验收：幂等两次入库向量数不变、杀进程 resume、csv table IR price 侧消费示例）。
> 全量 **497 passed**。P5 完成态判据满足：pytest 全绿；幂等/续跑/table IR 消费均有离线用例；
> 旧 pipeline_legacy + --legacy 回滚可用。

---

## 附录 F：Phase 6 文件级任务清单（Supervisor 多 Agent，滚动式展开 2026-09-03）

> **P6 目标**：Supervisor 形态上线，与单图模式灰度并行（§5 Phase 6）。本附录先展开
> **第一步：子图化**——knowledge_qa / price_inquiry 两能力节点的 entry 换成 compiled
> subgraph，为 supervisor 主图（第二步）提供「agent = 子图」的可调度单元。后续步骤
> （supervisor 三级路由 / doc_agent / AGENT_MODE 灰度 / 记忆）按滚动式细化（§0-7）在各自
> 启动前再展开。
>
> **P6 前置事实（已核实）**：langgraph **1.2.11**（requirements 锁 `<2.0`）完整支持
> subgraph-as-node（父图 `add_node(name, compiled_subgraph)`）+ 子图状态命名空间隔离
> （子图独有通道只在子图作用域内读写，与父图共享的 `messages`/`business_result` 自动合并回
> 父图）+ 节点 `(state, config)` 双参签名（LangGraph 自动注入 config）。**本地实库已就绪**
> （P3 备注），price golden 可实跑对拍。`orchestration/` 尚未建立（本步不建，属第二步）。
>
> **关键设计决策（沿用既有约束）**：
> - **子图内部状态与 AgentState 解耦**：`KnowledgeQaState` / `PriceSubgraphState` 只定义在
>   各自能力包子图内；子图独有键（`qa_retrieval`/`qa_attempts`/`qa_feedback` 等）经 langgraph
>   命名空间隔离，**AgentState 不加任何分支字段**——与 `state.py:5-9` 明示设计原则一致；
>   P4-2 State 分层继续推迟（P6 步骤 5 的 handoff 上下文经 messages 传递，复用 checkpointer）。
> - **NodeSpec.entry 保持 `(state, config=None) -> dict`**：节点入口内部 invoke compiled
>   subgraph 并转发 config（thread_id）；`graph.py` 接线、`_with_fallback` 兜底、registry 零
>   改动——为「新 agent 接入 ≤1 spec + 1 子图目录」确立模板。
> - **守卫/超时/引导不因子图化丢失**：price 的 D-5 保留清单（product 边界 / P0-11 / P0-12 /
>   双超时 / POST_GUARD）全部成为子图内部节点与条件边，一个不落。
> - **重试循环是唯一行为增量**：knowledge 子图引入护栏失败→带反馈重生成（≤1 次，总 ≤2 次），
>   其余分支与现链逐键等价；Settings 开关 `kb_guardrail_retry`（默认 true）可即时关闭。
> - **checkpointer**：子图不编译自带 saver，随父图 thread_id 运行（config 转发）；P6 记忆
>   （步骤 5）复用现有机制。
>
> **执行顺序**：F-1~F-3（knowledge，离线安全）→ F-4（price，需实库对拍）→ F-5（守卫与测试
> 同步）。每项一个 commit，全绿过门。

| # | 目标文件 | 动作 | 要点 / 验证 |
|---|---|---|---|
| F-1 | `agent/nodes/knowledge_qa/subgraph.py` | 新建 | `KnowledgeQaState`（共享 `messages`/`business_result` + 子图独有 `qa_retrieval`/`qa_raw_answer`/`qa_feedback`）+ `build_knowledge_qa_subgraph()` → compiled subgraph（**不带** checkpointer）。内部节点：`retrieve`（调 `kb_search`）/ `generate`（prompt\|llm\|parser，槽位 kb_generation）/ `guardrail`（`CitationValidator`）/ `refuse`（空检索拒答，复用 `_REFUSAL_ANSWER`）；条件边：retrieve 空 → refuse，否则 → generate → guardrail → END。终态节点写 `business_result`+`messages`（与 `node._run_tool_node` 现封装同构）。**本步不加重试**（重试 = F-3）；`chain.py` 保留为对拍基准。首个 commit 可先落一个子图能力探针测试（验证命名空间隔离 + config 转发），降低后续风险。验证：mock kb_search/llm，子图输出与 `chain.answer_knowledge` 在相同 fixtures 上**逐键等价**（answer/sources/citations/citation_validation/retrieval_diagnostics + messages） |
| F-2 | `agent/nodes/knowledge_qa/node.py` + `agent/graph.py` | 改 | `node_knowledge_qa` 升级签名 `(state, config=None)`：工具路径主调 `subgraph.invoke(state, config=config)`，返回子图共享通道终态；legacy 路径不动（`USE_TOOL_KB` 开关语义不变）。`_with_fallback`（`graph.py:48-96`）改 `wrapped(state, config=None)` 透传 config（LangGraph 按签名自动注入双参）。验证：mock fixtures 上经 node 入口行为与改造前逐键一致；`python -m agent` 与 `python -m api.cli` 双入口冒烟一致 |
| F-3 | `agent/nodes/knowledge_qa/subgraph.py` + `core/config/settings.py` | 改 | 重试循环：guardrail 失败 → 把校验失败原因（report 未过项）注入 prompt 作「修改要求」反馈 → `generate` 重跑（总 ≤2 次）；仍失败返回答案 + `citation_validation` 报告（现状语义，不无限重试）。Settings 增 `kb_guardrail_retry: bool = True`。`chain.py` 标注 deprecated（保留一个 Phase，P6 验收后删）。验证：新增测试——护栏失败→重试成功、重试仍失败→返回报告且 attempts=2、空检索拒答不触发重试 |
| F-4 | `agent/nodes/price_inquiry/subgraph.py` | 新建 | price 子图化。`PriceSubgraphState` + 内部节点：`parse_intent`（LLM 意图解析 + enum 归一 + LLM 60% 超时检查 `node.py:280-298`）→ `guard_entity`（product 边界 / P0-11 三层 / P0-12 修正与注入 `node.py:205-275`，多出口条件边 → 共享 `guidance` 终态节点）→ `run_query`（线程池 `_sql_executor` + remaining_timeout `node.py:300-338`，SQL 超时独立终态）→ `post_guard`（bidding/company 后置校验 `node.py:348-407`）→ `render`（output_template 筛选 + answer_templates 渲染 `node.py:409-499`）。**D-5 保留清单全部成为子图内部节点**；`node_price_inquiry` 薄化为子图入口（同 F-2 config 转发）。验证：**price golden 13 题 before/after 0/104 字段差异保持**（本地实库）；企业详情/中标历史/项目编号 L4 冒烟；全量 pytest |
| F-5 | 测试与守卫 | 新增/更新 | 新增 `test/agent/test_knowledge_qa_subgraph.py`（节点序列 / 拒答 / 护栏失败 / 重试上限 / 终态 business_result 结构）；`test/price_engine/test_price_subgraph.py`（守卫出口矩阵：product/裸实体/无实体/project_detail_no_number/POST_GUARD 各出口 business_result 与现节点逐键一致）。更新 `test/test_public_kb_layout.py`（禁区清单覆盖 subgraph.py——走 `public_kb.tools` 天然满足；新增子图布局断言防回潮）、`test_no_reverse_dep.py`（若适用）；`test_knowledge_qa_tool.py` 改指子图路径。运行 `scripts/run_knowledge_parity_smoke.py`（book2 本地对拍 harness）before/after 均一致 |

**P6 Step 1 完成态判据**：`pytest test/` 全绿（497 + 新增）；knowledge 子图在 mock fixtures
与现链逐键等价，重试仅改变护栏失败分支且指标不降；price golden before/after 0 字段差异；
harness 对拍一致；CLI 双入口冒烟一致；`USE_TOOL_KB` / legacy 路径不受影响。
**回滚**：F-1/F-2 单 commit 可 revert（chain.py/node.py 原实现保留一个 Phase）；F-3 关
`kb_guardrail_retry=false` 即时回退；F-4 回滚 = 保留 node.py 单函数实现一个 Phase。

> **P6 执行状态（2026-09-03，Step 1 子图化完成）**：
> - ✅ F-1：`knowledge_qa/subgraph.py`（`KnowledgeQaState` 命名空间隔离 + 四节点
>   retrieve/generate/guardrail/refuse + 空消息/检索未就绪出口）；与 chain.answer_knowledge
>   mock fixtures 逐键等价；子图作父图节点隔离探针。langgraph 1.2.11 能力探针先行验证。
> - ✅ F-2：`node_knowledge_qa(state, config=None)` 主调子图；`graph.py::_with_fallback`
>   支持 config 按签名转发（`_accepts_config`）；CLI 双入口冒烟；test_knowledge_qa_tool
>   同步改打桩子图（`_tool_chain` 移除）。
> - ✅ F-3：护栏失败有界重试（总 ≤2 次，反馈注入 `build_retry_prompt` 修改要求块）；
>   Settings 增 `kb_guardrail_retry`（默认 true）；chain.py 标注 deprecated（保留一个
>   Phase）；重试成功/耗尽/关闭/拒答不重试 4 用例。**实现注**：默认校验配置下 R7 关、
>   R6 开——无【来源N】标注的回答会通过校验（R1~R5 只看 citations 结构），真正触发失败
>   的是幻影来源【来源N】，测试据此构造。
> - ✅ F-4：`price_inquiry/subgraph.py`——501 行单函数拆为 parse_intent→guard_entity→
>   run_query→post_guard→render 五节点 + exit 终态；**D-5 保留清单（P0-11/P0-12/product
>   边界/双超时/POST_GUARD）全部成为子图内部节点**；node.py 薄化为子图入口；包门面改从
>   subgraph re-export。验证：**price golden 13 题 before/after 0 字段差异**（本地实库，
>   test_report/price_golden_after_subgraph.json）；守卫出口矩阵 9 用例。
> - ✅ F-5：subgraph.py 纳入 `test_no_reverse_dep`（public_kb 门面白名单）与
>   `test_public_kb_layout`（禁区清单）；knowledge parity harness **12/12 一致**（本地
>   book2，工具链路已走子图）。
> - 全量 **522 passed**（基线 497 + 25）。**偏差**：`test_sub_route::test_node_price_
>   inquiry_end_to_end` 的鸭子类型消息改为 `HumanMessage`（子图 messages 通道带
>   add_messages reducer 校验 BaseMessage，真实链路本就是 HumanMessage）。
> **P6 Step 1 完成态判据全部满足**；`orchestration/` 与 supervisor 主图属第二步，待启动。

---

## 附录 G：Phase 6 Step 2 文件级任务清单（Supervisor 主图，滚动式展开 2026-09-03）

> **目标**：Supervisor 形态主图（`orchestration/`）——三级路由（规则快路径→语义相似度→
> LLM 分类）+ handoff 协议 `HandoffMeta{from,to,reason,payload}`；跨 agent handoff
> （price 查询需政策解释→转 knowledge）端到端通过。Step 1 的 knowledge_qa / price_inquiry
> compiled subgraph 直接作 supervisor 图的 agent 节点（「agent = 子图」）。
> 方案一 Step 3.3 参考文档已不在仓库，本附录以 §5 Phase 6 为准自行展开。
>
> **关键设计决策（已探针验证 langgraph 1.2.11）**：
> - **`SupervisorState(AgentState)` 继承扩展**（TypedDict 继承作 graph schema 已验证）：
>   agent 子图共享 channels（messages/business_result）自动合并回父图；supervisor 独有
>   `handoff` / `supervisor_route` / `pending_context` 通道父图作用域隔离。**AgentState
>   本身零改动**（state.py 明示设计原则保持）。
> - **handoff 全在 supervisor 层**：agent 子图零改动（保证 single 模式行为逐字节不变，
>   双跑 eval 不降）。`handoff_decider` 节点在 agent 之后决策（price 意图 + 法规解释
>   关键词 → knowledge），不侵入 agent 内部。
> - **handoff 上下文经 messages 传递**（P6 步骤 5 约定）：decider 注入
>   `HumanMessage(sub_question)` 再路由目标 agent；`pending_context` 保存上一 agent 结果，
>   终态由 decider 组合为合并回答（price 结果 + 法规解释）。
> - **三级路由**：L1 规则快路径（问候/项目编号/文档关键词确定性匹配）→ L2 语义相似度
>   （BGE-M3 嵌入 × registry examples 余弦，阈值可配，embedder 注入离线测）→ L3 复用
>   既有 `build_router_node`（registry 驱动 LLM 分类，`router.py:155`）→ 全 miss 兜底
>   fallback。
> - **compiled subgraph 直接 add_node**（已验证）：qa_*/pg_* 内部键命名空间隔离、
>   config(thread_id) 原生传递；agent 节点统一经 `_with_fallback` 包装（对 compiled
>   subgraph 生效，config 按签名转发）。

| # | 目标文件 | 动作 | 要点 / 验证 |
|---|---|---|---|
| G-1 | `orchestration/__init__.py` + `orchestration/handoff.py` | 新建 | `HandoffMeta(BaseModel){from,to,reason,payload}`；`AGENT_NODES` 常量；`build_sub_question(question)`（Step 2 先取原问题，文档化简化） |
| G-2 | `orchestration/supervisor.py` | 新建 | `classify_rules(question)`（L1）；`classify_semantic(question, registry, embedder=None)`（L2，余弦阈值 `SEMANTIC_ROUTE_THRESHOLD`）；`route_question(messages, llm, registry, embedder, llm_router)` 三级级联；`decide_handoff(intent, question, business_result)`（price+法规关键词→knowledge）；`supervisor_node(state, config)` |
| G-3 | `orchestration/main_graph.py` | 新建 | `SupervisorState`；`build_supervisor_graph(llm, checkpointer, registry, knowledge_subgraph, price_subgraph)`：START→supervisor→条件边→agent→handoff_decider→条件边（handoff→目标 agent；pending_context→组合终态；否则 END）。子图/节点可注入（离线测 mock） |
| G-4 | `agent/graph.py` + `agent/__init__.py` | 改 | `AgentGraph.__init__(mode="single"\|"supervisor")` + env `AGENT_MODE`（默认 single）；`build_agent_graph(mode, ...)` 分发；`invoke/stream/get_state` 对外不变 |
| G-5 | 测试与守卫 | 新增/更新 | `test/orchestration/test_supervisor_router.py`（三级级联离线：规则命中/语义命中/LLM兜底/全miss→fallback）；`test/orchestration/test_handoff_e2e.py`（主图注入 fake 子图：price→handoff→knowledge 组合终态、无 handoff→END、handoff 清空防循环、子图内部键不泄漏）；`test_no_reverse_dep`/`test_public_kb_layout` 加 orchestration 分层断言；CLI 双入口冒烟 |

**P6 Step 2 完成态判据**：`pytest` 全绿；三级路由离线级联通过；handoff 端到端
（price→knowledge 组合回答）通过；`AGENT_MODE=supervisor` 走通 CLI；单图行为零改动。
**回滚**：`AGENT_MODE=single`（默认）立即回单图；orchestration/ 纯新增。

> **P6 执行状态（2026-09-03，Step 2 Supervisor 主图 + Step 3 工具化复用完成）**：
> - ✅ G-1~G-4（Step 2 主图）：`orchestration/{handoff,supervisor,main_graph}.py` +
>   `AgentGraph(mode=...)`（env `AGENT_MODE`，默认 single）。三级路由（L1 规则快路径 /
>   L2 语义相似度默认关——embedder 为 API 成本高，`SUPERVISOR_SEMANTIC_ROUTE` 开启 /
>   L3 复用 `build_router_node`）；`HandoffMeta{from,to,reason,payload}`；handoff 决策全
>   在 supervisor 层（agent 子图零认知，单图行为逐字节不变）；handoff 上下文经 messages
>   传递 + pending_context 组合终态。`SupervisorState(AgentState)` 继承扩展（AgentState
>   零改动）；compiled subgraph 直挂父图经 `_as_agent_callable` 适配（**langgraph 1.2.11
>   CompiledStateGraph 不可 callable，仅 .invoke**——已探针 + 回归测试固化）。条件边须
>   用 `{"end": END}` 映射（1.2.11 不支持条件函数直接返回 END 对象）。
> - ✅ **G-6 角色感知修复（guardrail ③）**：POST_GUARD 按意图角色校验——「X中标」
>   （successful_bidder）只验 successful_bidder 列、「X招标」（purchaser）只验 purchaser
>   列；实体存在于另一角色（非盲目召回）→ 空结果回答（bidder_query 空模板「暂未查询到
>   中标信息」），完全无匹配 → 统一引导。**修复「X中标」返回 X 作为采购人的记录**
>   （如福建师范大学纯采购方 21 行被当"中标"返回；数据本身无误——successful_bidder=0）。
>   验证：中标→0 条空答 / 招标→20 条采购项目；golden Q5 基线 17→0 属修复生效（记录原因），
>   Q5「招标/中标」解析存在 LLM 非确定性（修复前 17/20 之间也翻），属问题质量范畴。
> - ✅ **Step 3（工具化复用验证）**：`agent/tool_agent.py` 通用工具驱动 agent
>   （bind_tools + max_iterations 有界循环 + tool_trace）；`agent/nodes/doc_agent.py`
>   挂 `kb_search_as_tool()` + `price_query_as_tool()` + 自有 prompt（models.yaml 增
>   `doc_agent` 槽位，第 4 槽）；registry **enabled=False 预留**（同 echo_demo 先例，
>   不惊动 router golden/意图集）；base 登记 entry，「登记即接入」离线验证。**路由给
>   真实用户 = 置 enabled=True + 重生成 router golden + 重跑意图集**，待数据清理后。
> - 全量 **561 passed**（Step1 522 + Step2 20 + Step3 19 + 守卫更新）。live 验证：
>   supervisor 模式 L3 路由→knowledge 带引用回答、handoff（中标+法律效力→组合回答）
>   端到端通过；CLI 双入口 supervisor/single 均通。
> - ⏸️ **Step 4（灰度双跑 eval）**：机制就绪（`AGENT_MODE` 已通），但按用户决策——
>   数据修复 + 高数据集建在最终验收前做（guardrail ②），届时单图 vs supervisor 双跑
>   的数字才有可发布意义；当前可用现有数据集做 drift 对比（provisional）。

