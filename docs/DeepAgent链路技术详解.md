# Deep Agent 模块技术详解

> 适用范围：招投标智能助手 · Agent 编排层深度综合模块
> 代码基线：`agent/nodes/deep_agent.py`（2026-09-16 版，含 Plan-and-Execute 有界回灌与 `read_file` 工具）
> 关联文档：《RAG链路技术详解.md》《技术栈全景_前端与后端.md》

---

## 1. 模块定位

### 1.1 它解决什么问题

系统里有三类数据源，彼此异构、无法用一次检索覆盖：

| 数据源 | 形态 | 直接消费者 |
|---|---|---|
| 公共法规知识库 | Milvus 向量库（法规/汇编/问答/实务） | `knowledge_qa` 子图 |
| 结构化业务库 | MySQL `ztb_clean`（企业/处罚/中标/产品） | `price_inquiry` 子图 |
| 用户会话文件 | 上传文档 → `core.doc_index` 会话级索引 | `doc_qa` 节点 |

单节点只能命中其中**一类**。当用户问「这份标书要求的保证金比例符合招标投标法吗」——需要**先读文件取条款、再查法规比对**——单节点必然答不全。

Deep Agent 就是把这三类源统一成**工具**，让 LLM 自己决定查几次、查哪个、怎么拼。

### 1.2 与其他分支的边界

| 分支 | 输入特征 | 检索次数 | 适用 |
|---|---|---|---|
| `knowledge_qa` | 纯法规问题，无文件 | 1 次（含多跳 L0/L1/L2） | 「评标委员会怎么组成」 |
| `price_inquiry` | 结构化事实查询 | 1 次 | 「XX公司有没有不良记录」 |
| `doc_qa` | 带文件，但答案只在文件内 | 1 次（或全文注入） | 「这份合同的工期是多久」 |
| **`deep_agent`** | **带文件 + 需跨源比对** | **1~8 次（有界）** | 「这份标书的资质要求是否过严，依据是什么」 |

### 1.3 关键文件清单

| 文件 | 职责 |
|---|---|
| `agent/nodes/deep_agent.py` | 主实现：四阶段管线 + 节点封装 + 进程级单例 |
| `core/contracts/trace.py` | 轨迹数据契约（`AgentTrace` / `PlanStep` / `StepRecord` / `ReflectionRecord`） |
| `agent/registry.py` | 能力注册（`_seed_deep_agent`，默认 `enabled=False`） |
| `agent/nodes/base.py` | 节点 entry 登记（`deep_agent` → `node_deep_agent`） |
| `orchestration/supervisor.py` | 三级路由 + `agent_gate` 确定性闸门 |
| `orchestration/routing_rules.yaml` | 闸门词表与阈值（改规则不改代码） |
| `public_kb/tools.py` | `kb_search_as_tool`（法规检索工具） |
| `price_engine/tools.py` | `price_query_as_tool`（结构化查询工具） |
| `agent/doc_context.py` | `read_file_as_tool` / `session_file_names`（会话文件工具） |
| `core/streaming/sink.py` | `emit_token` 流式通道 |
| `evals/metrics/agent_trace.py` | 轨迹指标计算（纯确定性、零 LLM） |

---

## 2. 整体架构与链路走向

### 2.1 分层架构

模块分五层，自顶向下为**触发 → 节点 → 管线 → 工具 → 依赖**：

```
┌─ 触发层 ────────────────────────────────────────────────┐
│  supervisor 三级路由  →  agent_gate 确定性闸门            │
│  （带文件 + 跨源关键词 → 升级 deep_agent，否则 doc_qa）    │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─ 节点层 ────────────────────────────────────────────────┐
│  node_deep_agent(state, config)                          │
│  · 取 messages[-1] 为问题                                 │
│  · 追加「本会话已上传文件」清单（只给文件名，不给正文）      │
│  · 外包 _with_fallback + with_output_guardrails          │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─ 管线层：PLAN → EXECUTE(有界轮) → SYNTHESIZE → REFLECT ──┐
│  规划 → 并发执行/补检索 → 流式综合 → 反思修订              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─ 工具层 ────────────────────────────────────────────────┐
│  kb_search_tool   │  price_query   │  read_file          │
│  （法规）          │  （结构化）     │  （会话文件）        │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─ 依赖层 ────────────────────────────────────────────────┐
│  Milvus  │  MySQL  │  core.doc_index  │  DeepSeek 槽位    │
└─────────────────────────────────────────────────────────┘
```

