# 自适应文档清洗智能体（AdaptiveDocCleaner）PRD 与技术方案

- 版本：v0.1（草案，待评审）
- 日期：2026-09-20
- 状态：设计阶段
- 关联代码：`public_kb/`（清洗/分块/质量链路为可复用资产）

---

## 0. TL;DR

**一句话**：一个"喂领域种子 → LLM 自动生成清洗规则与抽取 Schema → 可插拔引擎分级执行 → 确定性验证闭环修正 → 输出 JSONL + 审计报告"的文档清洗智能体。

**MVP 形态**：CLI + Python SDK + FastAPI HTTP 服务，内置 3 个领域 demo（招投标/政采、合同、财报）。

**核心判断**（先讲清楚为什么做、为什么这么做）：

1. 通用解析层已是红海（MinerU / Docling / Marker / Unstructured / TextIn / LlamaParse 均成熟），**不自研解析模型**，做可插拔接入。
2. 纯垂直清洗市场太窄（只做招投标 ≈ 一个 pipeline，不是产品）。
3. 差异化 = **成本感知分级路由**（80%~90% 文档走免费轻引擎）+ **自适应规则生成**（领域种子 → 声明式配置）+ **确定性验证闭环**（解决 LLM 生成规则不可信）+ **全链路审计**。
4. 方向已被 2026 年行业验证：agentic document processing 是当前趋势（AWS IDP AutoOpt 做"评分→诊断→改配置→重评"闭环、LlamaParse 转向 agentic parsing），本项目是把这个方向落到"中文政采优先 + 成本路由 + 可自部署"的空位。

---

## 1. 背景与问题

### 1.1 问题

- RAG/Agent 系统的质量上限由数据接入决定（garbage in, garbage out）。
- 现有工具三难：
  - **开源重**：MinerU 需 GPU / 权重加载，封装较重；
  - **商用贵**：TextIn、LlamaParse 等按量付费，规模化成本高；
  - **通用不适配**：通用工具无法处理垂直文档的特殊结构（招投标条文编号、合同条款、财报表格）。
- 垂直清洗产品市场窄：只做招投标/政采 ≈ 一个 pipeline，不是一个可复制的产品。

### 1.2 机会

- 用 LLM 把"垂直适配"变成**可自动化、可交互**的过程：领域种子 → 规则/Schema 自动生成 → 用户反馈持续修正。
- 用**分级路由**把成本打下来：简单文档走免费引擎，只有复杂文档才付费。
- 用**验证闭环**解决 LLM 生成配置的可靠性问题——这是产品化的前提。

---

## 2. 产品定位

- 产品名（暂定）：**AdaptiveDocCleaner**（自适应文档清洗智能体）
- 定位语：面向 RAG/Agent 开发者的自适应文档清洗智能体——给领域种子，自动生成清洗规则与抽取 Schema，可插拔引擎分级执行，验证闭环修正，输出 JSONL + 审计。
- 目标用户：
  - RAG/Agent 开发者（主目标）；
  - 企业数据团队（知识库 / 合规文档入库）；
  - 文字密集型行业：招投标/政采、合同、财报、公文（垂直 demo 打单）。
- 产品形态：
  - MVP：CLI + Python SDK + FastAPI HTTP 服务；
  - P1：Web UI、批量任务队列；
  - P2：飞书 Agent 形态、SaaS。
- 非目标（明确不做）：
  - 不自研解析/OCR 模型（接入现成引擎）；
  - 不做向量库/检索（对接 Milvus 及 LlamaIndex/LangChain 即可）；
  - 不做对话式聊天 UI。

---

## 3. 核心概念（术语表）

