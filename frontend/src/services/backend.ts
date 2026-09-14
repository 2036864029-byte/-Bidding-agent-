/**
 * backend —— 真实后端 HTTP 客户端（契约见 docs/前端接口清单.md）。
 *
 * Phase 2 范围：`/health`、`/chat`、`/files/*`（非流式）已可调用；
 * `/chat/stream`（SSE）在 Phase 4 接入。
 *
 * 三模块只读端点（热力/占比/走势、供应商、询价单）后端**尚未提供**（设计文档 §5）——
 * 对应函数抛明确 `BackendError`，即数据源开关的「空实现」侧；Phase 3 起 UI 走空态，
 * 后端就绪后在此填真实请求即可（UI 与 adapters 零改动）。
 *
 * ⚠️ 开发期经 Vite 代理 `/api → 127.0.0.1:8000`；业务错误走 HTTP 200 + error_code
 * （由 adapters 透传，不在此抛出），系统/过载错误才走 4xx/5xx。
 */

import { fetchEventSource } from '@microsoft/fetch-event-source';
import type { ChatRawResponse } from '@/types/chat';
import type { InquiryRawSummary } from '@/types/inquiry';
import type { KanbanRawOverview } from '@/types/kanban';
import type { SupplierRawOverview } from '@/types/supplier';

/**
 * 后端基址。开发期走 Vite 代理的 `/api`（见 vite.config.ts）；
 * 生产同域反代同理；可用 `VITE_API_BASE` 直连（联调 / 前后端分域部署）。
 */
const BASE = import.meta.env?.VITE_API_BASE ?? '/api';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** 后端/网络异常（含状态码；status=0 表示网络层失败）。 */
export class BackendError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'BackendError';
    this.status = status;
  }
}

/** 统一请求：非 2xx 解析错误信封 → BackendError。 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new BackendError(0, '网络请求失败，请确认后端服务已启动');
  }
  if (!res.ok) {
    throw new BackendError(res.status, await describeError(res));
  }
  return (await res.json()) as T;
}

/**
 * 从错误响应里榨出一句人话。
 *
 * 三种形状都要认：统一错误信封 `{error:{code,message}}`、FastAPI 的字符串 `detail`、
 * 以及 FastAPI **校验错误**的 `detail: [{loc,msg},…]` —— 最后这种若直接 `String()`
 * 会变成 `[object Object]` 甩给用户。
 */
async function describeError(res: Response): Promise<string> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return `请求失败（HTTP ${res.status}）`;
  }
  const b = body as {
    error?: { code?: string; message?: string };
    detail?: unknown;
  };

  if (b?.error?.message) return b.error.message;

  if (typeof b?.detail === 'string' && b.detail) return b.detail;

  if (Array.isArray(b?.detail) && b.detail.length) {
    const first = b.detail[0] as { loc?: unknown[]; msg?: string };
    const field = Array.isArray(first?.loc) ? first.loc[first.loc.length - 1] : undefined;
    const msg = first?.msg ?? '参数校验未通过';
    return field ? `参数有误（${String(field)}）：${msg}` : String(msg);
  }

  if (res.status === 413) return '文件超过大小上限（20 MB）';
  if (res.status === 400) return '文件类型不支持';
  if (res.status === 429) return '请求过于频繁，请稍后重试';
  if (res.status === 503) return '当前请求较多，请稍后重试';
  return `请求失败（HTTP ${res.status}）`;
}