### 2.2 图 1：模块架构与整体链路

![图1 模块架构与整体链路](deep_agent_fig1.svg)

*图 1：Deep Agent 模块架构。红色虚线为触发闸门，蓝色为工具调用，灰色为降级路径。*

---

## 3. 触发链路：什么请求会进入 Deep Agent

### 3.1 三级路由 + 确定性闸门

`orchestration/supervisor.py` 的路由级联（先命中先返回）：

| 层级 | 机制 | 成本 | 命中条件 |
|---|---|---|---|
| L0（强） | 文档上下文 `explicit` | 零 | 客户端显式传了 `file_ids` |
| L1 | 正则规则 | 零 | 问候 / 项目编号 / 文档关键词 |
| L0（弱） | 文档上下文 `session` | 零 | 会话自动附着文件，且 L1 未命中 |
| L2 | 语义相似度（BGE-M3 × examples 余弦，阈值 0.5） | 高（每请求 13+ 次 embed） | **默认关闭** |
| L3 | LLM 分类（temperature=0） | 中 | registry 驱动的候选集 |
| — | `fallback` | — | 全部未命中 |

**Deep Agent 的入口不在 L3 文本判断，而在 L0 之后的确定性闸门** `should_upgrade_to_agent()`：

```python
# orchestration/supervisor.py
def should_upgrade_to_agent(question, *, rules=None, registry=None) -> bool:
    gate = (rules or load_rules_cached()).agent_gate
    if not gate.enabled or not gate.keywords:      return False
    if not is_capability_enabled(spec):            return False   # 未接线不得放行
    if not any(kw in question for kw in gate.keywords): return False
    if gate.require_doc_ref and not gate.doc_ref_pattern.search(question):
        return False
    return True
```

判定链：**会话有文件（前置，由调用方保证）+ 关键词命中 +（可选）问题指向本文档** → 升级；否则落 `doc_qa`。

### 3.2 为什么不用 LLM 判「是不是复杂问题」

代码注释里记了实测结论（`agent/registry.py::_seed_deep_agent`）：

| 判据 | 实测结果 | 结论 |
|---|---|---|
| 跨源（数据+法规） | 撞墙 | 已弃用 |
| 多原子 | 天花板 ~73% | 已弃用（E5 实测） |
| 问题长度 / 子句数 | 漏答 78~87% | **已实测证否，不要用** |
| **是否带文件**（确定性） | 误入 3.8% / 漏答 0~13% / 命中 87% | **采用** |

关键判断：**文本特征无法可靠预测「需不需要多步」，但「用户本轮是否带了文件」是确定的**。所以闸门只留这一个硬信号。

`routing_rules.yaml` 中闸门词表（18 词）与阈值：

```yaml
agent_gate:
  enabled: true
  require_doc_ref: false          # true → 误入 0%，但漏答升到 17~30%
  keywords: [合规, 符合, 依据, 违反, 是否合法, 合法吗, 规定, 法规, 条例,
             法条, 第几条, 法律, 处罚, 不良记录, 失信, 中标记录, 采购记录,
             工商, 企业信息, 过往, 有没有中标]
  doc_ref_pattern: "这份|本标书|本文件|该文件|文件里|文件中|上传的|这几份|这两份|标书里|招标文件里|这份文件"
```

> ⚠️ 词表与阈值是在 **49 条合成题**上调的，属过拟合。真实阈值须用线上采样校准，届时只改 YAML、不动代码。

### 3.3 启用方式

Deep Agent 在 `registry.py` 中声明 `enabled=False`，两条路开启：

1. **环境变量**（推荐，不改代码）：`CAPABILITY_ENABLED=deep_agent`
2. 改 `CapabilitySpec.enabled` 默认值

`is_capability_enabled()` 的优先级：**Settings 覆盖 > spec 声明默认值**。

> 历史坑位（代码注释原文）：`_get_deep_agent_node()` 曾长期不传 `llm_factory`，而 `build_deep_agent_node()` 无 LLM 来源会直接抛 `ValueError`。由于当时 `enabled=False`，节点从未执行、异常被 `_with_fallback` 吞成降级话术，**直到端到端验收才发现**。现版本强制传 `llm_factory=_slot_llm_factory`。

### 3.4 图 2：触发链路决策

![图2 触发链路决策](deep_agent_fig2.svg)

*图 2：从用户提问到 deep_agent 的完整决策路径。*

