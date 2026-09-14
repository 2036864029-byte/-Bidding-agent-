import { num, pick, str, strArray } from '@/adapters/helpers';
import type {
  AgentTrace,
  ChatData,
  ChatRawResponse,
  ChatResponse,
  Citation,
  DocQaMode,
  DocQaRetrieval,
  DocQaSource,
  DocQaStatus,
  PriceIntent,
  PriceMeta,
  PriceRecordRow,
  ReplyType,
  TracePlanStep,
  TraceReflection,
  TraceStep,
} from '@/types/chat';

const REPLY_TYPES: readonly ReplyType[] = [
  'kb_answer',
  'price_records',
  'doc_qa',
  'deep_agent',
  'chat',
  'safety',
  'fallback',
];

/** reply_type 归一：未知分支一律回落 fallback（避免把成功作答渲染成空白）。 */
function normalizeReplyType(v: unknown): ReplyType {
  const s = str(v);
  return (REPLY_TYPES as readonly string[]).includes(s) ? (s as ReplyType) : 'fallback';
}

type RawObj = Record<string, unknown>;

const asObj = (v: unknown): RawObj => (v && typeof v === 'object' ? (v as RawObj) : {});
const asArr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** 表行归一：null/undefined 保留 null，标量保留，其余转字符串。 */
function normalizeRow(row: unknown): PriceRecordRow {
  const out: PriceRecordRow = {};
  for (const [k, v] of Object.entries(asObj(row))) {
    if (v === null || v === undefined) out[k] = null;
    else if (typeof v === 'number' || typeof v === 'string') out[k] = v;
    else out[k] = String(v);
  }
  return out;
}

function mapCitation(c: unknown): Citation {
  const o = asObj(c);
  return {
    contextIndex: num(o.context_index),
    chunkId: o.chunk_id === null || o.chunk_id === undefined ? null : num(o.chunk_id),
    chunkUid: str(o.chunk_uid),
    docName: str(o.doc_name),
    chapter: str(o.chapter),
    chunkIndex: num(o.chunk_index),
    text: str(o.text),
    score: num(o.score),
    metadata: asObj(o.metadata),
  };
}

function mapPriceIntent(v: unknown): PriceIntent | null {
  const o = asObj(v);
  if (Object.keys(o).length === 0) return null;
  const subRoute = str(pick(o.sub_route, o.subRoute));
  const queryType = str(pick(o.query_type, o.queryType));
  const exactTokens = strArray(pick(o.exact_tokens, o.exactTokens));
  return {
    ...(subRoute ? { subRoute } : {}),
    ...(queryType ? { queryType } : {}),
    semanticKeywords: strArray(pick(o.semantic_keywords, o.semanticKeywords)),
    ...(exactTokens.length ? { exactTokens } : {}),
  };
}

function mapPriceMeta(v: unknown): PriceMeta | null {
  const o = asObj(v);
  if (Object.keys(o).length === 0) return null;
  return {
    totalHits: num(pick(o.total_hits, o.totalHits)),
    displayedHits: num(pick(o.displayed_hits, o.displayedHits)),
    sqlCount: num(pick(o.sql_count, o.sqlCount)),
    totalSqlTime: num(pick(o.total_sql_time, o.totalSqlTime)),
    nodeElapsed: num(pick(o.node_elapsed, o.nodeElapsed)),
  };
}

function mapDocSource(v: unknown): DocQaSource {
  const o = asObj(v);
  return {
    doc: str(o.doc),
    chapter: str(o.chapter),
    chunkIndex: num(pick(o.chunk_index, o.chunkIndex)),
    contentSnippet: str(pick(o.content_snippet, o.contentSnippet)),
    score: num(o.score),
  };
}

function normalizeDocStatus(v: unknown): DocQaStatus {
  const s = str(v);
  return s === 'no_doc_attached' || s === 'failed' ? s : 'success';
}

function mapDocRetrieval(v: unknown): DocQaRetrieval | null {
  const o = asObj(v);
  if (Object.keys(o).length === 0) return null;
  const mode: DocQaMode = str(o.mode) === 'fulltext' ? 'fulltext' : 'retrieval';
  return {
    mode,
    blocks: num(o.blocks),
    truncated: Boolean(o.truncated),
    notes: strArray(o.notes),
  };
}