/** 探活。 */
export async function health(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/health`);
    return res.ok;
  } catch {
    return false;
  }
}

export interface ChatOptions {
  sessionId?: string;
  fileIds?: string[];
  signal?: AbortSignal;
}

/**
 * POST /chat（一次性返回）。
 * 超时建议 ≥ 60s——agent 路径 9~15s 属正常（接口清单 §7），别按 30s 掐。
 */
export async function sendChat(question: string, opts: ChatOptions = {}): Promise<ChatRawResponse> {
  const body: Record<string, unknown> = { question, stream: false };
  if (opts.sessionId) body.session_id = opts.sessionId;
  if (opts.fileIds?.length) body.file_ids = opts.fileIds;
  return request<ChatRawResponse>('/chat', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
    signal: opts.signal,
  });
}

/** 流式事件回调（原始形状；Domain 归一在 services/api.ts 做）。 */
export interface RawStreamHandlers {
  /** 首个事件：会话 id，前端应立刻保存 */
  onSession?: (sessionId: string) => void;
  /** 增量 token，逐帧追加展示 */
  onToken?: (delta: string) => void;
  /** 终态权威结果（answer / citations / data） */
  onResult?: (raw: ChatRawResponse) => void;
}

/**
 * POST /chat/stream（SSE）。
 *
 * 事件序：session → token* → result → done（见接口清单 §4）。
 * ⚠️ agent 路径**只在 SYNTHESIZE 阶段产 token**：前 5~10s 界面无输出属正常，
 * 调用方需自备占位文案。整条链路超时放宽到 ≥120s。
 * ⚠️ `fetchEventSource` 默认出错会**自动重连**；这里在 onerror 里抛出让其停止重试。
 */
export async function streamChat(
  question: string,
  opts: ChatOptions,
  handlers: RawStreamHandlers
): Promise<void> {
  const body: Record<string, unknown> = { question, stream: true };
  if (opts.sessionId) body.session_id = opts.sessionId;
  if (opts.fileIds?.length) body.file_ids = opts.fileIds;

  await fetchEventSource(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
    signal: opts.signal,
    openWhenHidden: true, // 标签页切走也不暂停流（agent 路径本来就慢）

    async onopen(res) {
      if (res.ok) return;
      // 过载/限流走 HTTP 错误码（业务错误才走 200 + error_code）
      throw new BackendError(res.status, await describeError(res));
    },

    onmessage(ev) {
      if (!ev.event || ev.event === 'done') return;
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(ev.data) as Record<string, unknown>;
      } catch {
        return; // 半截帧直接忽略，不因解析失败中断整条流
      }
      switch (ev.event) {
        case 'session':
          handlers.onSession?.(String(payload.session_id ?? ''));
          break;
        case 'token':
          handlers.onToken?.(String(payload.delta ?? ''));
          break;
        case 'result':
          handlers.onResult?.(payload as ChatRawResponse);
          break;
        case 'error': {
          const envelope = payload as { error?: { message?: string } };
          throw new BackendError(0, envelope?.error?.message ?? '流式请求出错');
        }
        default:
          break;
      }
    },

    onclose() {
      /* 正常收尾：done 之后服务端关闭连接 */
    },

    // 不重试：把错误抛给调用方处理（重试会让用户看到重复的答案片段）
    onerror(err) {
      throw err;
    },
  });
}

/** 上传结果（同步/异步端点归一形状）。 */export interface UploadResult {
  fileId: string;
  sessionId: string;
  status?: string;
  sourceName?: string;
  preview?: string;
  chunkCount?: number;
}

/**
 * POST /files/upload[/async]。
 * ⚠️ `session_id` 是 **query** 参数（不是表单字段）；校验（20MB / 扩展名白名单）建议前端先做。
 */
export async function uploadFile(
  file: File,
  sessionId?: string,
  opts: { async?: boolean; signal?: AbortSignal } = {}
): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);
  const path = opts.async === false ? '/files/upload' : '/files/upload/async';
  const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const raw = await request<Record<string, unknown>>(`${path}${qs}`, {
    method: 'POST',
    body: form,
    signal: opts.signal,
  });
  return {
    fileId: String(raw.file_id ?? ''),
    sessionId: String(raw.session_id ?? sessionId ?? ''),
    status: raw.status === undefined ? undefined : String(raw.status),
    sourceName: raw.source_name === undefined ? undefined : String(raw.source_name),
    preview: raw.preview === undefined ? undefined : String(raw.preview),
    chunkCount: raw.chunk_count === undefined ? undefined : Number(raw.chunk_count),
  };
}

/** GET /files/{fileId}/status（轮询端点，已豁免限流）。 */
export async function getFileStatus(fileId: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/files/${encodeURIComponent(fileId)}/status`);
}

/* ── 三模块只读端点：后端未提供 → 空实现 ───────────────────────────── */

const NOT_IMPLEMENTED = '该模块后端只读端点尚未接入（设计文档 §5）；当前请使用 Mock 数据源。';

export async function getKanbanOverview(): Promise<KanbanRawOverview> {
  throw new BackendError(501, NOT_IMPLEMENTED);
}

export async function getInquirySummary(): Promise<InquiryRawSummary> {
  throw new BackendError(501, NOT_IMPLEMENTED);
}

export async function getSuppliers(): Promise<SupplierRawOverview> {
  throw new BackendError(501, NOT_IMPLEMENTED);
}