| 术语 | 定义 |
|---|---|
| 领域种子 DomainSeed | 用户给出的最小先验：1–3 份示例文档 + 目标字段清单 + 约束描述（均可选） |
| 文档画像 DocProfile | 对文档的自动探测结果：格式、文本层有无、页数、表格密度、扫描置信度、领域类型、结构特征 |
| 分级路由 TieredRouter | 按 DocProfile 复杂度分档，决定使用哪一档引擎（免费轻 / 付费重） |
| 清洗配置 CleaningConfig | 声明式 DSL（YAML），描述清洗规则 + 结构规则 + 抽取引用 + 分块策略。**配置非代码** |
| 抽取 Schema ExtractionSchema | JSON Schema，描述目标结构化输出字段 |
| 验证器 Validator | 确定性检查 + 加权评分 + 诊断报告，输出 pass / review / fail 三档 |
| 反馈用例 FeedbackCase | 用户修正后的（输入, 期望输出）对，入库作为后续生成的 few-shot 示例与回归用例 |
| 审计记录 AuditRecord | 全链路留痕：引擎版本、规则集 hash、验证结果、耗时 |

---

## 4. 核心流程：自适应闭环

```
① 输入(文件 + 领域种子)
   → ② 画像(轻量探测 → DocProfile)
   → ③ 路由(DocProfile → EnginePlan)
   → ④ 生成(LLM：CleaningConfig + ExtractionSchema，Schema 校验后落地)
   → ⑤ 执行(清洗 → 抽取 → 分块)
   → ⑥ 验证(确定性检查 → ValidationReport)
   → ⑦ 判定：
        pass    → ⑧ 输出(JSONL + 审计报告)
        review/fail → 自动诊断 → 回 ④ 重生成(有界 N=3 轮)
                      仍不达标 → 人工 review_queue
   → ⑨ 人工修正 → 存 FeedbackCase → 注入下次生成
```

要点：
- **配置生成 + 确定性验证**是双保险：LLM 负责"生成候选"，验证器负责"裁决"；
- 诊断信息结构化回传生成器，形成"生成-执行-验证-修正"闭环；
- 反馈用例库让系统随使用变好（自适应）。

---

## 5. 功能需求

### 5.1 P0（MVP）

| 编号 | 需求 | 验收要点 |
|---|---|---|
| FR-1 | 文档画像 | 输出 DocProfile：格式 / 页数 / 文本层 / 扫描置信度 / 表格密度 / 领域类型 |
| FR-2 | 分级路由 | 4 档（A/B/C/D）路由矩阵；目标 ≥80% 文档落 A/B/C（免费引擎） |
| FR-3 | 引擎适配层 | `EngineAdapter` 统一接口；内置 ≥4 个适配器（PyMuPDF、MarkItDown、Docling-CPU、PaddleOCR-VL，可选 DeepSeek-OCR / MinerU-API / TextIn） |
| FR-4 | 规则生成 | 从 DomainSeed 生成 CleaningConfig + ExtractionSchema；输出前做 YAML/JSON Schema 校验；失败自动重试 ≤2 次 |
| FR-5 | 声明式执行 | 规则引擎执行 清洗→抽取→分块；复用现有 clean_markdown / chunk_cleaned_markdown |
| FR-6 | 验证器 | ≥8 个确定性检查 + 加权评分 + 结构化诊断（见 §9） |
| FR-7 | 反馈闭环 | 人工修正 → FeedbackCase 存储 → 作为示例注入下一次生成 |
| FR-8 | 输出 | JSONL（chunk + metadata + extraction + audit）+ Markdown 审计报告 |
| FR-9 | 交付形态 | CLI（`adc clean --seed … --input … --out …`）、Python API、FastAPI 服务 |

### 5.2 P1

- FR-10 评分模型：LLM-as-judge 或小模型对抽取质量打分，替代部分确定性检查；
- FR-11 批量异步：任务队列 + worker 池 + 断点续跑（复用 checkpoint / ledger 思路）；
- FR-12 Web UI：上传、画像预览、规则编辑、结果比对；
- FR-13 规则回归：FeedbackCase 库自动回归，规则变更防劣化。

### 5.3 P2

- FR-14 领域种子库 / 插件市场（社区共享种子与规则集）；
- FR-15 多租户 SaaS + 按量计费；
- FR-16 与 RAG 框架集成：LlamaIndex / LangChain Loader、Milvus 直写。

---

## 6. MVP 范围与验收

### 6.1 范围