---

## 4. 四阶段管线

### 4.1 总览

`run_deep_agent()` 是管线主入口，四个阶段串行，**阶段内并发**：

| 阶段 | 函数 | LLM 调用 | 输出 | 失败行为 |
|---|---|---|---|---|
| PLAN | `_plan_question` | 1 次 invoke | `List[PlanStep]` | 降级为单步直查 |
| EXECUTE | `_execute_steps` | 0 | `List[StepRecord]` | 单步 error，不中断 |
| REPLAN | `_replan` | ≤1 次/额外轮 | `List[PlanStep]` | 返回 `[]`（视作已足够） |
| SYNTHESIZE | `_synthesize` | 1 次 stream | `draft: str` | 会抛出（由外层兜底） |
| REFLECT | `_reflect` | 1 次 invoke | `ReflectionRecord` | 采纳草稿 |

### 4.2 PLAN — 规划

**输入**：用户问题（已追加会话文件清单）+ 工具名与描述清单。

```
PLAN_PROMPT 关键约束：
1. 输出 JSON 数组，每项含 objective / tool / query
2. 简单问题 1 步；多源组合拆 2~4 步
3. 工具选择规则：
   - 问上传文件 → read_file（文件名放 args.file_name）
   - 问企业/工商/处罚/中标 → price_query
   - 问法律/法规/条文 → kb_search_tool
   - 多源组合 → 都排进计划
4. 只输出 JSON
```

**解析链**（`_parse_steps`）：

1. `_extract_json` 容错 —— 剥 markdown 围栏、截取首个完整 `[...]` / `{...}`
2. 逐项校验：非 dict 跳过、`tool` 不在候选集跳过、`query` 缺失用原问题兜底
3. `_clean_step_args` 过滤 LLM 传参（见 §5.2）
4. 步数封顶 `max_steps`（默认 4）
5. `start_id` 递增 —— 保证补检索轮的 step_id 全局唯一

**降级**：解析失败 / 输出非法 → 返回单步 `[PlanStep(id=1, objective="直接检索回答", tool=第一个工具, query=问题)]`，**不阻断本轮**。

### 4.3 EXECUTE — 并发执行

```python
def _execute_steps(plan, tool_map, round_id=1):
    with ThreadPoolExecutor(max_workers=min(max(1, len(plan)), 4)) as ex:
        futures = [ex.submit(contextvars.copy_context().run,
                             _execute_step, st, tool_map, round_id)
                   for st in plan]
        records = [f.result() for f in futures]
    return sorted(records, key=lambda r: r.step_id)
```

两个设计点：

- **并发**：步间无数据依赖，工具调用是 I/O 主导。顺序执行 4 步 ≈ 4 倍耗时，并发后 ≈ 1 倍。实测收益见 §8。
- **`contextvars.copy_context().run`**：线程池**不复制**上下文，不显式带入会导致工具内的 span 脱离请求 trace、会话作用域丢失（`read_file` 依赖 contextvar 还原 session）。

**单步执行**（`_execute_step`）的状态机：

| 状态 | 触发条件 | `observation_head` |
|---|---|---|
| `ok` | 正常返回且摘要非空 | 观察摘要 |
| `empty` | 摘要为空串（检索无结果） | `""` |
| `error` | 工具不存在 / 抛异常 | `未知工具: X` / `工具错误: ...` |

`empty` 是**正常状态而非错误**——它是 REPLAN 判断「要不要补检索」的现成信号。

### 4.4 REPLAN — 有界回灌补检索

首轮并发执行后，把观察回灌 LLM 问「还缺什么」：

```python
for round_id in range(1, budget + 1):
    if not batch: break
    rounds_used = round_id
    steps.extend(_execute_steps(batch, tool_map, round_id))
    if round_id >= budget: break
    batch = _replan(question, steps, llm, tools, max_steps)   # 返回 [] = 已足够
```

- **有界**：`budget = max(1, max_exec_rounds)`，`replan_enabled=False` 时强制 1（一行配置回滚到旧行为）
- **轮内仍并发**：回灌只发生在轮与轮之间，保住并行收益
- **异常即终止**：`_replan` 任何失败都返回 `[]`，因为补检索是**增益而非必需**
- **防凑步**：REPLAN_PROMPT 明确写「观察已足够回答 → 输出 `[]`，不要为了凑步数而补检索」
- **防重复**：`start_id = len(executed) + 1`，提示「不要重复已执行过的查询」

