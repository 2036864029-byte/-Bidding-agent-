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

import type { ChatRawResponse } from '@/types/chat';
import type { InquiryRawSummary } from '@/types/inquiry';
import type { KanbanRawOverview } from '@/types/kanban';
import type { SupplierRawOverview } from '@/types/supplier';

const BASE = '/api';
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
    let message = `请求失败（HTTP ${res.status}）`;
    try {
      const body = (await res.json()) as { error?: { message?: string }; detail?: unknown };
      if (body?.error?.message) message = body.error.message;
      else if (body?.detail) message = String(body.detail);
    } catch {
      /* 保留默认文案 */
    }
    throw new BackendError(res.status, message);
  }
  return (await res.json()) as T;
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

/** 上传结果（同步/异步端点归一形状）。 */
export interface UploadResult {
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