- 输入格式：PDF（文本层 / 扫描）、DOCX、Markdown（P1 扩展 XLSX / PPTX / HTML）。
- 领域：3 个内置种子打样——招投标/政采、合同、财报。
- 明确不做：Web UI、评分模型、分布式调度、多租户。

### 6.2 验收标准（demo 集：每领域 10 份，共 30 份）

| 指标 | 目标 | 口径 |
|---|---|---|
| 结构完整性通过率 | ≥95% | Validator C1–C5 全 pass 的文档占比 |
| 内容保真 | ≥90% | 清洗后文本量 / 表格保留率（C1 / C2） |
| 抽取字段 F1 | ≥0.80 | 10 份预标注文档上的字段级 F1 |
| 免费引擎覆盖率 | ≥80% | 路由到 A/B/C 档（免费）的文档占比 |
| 单文档闭环耗时 | ≤60s | 平均（免费档，不含付费引擎等待） |

---

## 7. 系统架构与模块划分

```
接入层       CLI / Python SDK / HTTP API
                  │
                  ▼
┌───────────────────────────────────────────────────────────┐
│ ① profiler    文档画像        ⑤ validator   验证器        │
│ ② router      分级路由        ⑥ feedback    反馈用例库    │
│ ③ generator   规则/Schema 生成 ⑦ output      产物导出     │
│ ④ executor    规则引擎执行     ⑧ audit       全链路留痕    │
└───────────────────────────────────────────────────────────┘
                  │
                  ▼
产出层       JSONL（chunk+metadata+extraction+audit）+ 审计报告(Markdown)
```

| 模块 | 职责 | 关键接口 | 现有资产复用 |
|---|---|---|---|
| adapters/ | 引擎池：统一 parse 入口、缓存、故障切换 | `parse(path, tier) → ParsedDoc` | MinerUApiParser（HTTP+缓存+版本回退）、parser_factory |
| profiler/ | 文档画像 | `analyze(ParsedDoc) → DocProfile` | pdf_page_profile、pdf_complex_range、pdf_fast_text |
| router/ | 复杂度分档 | `route(DocProfile) → EnginePlan` | pdf_router、pdf_mineru_router（已有三档路由） |
| generator/ | LLM 生成配置 | `generate(DomainSeed, examples, diagnosis) → (CleaningConfig, ExtractionSchema)` | 新增（复用 router 的 structured output 经验） |
| executor/ | 声明式执行 | `run(config, ParsedDoc) → CleanResult` | clean_markdown、chunk_cleaned_markdown、TypedChunker、chunk_uid |
| validator/ | 验证与诊断 | `validate(CleanResult) → ValidationReport` | QualityGate（threshold/partition/review 落盘）、A4 repair_structure 报告 |
| feedback/ | 用例库 | `add_case / inject_examples / run_regression` | 新增（SQLite） |
| output/ | 产物导出 | `to_jsonl / to_audit_markdown` | dump_documents_jsonl、handoff 三段产物约定 |
| audit/ | 全链路留痕 | `record(step, meta)` | parser_version 持久化、ledger 思路 |

---

## 8. 核心数据结构

### 8.1 DomainSeed（用户输入）

```yaml
# domain_seed_ztb.yaml
domain: ztb                # 领域标识
name: 招投标公告清洗种子
examples:                  # 1~3 份示例文档（可选，路径或内容）
  - samples/ztb_announce_1.pdf
fields:                    # 目标抽取字段清单（可选）
  - 项目编号
  - 项目名称
  - 招标人
  - 预算金额
  - 投标截止时间
  - 资质要求
constraints:               # 约束描述（可选）
  - "保留'第X章/第X条'等条文标题，不得当作页眉删除"
```

### 8.2 CleaningConfig（LLM 生成的声明式配置，YAML）