`trace.mode` 会据此标记：`plan_execute_reflect` / `plan_execute` / `plan_and_solve_reflect` / `plan_and_solve`。

### 4.5 SYNTHESIZE — 流式综合

```python
for chunk in llm.stream([SystemMessage(content=prompt)]):
    text = getattr(chunk, "content", chunk)
    emit_token(str(text))      # 无 sink 时零开销 no-op
    parts.append(str(text))
```

- **只在综合阶段流式**：PLAN / REFLECT 需要完整输出才能解析，流式无意义
- **`emit_token` 契约**：`core.streaming.sink` 的 contextvar 通道。SSE 端点创建 `StreamSink` 置入 contextvar，图在独立线程运行；非流式路径（`/chat` invoke、CLI、单测）无 sink → 零开销退化为累积
- **为什么不用 LangGraph 原生 messages 流**：实测 `stream_mode="messages"` 不跨编译子图边界，见 `core/streaming/sink.py` 模块文档

### 4.6 REFLECT — 反思修订

**输出协议是标记行而非 JSON**（这是踩坑后的设计）：

```
第 1 行：VERDICT: adequate | revised | gap
第 2 行：NOTES: 一句话说明
仅当 revised：
第 3 行：---ANSWER---
其后：修订后的完整回答
```

> **为什么不用 JSON**：修订回答是长中文散文，塞进 JSON 字符串要靠模型自行转义，实测稳定产出非法 JSON（`Expecting ',' delimiter`）→ 只能降级为「采纳草稿」，也就是**答案根本没被审校**。把长文本移出 JSON，从结构上消除了这一类失败。

三种裁决的落地：

| verdict | 终答 | 备注 |
|---|---|---|
| `adequate` | 草稿 | 默认路径 |
| `revised` | `reflection.revised_answer` | **必须为完整回答，不能只给差异** |
| `gap` | 草稿 | 观察不足，如实说明缺口 |

解析失败（既非标记格式也非 JSON）→ 记 warning + 返回 `adequate`，**采纳草稿**。

### 4.7 图 3：四阶段时序与状态流转

![图3 四阶段时序与状态流转](deep_agent_fig3.svg)

*图 3：四阶段时序。绿色为正常路径，红色虚线为降级路径。*

---

## 5. 核心子模块与机制

### 5.1 工具集

| 工具 | 底层实现 | 默认参数（系统兜底） | 返回形状 | 数据源 |
|---|---|---|---|---|
| `kb_search_tool` | `public_kb.tools.kb_search` | `summary=True, top_k=5` | `{chunks, citations_meta, rendered_context, recall_trace}` | Milvus `public_kb` |
| `price_query` | `price_engine.tools.price_query` | `max_rows=10` | `{records, total_found, queried_tables, sql_count, guard_reason?}` | MySQL `ztb_clean` |
| `read_file` | `agent.doc_context.read_session_file` | `top_k=6` | `{mode, chunks, matched, total_chunks, available_files, files_searched, note?}` | 会话文件索引 |

三者返回统一含 `chunks` / `records` 字段 —— 这是刻意的**观察摘要契约对齐**，使 `_summarize_result` / `_count_records` 不必为不同工具分支。

**工具共同特征：都不调 LLM 做生成**。`kb_search` 只检索+构建三元组，`price_query` 内部有意图解析 LLM（属引擎本身，不是 agent 的规划）。

### 5.2 工具参数白名单（关键约束）

LLM 规划时可以给 `args`，但**只有白名单内的参数会被采纳**：

```python
_LLM_TUNABLE_ARGS = {"read_file": {"file_name"}}   # 唯一放开的参数
```

两道过滤（`_clean_step_args`）：

1. **白名单**：挡掉 LLM 自行改动系统调优旋钮
2. **存在性**：挡掉 LLM 编造的参数名（否则工具抛 `unexpected keyword argument`）

**设计依据（实测数据，代码注释原文）**：

> 实测让 LLM 自行决定 `summary` / `top_k` 会扰动检索行为：引用覆盖率 **0.948 → 0.894**
> （基线 n=3 vs 放开后 n=8，两组区间不重叠；temperature=0，可复现非抖动）。
> 实测分布：16 题里 LLM 有 8 次把 `summary` 改成 False、9 次把 `top_k` 调到 8。

结论：`summary=True` / `top_k=5` 是刻意的 compact 契约，交给 LLM **只增加方差、不增加能力**。唯一例外是 `read_file.file_name` —— 它来自问题文本，代码无从推断。

