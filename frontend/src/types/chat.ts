/**
 * 对话主模块领域契约（Domain Model）—— 对齐 docs/前端接口清单.md §3。
 *
 * `data` 是按 reply_type 分发的判异联合（`kind` 恒等于顶层 `replyType`），
 * 后端原始字段（snake_case / 通用 data 容器）经 adapters/chatAdapter 归一化。
 *
 * ⚠️ CoT 折叠面板只对 `deep_agent` 有真数据（data.trace）——其余分支无 trace。
 */

export type ReplyType =
  | 'kb_answer'
  | 'price_records'
  | 'doc_qa'
  | 'deep_agent'
  | 'chat'
  | 'safety'
  | 'fallback';

/** kb_answer 引用条目 —— 回答文本中【来源N】对应的原文。 */
export interface Citation {
  /** 对应回答中的来源序号，从 1 起 */
  contextIndex: number;
  /** Milvus 主键（可 null） */
  chunkId: number | null;
  /** 内容稳定标识 */
  chunkUid: string;
  docName: string;
  chapter: string;
  chunkIndex: number;
  text: string;
  /** 相关度（0~1） */
  score: number;
  metadata: Record<string, unknown>;
}

/** price_records 表行（列集由子路由决定：bid_project / company_info …）。 */
export type PriceRecordRow = Record<string, string | number | null | undefined>;

/** price_records 的意图元信息。 */
export interface PriceIntent {
  subRoute?: string;
  queryType?: string;
  semanticKeywords: string[];
  exactTokens?: string[];
}

/** price_records 的执行元信息（排查"为什么这次慢"用）。 */
export interface PriceMeta {
  totalHits: number;
  displayedHits: number;
  sqlCount: number;
  totalSqlTime: number;
  nodeElapsed: number;
}

/** doc_qa 来源段落（长文档检索模式）。 */
export interface DocQaSource {
  doc: string;
  chapter: string;
  chunkIndex: number;
  contentSnippet: string;
  /** 余弦相关度（0~1） */
  score: number;
}

export type DocQaStatus = 'success' | 'no_doc_attached' | 'failed';
export type DocQaMode = 'retrieval' | 'fulltext';

export interface DocQaRetrieval {
  mode: DocQaMode;
  blocks: number;
  truncated: boolean;
  /** 后端显式提示（低相关度兜底 / 截断）——建议直接展示给用户 */
  notes: string[];
}

/** deep_agent 规划步骤。 */
export interface TracePlanStep {
  id: number;
  objective: string;
  tool: string;
  query: string;
  dependsOn: number[];
}

/** deep_agent 单步执行记录。 */
export interface TraceStep {
  stepId: number;
  tool: string;
  query: string;
  status: 'ok' | 'empty' | 'error';
  /** 观察摘要（截断） */
  observationHead: string;
  recordsCount: number;
  latencyMs: number | null;
  /** 本步检索来源标识（形如 "doc_name｜chapter"，引用核算用） */
  sources: string[];
}

/** deep_agent 反思结论。 */
export interface TraceReflection {
  verdict: 'adequate' | 'revised' | 'gap';
  notes: string;
  revisedAnswer: string | null;
}

/** deep_agent 多步检索轨迹（唯一有真 trace 的分支）。 */
export interface AgentTrace {
  mode: string;
  rounds: number;
  plan: TracePlanStep[];
  steps: TraceStep[];
  reflection: TraceReflection | null;
}

/** data 判异联合 —— kind 恒等于顶层 replyType。 */
export type ChatData =
  | {
      kind: 'kb_answer';
      sources: Record<string, unknown>[];
      citationValidation: Record<string, unknown> | null;
    }
  | {
      kind: 'price_records';
      records: PriceRecordRow[];
      tables: string[];
      intent: PriceIntent | null;
      meta: PriceMeta | null;
    }
  | {
      kind: 'doc_qa';
      status: DocQaStatus;
      mode: DocQaMode;
      sources: DocQaSource[];
      attachedDocs: string[];
      retrieval: DocQaRetrieval | null;
    }
  | { kind: 'deep_agent'; trace: AgentTrace; draftAnswer: string }
  | { kind: 'safety'; status: Record<string, unknown> | null }
  | { kind: 'chat' }
  | { kind: 'fallback' };

/** /chat 完整响应（Domain 形态）。 */
export interface ChatResponse {
  answer: string;
  replyType: ReplyType;
  intent: string;
  citations: Citation[];
  data: ChatData;
  latencyMs: number;
  sessionId: string;
  /** 业务错误码（HTTP 200 时非空，见接口清单 §3.3） */
  errorCode: string | null;
}

/** 会话内一条消息（Phase 4 将把 agent 消息挂上 replyType / citations / data）。 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'agent';
  content: string;
  replyType?: ReplyType;
  intent?: string;
  citations?: Citation[];
  data?: ChatData | null;
  errorCode?: string | null;
  /** true = 等待中（agent 慢路径 9~15s 占位） */
  pending?: boolean;
  /** true = 这条回答失败了（可重试；主动停止不算失败） */
  failed?: boolean;
  /** 触发这条回答的**原始提问**（联动跳转时把上下文带过去用） */
  sourceQuestion?: string;
}

/** 后端原始响应（snake_case，未归一化）—— 适配器输入 / backend 客户端返回。 */
export interface ChatRawResponse {
  answer?: string;
  reply_type?: string;
  intent?: string;
  citations?: Array<Record<string, unknown>>;
  data?: Record<string, unknown> | null;
  latency_ms?: number | null;
  session_id?: string;
  error_code?: string | null;
}