```yaml
domain: ztb
schema_ref: ztb_announcement_v1
parser_tier: auto
cleaning:
  remove_repeating_headers: true
  keep_legal_headings: true
  legal_heading_pattern: "第[一二三四五六七八九十百千0-9]+[章节条款项部分编]"
  remove_page_numbers: true
  remove_noise_short_lines: true      # 仅无汉字符号行
  latex_escape: true
  dedup_paragraphs: true
structure:
  heading_levels: ["#", "##", "###"]
  mark_front_matter: true
extraction:
  fields:
    - name: project_no
      type: string
    - name: budget
      type: number
      unit: 元
      rule: "金额字段，允许'约/预算/最高限价'前缀"
chunking:
  strategy: heading_aware
  max_chars: 2000
  overlap_chars: 100
```

### 8.3 ExtractionSchema（JSON Schema，示例：招投标公告）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ztb_announcement_v1",
  "type": "object",
  "properties": {
    "doc_type": {"type": "string", "enum": ["招标公告", "中标公示", "更正公告", "资格预审"]},
    "project_name": {"type": "string"},
    "project_no": {"type": "string"},
    "tenderer": {"type": "string"},
    "budget": {"type": "number", "description": "预算金额（元）"},
    "bid_deadline": {"type": "string", "format": "date"},
    "qualification_requirements": {"type": "array", "items": {"type": "string"}},
    "contact": {"type": "object", "properties": {"name": {"type": "string"}, "phone": {"type": "string"}}}
  },
  "required": ["doc_type", "project_name", "project_no", "bid_deadline"]
}
```

### 8.4 输出 JSONL（每行一个 chunk）

```json
{
  "chunk_uid": "9f2c…",
  "doc_id": "ztb-2026-0001",
  "domain": "ztb",
  "index": 0,
  "content": "第一章 总则…",
  "metadata": {"heading": "第一章 总则", "page": 1, "chapter": "总则"},
  "extraction": {"project_no": "ZFCG2026001", "budget": 3200000, "doc_type": "招标公告"},
  "audit": {
    "parser": "paddleocr-vl@1.6",
    "ruleset": "ztb_v1#3f2a9c",
    "schema": "ztb_announcement_v1",
    "validator": {"score": 0.96, "verdict": "pass", "checks": {"C1": "pass", "C6": "pass"}}
  }
}
```

### 8.5 审计报告（Markdown，随产物输出）

```markdown
# 清洗审计：ztb-2026-0001.pdf
- 引擎：paddleocr-vl@1.6（A 档）  耗时 8.2s
- 规则集：ztb_v1#3f2a9c（LLM 生成，schema 校验通过）
- 验证：0.96 / pass（C1-C8 明细见附）
- 抽取：12 字段，填充率 10/12
```

---

## 9. 验证器设计（重点）

### 9.1 设计原则

- **确定性优先**：MVP 全部使用确定性检查（可复算、可解释、无模型抖动），P1 再引入 LLM 评分。
- **可诊断**：每个检查输出"通过/失败 + 失败原因定位"（哪条规则、哪个环节），供生成器定向修正。
- **有界闭环**：生成-验证最多 N=3 轮，之后进人工 review_queue（复用 QualityGate 的 review 落盘机制）。

### 9.2 检查清单（C1–C8）

| ID | 层次 | 检查 | 判定口径 | 失败动作 |
|---|---|---|---|---|
| C1 | 内容保真 | 文本量恢复率 | `len(clean_text) / len(engine_text) ≥ 0.80` | 诊断：疑似过度删减 |
| C2 | 内容保真 | 表格完整性 | 引擎标注表数一致；每表行列数偏差 ≤10% | 诊断：表格损坏 |
| C3 | 结构 | 章节结构保留 | 标题节点数偏差 ≤15%；条文标题未被删 | 诊断：页眉误删 / 结构丢失 |
| C4 | 内容保真 | 内容连续性 | 随机抽 3 个窗口，simhash 与原文一致 | 诊断：整段丢失 |
| C5 | 结构 | 无空/异常结果 | 清洗后非空；无异常重复段落（同段 >3 次） | 诊断：规则副作用 |
| C6 | 抽取 | Schema 校验 | required 字段非空率 + 类型/枚举校验（jsonschema） | 诊断：抽取字段缺失 |
| C7 | 编码 | 非法字符率 | 乱码/`�` 占比 < 0.1% | 诊断：编码问题 |
| C8 | 分块 | 分块质量 | 长度 200–4000；无截断句子（结尾为完整句/标题）；chunk_uid 合法 | 诊断：分块参数问题 |

### 9.3 评分与判定

```
score = Σ wᵢ·checkᵢ     w = {C1: 0.15, C2: 0.15, C3: 0.10, C4: 0.15, C5: 0.10, C6: 0.20, C7: 0.05, C8: 0.10}
verdict：score ≥ 0.90 → pass；0.70–0.90 → review；< 0.70 → fail
```

- pass → 输出产物；
- review → 自动诊断 + 重生成（若轮次 < N）；仍不达标 → 人工 review_queue；
- fail → 自动诊断 + 重生成；超限 → 人工 review_queue。

### 9.4 诊断-修正闭环（关键机制）

失败时，validator 产出**结构化诊断**（不是人话描述）：

```json
{
  "check": "C1",
  "severity": "fail",
  "probable_rule": "remove_noise_short_lines",
  "evidence": {"engine_text_len": 12000, "clean_text_len": 8200, "ratio": 0.68}
}
```

生成器把（原配置, 诊断）一并注入提示词，重生成配置。**这一机制让"LLM 生成规则"变得可控，是整个产品的技术护城河所在。**

### 9.5 反馈用例库（回归）

```json
{
  "id": "case_001",
  "domain": "ztb",
  "input": "samples/ztb_announce_2.pdf",
  "expect": {"C1_ratio": 0.88, "C6_filled": ["project_no", "budget"]},
  "created_at": "2026-09-20",
  "source": "user_correction"
}
```

规则变更后自动跑全库回归，防止"修 A 坏 B"。

---

## 10. 分级路由设计

### 10.1 档位与引擎矩阵

| 档位 | 特征 | 推荐引擎 | 单页成本参考 |
|---|---|---|---|
| A 电子文本 | 有文本层、单栏/简单版式 | PyMuPDF / MarkItDown | ≈0 |
| B 常规版式 | 双栏、页眉页脚、多级标题 | Docling-CPU / PaddleOCR-VL | ≈0（本地） |
| C 扫描件 | 无文本层、图片占比高 | PaddleOCR-VL / DeepSeek-OCR | ≈0（本地） |
| D 复杂 | 密集表格/公式/印章/低质扫描 | MinerU 服务 / TextIn / LlamaParse | 付费/GPU |

### 10.2 路由判定依据（profiler 特征链）

文本层存在性 → 文本密度 → 图片占比 → 表格密度 → 公式/印章特征 → 档位

### 10.3 成本模型

目标：**≥80% 文档落 A/B/C（免费），≤20% 落 D（付费）**。MVP 阶段只做路由与计量，不承诺具体价格（付费引擎按厂商报价）。

---

## 11. 可插拔引擎层设计

```python
class EngineAdapter(Protocol):
    name: str
    tier: str            # "A" | "B" | "C" | "D"
    def parse(self, path: Path, *, page_range: str | None = None) -> ParsedDoc: ...