### 5.3 观察摘要预算（按工具区分）

```python
_OBSERVATION_BUDGETS = {"read_file": (5, 800, 4000)}          # (块数, 每块字符, 总字符)
_DEFAULT_OBSERVATION_BUDGET = (3, 120, OBSERVATION_HEAD_CHARS) # (3, 120, 400)
```

**为什么 `read_file` 的预算是默认的 6 倍多**（代码注释记录的实测教训）：

> 用默认的 120 字/块会把事实截在句中——实测：「设备质保期：自验收合格之日起五年」
> 正好落在第 121 字，agent 检索对了却回答「文件中未提及质保期年限」；
> 另一题 LLM 自己说出「文件表述被截断（显示为「按合…」）」。

而 `kb_search_tool` 的 120 字/块够看主旨（KB 块 700~900 字，且工具自带 summary 契约），维持原值以免无差别抬高 token 成本。

### 5.4 多文件配额算法（`read_file` 不指定文件名时）

```python
per_file_k = max(2, -(-k // len(targets)))   # ceil(k / n)，下限 2
```

**为什么不是全局排序**：不同文档的余弦分**不可比**，全局排序会让某一份文件的高分把其他文件整份挤掉。按 `ceil(k/n)` 给每份配额、各自取自己的 top-k，是**算力公平分配**。

> 历史问题（代码注释）：原先「留空 = 最近上传的那一个」，导致多文件问题必然失败——
> 规划器通常不传名字，连续两次调用拿回**同一份**文件（实测原话：「只返回了《道路工程》
> 的工期条款，未返回《智慧园区》的任何工期信息」）。

### 5.5 轨迹契约 `AgentTrace`

落 `business_result.data.trace`，是评测与可观测的唯一数据源：

| 字段 | 类型 | 说明 |
|---|---|---|
| `plan` | `List[PlanStep]` | 首轮规划步骤（`id / objective / tool / query / args / depends_on`） |
| `steps` | `List[StepRecord]` | 全部执行记录（含补检索轮，`round` 字段区分） |
| `reflection` | `ReflectionRecord?` | `verdict / notes / revised_answer` |
| `rounds` | `int` | 实际执行轮次 |
| `mode` | `str` | `plan_execute_reflect` / `plan_execute` / `plan_and_solve_reflect` / `plan_and_solve` |

`StepRecord` 关键字段：

| 字段 | 说明 | 用途 |
|---|---|---|
| `step_id` | 全局唯一（跨轮递增） | 轨迹排序与归因 |
| `round` | 第几轮（1 = 首轮计划） | 区分计划步与补检索步 |
| `status` | `ok / empty / error` | 补检索触发信号 |
| `args` | **实际调用**参数（不含 question） | 与 `PlanStep.args` 比对 → 哪些走了兜底 |
| `observation_head` | 观察摘要（限长） | 评测归因，不落正文 |
| `records_count` | 结果条数 | 空结果检测 |
| `latency_ms` | 单步耗时 | 性能归因 |
| `sources` | 来源标识 `doc_name｜chapter`（≤8 条） | **引用覆盖率**核算 |

`core/contracts/trace.py` 是纯 Pydantic 模型，**core 层零依赖** —— agent / orchestration / scripts / 前端均可 import。

### 5.6 节点封装与接线

```python
# 进程级惰性单例（threading.Lock 双重检查）
def _get_deep_agent_node():
    global _node
    if _node is None:
        with _lock:
            if _node is None:
                _node = build_deep_agent_node(llm_factory=_slot_llm_factory)
    return _node
```

**接线全链路（registry 数据驱动，router.py / graph.py 零改动）**：

```
CapabilitySpec(id="deep_agent", node="deep_agent", enabled=False)
   ↓ agent/registry.py::is_capability_enabled  （Settings 覆盖 > 声明默认值）
   ↓ agent/nodes/base.py::_node_entries        （name → entry 映射）
   ↓ agent/graph.py                            （动态 add_node + 条件边）
```

节点外包两层保护（`agent/graph.py`）：

```python
entry = with_output_guardrails(_with_fallback(ns.entry))
```

- `_with_fallback`：任何未捕获异常 → 友好降级话术 + 分支耗时指标
- `with_output_guardrails`：形状校验，`deep_agent` 的 `data_required=["trace"]`