function mapTraceStep(v: unknown): TraceStep {
  const o = asObj(v);
  const status = str(o.status);
  return {
    stepId: num(pick(o.step_id, o.stepId)),
    tool: str(o.tool),
    query: str(o.query),
    status: status === 'empty' || status === 'error' ? status : 'ok',
    observationHead: str(pick(o.observation_head, o.observationHead)),
    recordsCount: num(pick(o.records_count, o.recordsCount)),
    latencyMs: o.latency_ms === null || o.latency_ms === undefined ? null : num(o.latency_ms),
    sources: strArray(o.sources),
  };
}

function mapTracePlanStep(v: unknown): TracePlanStep {
  const o = asObj(v);
  return {
    id: num(o.id),
    objective: str(o.objective),
    tool: str(o.tool),
    query: str(o.query),
    dependsOn: (asArr(pick(o.depends_on, o.dependsOn)) as unknown[]).map((x) => num(x)),
  };
}

function mapTraceReflection(v: unknown): TraceReflection | null {
  const o = asObj(v);
  if (Object.keys(o).length === 0) return null;
  const verdict = str(o.verdict);
  const revised = pick(o.revised_answer, o.revisedAnswer);
  return {
    verdict: verdict === 'revised' || verdict === 'gap' ? verdict : 'adequate',
    notes: str(o.notes),
    revisedAnswer: revised === null || revised === undefined ? null : str(revised),
  };
}

function mapTrace(v: unknown): AgentTrace {
  const o = asObj(v);
  return {
    mode: str(o.mode, 'plan_and_solve'),
    rounds: num(o.rounds),
    plan: asArr(o.plan).map(mapTracePlanStep),
    steps: asArr(o.steps).map(mapTraceStep),
    reflection: mapTraceReflection(o.reflection),
  };
}

/** 按 reply_type 构造判异联合（kind ≡ replyType）。 */
function mapData(replyType: ReplyType, data: RawObj): ChatData {
  switch (replyType) {
    case 'kb_answer':
      return {
        kind: 'kb_answer',
        sources: asArr(data.sources).map(asObj),
        citationValidation: Object.keys(asObj(pick(data.citation_validation, data.citationValidation))).length
          ? asObj(pick(data.citation_validation, data.citationValidation))
          : null,
      };
    case 'price_records':
      return {
        kind: 'price_records',
        records: asArr(data.records).map(normalizeRow),
        tables: strArray(data.tables),
        intent: mapPriceIntent(data.intent),
        meta: mapPriceMeta(data.meta),
      };
    case 'doc_qa':
      return {
        kind: 'doc_qa',
        status: normalizeDocStatus(data.status),
        mode: str(data.mode) === 'fulltext' ? 'fulltext' : 'retrieval',
        sources: asArr(data.sources).map(mapDocSource),
        attachedDocs: strArray(pick(data.attached_docs, data.attachedDocs)),
        retrieval: mapDocRetrieval(data.retrieval),
      };
    case 'deep_agent':
      return {
        kind: 'deep_agent',
        trace: mapTrace(data.trace),
        draftAnswer: str(pick(data.draft_answer, data.draftAnswer)),
      };
    case 'safety':
      return {
        kind: 'safety',
        status: Object.keys(asObj(data.status)).length ? asObj(data.status) : null,
      };
    case 'chat':
      return { kind: 'chat' };
    case 'fallback':
    default:
      return { kind: 'fallback' };
  }
}

/**
 * chatAdapter —— /chat 原始响应 → Domain。
 *
 * 宽松点：`data` 是通用容器（不同分支键不同），一律按 reply_type 分支取；
 * 未知 reply_type → fallback；缺字段逐项归零/默认。纯函数，不改入参。
 */
export function chatResponseAdapter(raw?: ChatRawResponse | null): ChatResponse {
  const replyType = normalizeReplyType(raw?.reply_type);
  const data = asObj(raw?.data);

  return {
    answer: str(raw?.answer),
    replyType,
    intent: str(raw?.intent),
    citations: asArr(raw?.citations).map(mapCitation),
    data: mapData(replyType, data),
    latencyMs: num(raw?.latency_ms),
    sessionId: str(raw?.session_id),
    errorCode: raw?.error_code === null || raw?.error_code === undefined ? null : str(raw?.error_code),
  };
}