```

- `ParsedDoc`：`{format, pages, text, tables, images, meta, engine, version}`
- 缓存：复用现有 `md5(source_bytes) | page_range | version` 缓存键设计；
- 故障切换：primary → fallback（如 D 档付费引擎不可达 → 降级 C 档本地引擎 + 标记 review）；
- 引擎清单（MVP 内置）：PyMuPDF、MarkItDown、Docling-CPU、PaddleOCR-VL、DeepSeek-OCR（可选）、MinerU-API（现有封装直接复用）、TextIn（可选，付费）。

---

## 12. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 与现有代码栈一致 |
| Agent 编排 | LangGraph（或自研状态机） | 项目已有 LangGraph 经验；8 步闭环适合图编排 |
| 配置/Schema | Pydantic v2 + jsonschema | 声明式校验，类型安全 |
| LLM | DeepSeek（现有）+ 可选 Claude | 成本优先 |
| 存储 | SQLite（反馈用例/审计）；JSONL（产物） | MVP 够用，可迁移 |
| 复用 | public_kb 的 clean_markdown / chunk_cleaned_markdown / chunk_uid / QualityGate / ledger / checkpoint / parser 缓存 | 避免重复造轮子 |

---

## 13. 里程碑

| 里程碑 | 内容 | 预估 |
|---|---|---|
| M1 最小闭环 | 骨架 + CLI + 引擎适配层（A/B/C 档）+ 规则生成（暂不接验证器） | W1–2 |
| M2 验证闭环 | Validator C1–C8 + 诊断修正闭环 + 审计输出 + 3 领域 demo | W3–4 |
| M3 产品化 | FastAPI + 批量队列 + 反馈用例回归 + 验收跑分（§6.2） | W5–6 |
| M4 扩展 | Web UI / 评分模型 / 领域种子库 | 后续 |

---

## 14. 风险与应对

| 风险 | 应对 |
|---|---|
| R1 LLM 生成规则不可控 | 声明式 DSL + Schema 校验 + 有界重试 + 验证关卡兜底；**生成配置而非代码**（避免执行风险） |
| R2 无标注数据，质量信号弱 | 确定性检查（MVP）+ 人工反馈用例库 + 诊断闭环；P1 引入 LLM 评分 |
| R3 "通用平台"卖不动 | 开源 + API 双形态；每垂直领域（招投标/合同/财报）做 demo 打单 |
| R4 大厂入局（AWS/Azure/LlamaParse） | 差异化：中文政采优先、可自部署、成本路由、审计溯源 |
| R5 规则变更劣化 | 回归用例库自动跑；配置版本化 + hash |
| R6 免费轻引擎中文质量不足 | 路由参数按中文文档调优；C/D 档兜底付费引擎 |

---

## 15. 附录

### A. 现有代码复用清单

| 现有资产 | 位置 | 复用方式 |
|---|---|---|
| 复杂度/档位 PDF 路由 | `public_kb/ingestion/normalizers/pdf/*router.py` | 泛化为 router 模块 |
| MinerU HTTP 适配（缓存/版本回退） | `public_kb/services/mineru_api_parser.py` | adapters/ 的参考实现 |
| 清洗链路（TextCleaner + A3 结构归一 + A4 修复闭环） | `public_kb/ingestion/handoff.py::clean_markdown` | executor/ 核心 |
| 分块（Semantic/TypedChunker + chunk_uid） | `handoff.py::chunk_cleaned_markdown`；`core/contracts` | executor/ 核心 |
| JSONL 产物契约 | `handoff.py::dump/load_documents_jsonl` | output/ 直接复用 |
| 质量关卡 | `public_kb/ingestion/quality.py::QualityGate` | validator/ 的雏形（threshold/partition/review 落盘） |
| 幂等/断点 | `pipeline.py`（ledger / checkpoint） | P1 批量队列复用 |
| 解析缓存键设计 | `mineru_api_parser.py::_cache_key` | adapters/ 统一缓存 |

### B. 求职项目包装建议

- 定位语：面向 RAG/Agent 的自适应文档清洗智能体（生成-执行-验证-修正闭环 + 分级路由降本）；
- 简历亮点：Agent 闭环设计、可插拔引擎架构、确定性验证体系、结构化输出、成本优化；
- 演示脚本：同一系统喂招投标/合同/财报三类文档 → 自动生成规则 → 验证闭环 → JSONL + 审计；
- 技术叙事：声明式 DSL（配置非代码）+ 结构化诊断回传 + 回归用例库，是区别于"调 prompt"式 Agent 项目的关键。

### C. 参考来源

- 开源解析工具对比：MinerU / Docling / Marker / Unstructured 的输出格式与生态（2026）；
- 轻量免费引擎：PaddleOCR-VL（0.9B，CPU 可跑）、DeepSeek-OCR（1.3B，MIT）、Docling（商品硬件）；
- Agentic document processing 趋势：AWS IDP AutoOpt（评分→诊断→改配置→重评闭环）、LlamaParse agentic parsing。