**LLM 槽位**：`create_llm_for_slot("deep_agent", Settings())`，`models.yaml` 中 `provider: deepseek, temperature: 0.0`（规划/综合/反思都需确定性）。

### 5.7 图 4：单步执行决策链

![图4 单步执行决策链](deep_agent_fig4.svg)

*图 4：从 PlanStep 到 StepRecord 的完整决策链。*

---

## 6. 算法与机制汇总

| # | 机制 | 实现位置 | 算法 / 规则 | 设计依据 |
|---|---|---|---|---|
| A1 | Plan-and-Solve | `_plan_question` | LLM 输出 JSON 步骤数组 → 容错解析 → 步数封顶 4 → 失败降级单步 | 复杂问题拆解，简单问题零额外成本 |
| A2 | Plan-and-Execute 有界回灌 | `run_deep_agent` 循环 | 首轮并发执行 → 观察回灌 → 至多 2 轮 | 补检索增益，但必须设上界控成本 |
| A3 | 轮内并发 | `_execute_steps` | `ThreadPoolExecutor(max_workers=min(n, 4))` + `contextvars.copy_context()` | 步间无依赖、I/O 主导；不复制上下文会丢 trace 与会话 |
| A4 | 工具参数白名单 | `_clean_step_args` | 白名单 ∩ schema 存在性，双过滤 | 实测放开 `summary`/`top_k` 使引用覆盖率 0.948→0.894 |
| A5 | 观察摘要预算 | `_summarize_result` | 按工具取 `(块数, 每块字符, 总字符)`，超总长截断加 `…` | `read_file` 需原文（120 字曾截掉关键事实） |
| A6 | 多文件配额 | `read_session_file` | `per_file_k = max(2, ceil(k/n))` | 跨文档余弦分不可比，全局排序会挤掉整份文件 |
| A7 | 文件名模糊匹配 | `_pick_record` | 精确 → 忽略大小写 → 去扩展名 → 唯一子串；**多义即报错** | 静默挑一个会让 agent 读错文件却不自知 |
| A8 | 反思标记行协议 | `_parse_reflection` | `VERDICT:` / `NOTES:` 正则 + `---ANSWER---` 分隔；兼容 JSON 兜底 | JSON 转义长中文散文实测必失败 |
| A9 | 确定性闸门 | `should_upgrade_to_agent` | 关键词命中 ×（可选 `doc_ref_pattern`）× 能力已启用 | 文本复杂度判据实测天花板 73%，「带文件」是唯一可靠信号 |
| A10 | 引用核算 | `_extract_sources` | 取 `chunks` 的 `doc_name｜chapter`（≤8 条），只留标识不留正文 | 度量「答案是否引用了**实际检索到**的来源」 |

### 6.1 降级矩阵

| 环节 | 失败情形 | 行为 | 用户可感知 |
|---|---|---|---|
| PLAN | JSON 解析失败 | 单步直查 | 否（少了几步检索） |
| PLAN | 工具名非法 | 该步跳过 | 否 |
| EXECUTE | 工具不存在 | `status=error`，继续 | 否（可能答不全） |
| EXECUTE | 工具抛异常 | `status=error`，继续 | 否 |
| EXECUTE | 检索无结果 | `status=empty` | 否（触发 REPLAN） |
| REPLAN | LLM / 解析失败 | 返回 `[]`，按已足够处理 | 否 |
| SYNTHESIZE | LLM 失败 | 抛出 → `_with_fallback` 降级话术 | **是** |
| REFLECT | 解析失败 | 采纳草稿 | 否（未经审校） |
| 节点整体 | 未捕获异常 | `_with_fallback` 降级话术 | **是** |
| 输出形状 | 缺 `data.trace` | 输出护栏降级 | **是** |

设计原则：**除综合阶段外，任何环节的失败都不应让用户看到错误**——宁可答得少一点，也不要报错。

---

## 7. 配置与开关

| 配置项 | 默认值 | 位置 | 说明 |
|---|---|---|---|
| `DEEP_AGENT_MAX_EXEC_ROUNDS` | `2` | `core/config/settings.py` | 执行轮次上限；1 = 退回一次性计划 |
| `DEEP_AGENT_REPLAN_ENABLED` | `true` | 同上 | `false` = 不补检索（等价 rounds=1） |
| `CAPABILITY_ENABLED` | `""` | 同上 | 填 `deep_agent` 即启用 |
| `CAPABILITY_DISABLED` | `""` | 同上 | 填 `deep_agent` 即停用 |
| `SUPERVISOR_SEMANTIC_ROUTE` | `false` | `orchestration/supervisor.py` | L2 语义路由（非 deep_agent 专用） |
| `agent_gate.enabled` | `true` | `routing_rules.yaml` | 闸门总开关 |
| `agent_gate.require_doc_ref` | `false` | 同上 | 收紧方向（误入 0% / 漏答 17~30%） |
| `doc_index_top_k` | `6` | settings | `read_file` 默认返回块数 |
| `DEFAULT_MAX_STEPS` | `4` | `deep_agent.py` | 单轮规划 / 补检索步数上限 |
| `OBSERVATION_HEAD_CHARS` | `400` | 同上 | 默认观察摘要总字符上限 |
| `_MAX_SOURCES` | `8` | 同上 | 单步最多记录的来源标识数 |

**模型槽位**：`deep_agent` → `provider: deepseek`，`temperature: 0.0`。
**一次完整调用最坏 LLM 次数**：PLAN 1 + REPLAN 1 + SYNTHESIZE 1 + REFLECT 1 = **4 次**（不含工具内部的意图解析）。

---

## 8. 评测数据

### 8.1 评测口径

`evals/metrics/agent_trace.py` —— **纯确定性、零 LLM**，直接消费 `data.trace`：

| 指标 | 定义 |
|---|---|
| 规划步数 / 多步率 | `len(plan)` 及其 ≥2 的占比 |
| 工具使用频次 | 各工具被调用次数 |
| 步状态分布 | `ok / empty / error` 计数 |
| 反思结论分布 | `adequate / revised / gap` |
| 重复调用样本数 | 同 `(tool, query)` 出现 ≥2 次 |
| 端到端耗时均值 | 全流程墙钟时间 |
| 工具选择 P/R/F1 | 对照 `expected_tools`（弱标注，规则派生） |
| 有序覆盖 / 严格等序 | 对照 `expected_sequence`（`eval/agent_golden_set.json` 人工金标） |

### 8.2 32 题轨迹评测（2026-09-16）

| 指标 | 20:30:44 | 20:59:32 | 21:09:47 |
|---|---:|---:|---:|
| 样本数 | 32 | 32 | 32 |
| 规划步数均值 | 1.8438 | 1.8438 | **1.9062** |
| 多步率（≥2 步） | 65.6% | 68.8% | **68.8%** |
| 步数分布 | {1:11, 2:16, 3:4, 4:1} | {1:10, 2:18, 3:3, 4:1} | {1:10, 2:16, 3:5, 4:1} |
| `kb_search_tool` | 38 | 38 | **45** |
| `price_query` | 20 | 20 | **24** |
| `read_file` | 1 | 1 | 1 |
| `ok` / `empty` | 52 / 7 | 52 / 7 | **60 / 10** |
| 反思 `adequate` / `revised` | 18 / 14 | 19 / 13 | 19 / 13 |
| 重复调用样本 | 0 | 0 | 0 |
| 失败步样本 | 0 | 0 | 0 |
| 端到端耗时均值 | 8481.9 ms | 8161.7 ms | **9404.8 ms** |
| 工具选择 P / R / F1 | .9844 / 1.0 / .9896 | 同左 | 同左 |
| 有序覆盖率 | 100% | 100% | 100% |
| 严格等序率 | **62.5%** | 59.4% | **50.0%** |

**读法**：

- **工具选择近乎满分**：precision 0.9844 / recall 1.0 / F1 0.9896，有序覆盖 100% —— 说明**规划器选工具这件事已经不是瓶颈**（归功于 PLAN_PROMPT 里把工具描述喂给规划器 + 白名单约束）。
- **零重复调用、零失败步**：补检索的「不要重复已执行过的查询」约束生效。
- **21:09:47 那轮的增量**：工具调用 58 → 69（+11 次）、耗时 8482 → 9405 ms（+10.9%）、`empty` 步 7 → 10。这与「补检索轮带来额外工具调用」的行为一致，**但产物未记录当次 `DEEP_AGENT_REPLAN_ENABLED` 取值，归属需以运行命令核对**。
- **严格等序率下降（62.5% → 50.0%）**：补检索引入额外步骤后，轨迹工具序列不再与最小期望序列逐项相等。注意**有序覆盖仍是 100%** —— agent 多做了一步，但没有做错顺序。

### 8.3 图 5：三次运行对比

![图5 三次运行对比](deep_agent_fig5.svg)

*图 5：三次 32 题评测对比。左轴为次数，右轴为耗时（ms）。*

> ⚠️ 口径提示：三次运行间隔约 40 分钟，环境噪声（API 延迟、LLM 采样）未隔离，差异不全部归于代码改动。

### 8.4 单次成本

| 项 | 数值 | 来源 |
|---|---|---|
| 端到端耗时均值（32 题） | 8162 ~ 9405 ms | 上述评测 |
| 冒烟样本（2 题） | 31218.5 ms | `test_report/agent_trace_golden_smoke` |
| 早期版本（2 题） | 10171 ms | `test_report/deep_agent_report.md`（2026-09-09） |

**成本定位**：deep_agent 单题约 8~9 s，是 `knowledge_qa`（p95 10 s，但含全链路）量级相当、比单节点路径重的分支。这也是它**默认关闭、只服务带文件跨源场景**的量化依据。

---

## 9. 已知限制与后续

| # | 限制 | 现状 | 后续方向 |
|---|---|---|---|
| 1 | 闸门词表过拟合 | 在 49 条合成题上调 | 用 `scripts/sample_traces_for_golden.py` 采真实提问校准，只改 YAML |
| 2 | 默认关闭 | `enabled=False` | 需线上采样 + router golden 重生成后启用 |
| 3 | `read_file` 只用 1 次（32 题） | 评测集多是无文件题 | 需带文件题集专项评测 |
| 4 | 补检索收益未量化 | 三次运行配置口径未记录 | 固定 A/B（`replan_enabled` on/off，同题集同环境） |
| 5 | `read_file` 依赖现场建索引 | 索引缺失时首问慢（60 块 ≈ 3.7 s） | 上传时预热已覆盖主路径，可监控现场建索引率 |
| 6 | 反思 `gap` 未观测 | 32 题全为 adequate/revised | 需构造「观察确实不足」的题集 |
| 7 | 无 `sources` 与最终答案的自动比对 | 只落标识，未算覆盖率 | 接入 `evals/metrics` 自动核算 |

---

## 附录 A：一次完整调用示例（轨迹片段）

```json
{
  "mode": "plan_execute_reflect",
  "rounds": 2,
  "plan": [
    {"id": 1, "objective": "读取标书中的投标保证金条款",
     "tool": "read_file", "query": "投标保证金比例", "args": {"file_name": "招标文件.pdf"}},
    {"id": 2, "objective": "查招标投标法对保证金的规定",
     "tool": "kb_search_tool", "query": "投标保证金 比例 上限 规定", "args": {}}
  ],
  "steps": [
    {"step_id": 1, "tool": "read_file", "round": 1, "status": "ok",
     "args": {"file_name": "招标文件.pdf", "top_k": 6}, "records_count": 5,
     "observation_head": "[招标文件.pdf｜第三章 投标人须知] 投标保证金不得超过招标项目估算价的百分之二……",
     "latency_ms": 812, "sources": ["招标文件.pdf｜第三章 投标人须知"]},
    {"step_id": 2, "tool": "kb_search_tool", "round": 1, "status": "ok",
     "args": {"summary": true, "top_k": 5}, "records_count": 5,
     "observation_head": "[招标投标法实施条例｜第二十六条] 招标人在招标文件中要求投标人提交投标保证金的……",
     "latency_ms": 1243, "sources": ["招标投标法实施条例｜第二十六条"]},
    {"step_id": 3, "tool": "price_query", "round": 2, "status": "ok",
     "args": {"max_rows": 10}, "records_count": 3, "latency_ms": 640,
     "observation_head": "[{\"company_name\": \"...\"}]"}
  ],
  "reflection": {"verdict": "revised", "notes": "补充了条例第二十六条的具体上限",
                 "revised_answer": "……"}
}
```

## 附录 B：相关脚本

| 命令 | 用途 |
|---|---|
| `python scripts/run_deep_agent_eval.py --limit 2` | 冒烟（前 2 题） |
| `python scripts/run_deep_agent_eval.py --questions "q1" "q2"` | 自定义问题集 |
| `python scripts/run_agent_trace_eval.py` | 32 题轨迹评测（产出 `test_report/agent_trace_*`） |
| `python scripts/diag_gate_criteria.py` | 闸门判据诊断（49 条带文件金标集） |
| `python scripts/sample_traces_for_golden.py` | 线上采样（校准真实阈值） |
