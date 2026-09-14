/**
 * api —— Mock ↔ API 的唯一开关（设计文档 §3 / §5.1）。
 *
 * 页面组件只 import 本文件（取数据）与 `@/types`（取类型）——**禁止直接碰后端字段**。
 * 未来真数据就绪：翻 `dataConfig` 对应模块为 `'api'`，并在 adapters/ 填字段映射即可。
 *
 * ```
 * UI ──▶ api.xxx() ──▶ [开关] ──▶ adapters/ + mock/*（mock 态）
 *                            └──▶ adapters/ + backend.ts（api 态）
 * ```
 */

import { chatResponseAdapter } from '@/adapters/chatAdapter';
import { inquiryAdapter } from '@/adapters/inquiryAdapter';
import { kanbanAdapter } from '@/adapters/kanbanAdapter';
import { supplierAdapter } from '@/adapters/supplierAdapter';
import { chunkText } from '@/lib/chat';
import { mockChatFixtures } from '@/mock/mockChat';import { mockInquiryRaw } from '@/mock/mockInquiry';
import { mockKanbanRaw } from '@/mock/mockKanban';
import { mockSupplierRaw } from '@/mock/mockSupplier';
import * as backend from '@/services/backend';
import type { ChatRawResponse, ChatResponse, ReplyType } from '@/types/chat';
import type { InquirySummary } from '@/types/inquiry';
import type { KanbanOverview } from '@/types/kanban';
import type { SupplierOverview } from '@/types/supplier';

export type DataMode = 'mock' | 'api';

export interface DataSourceConfig {
  kanban: DataMode;
  inquiry: DataMode;
  supplier: DataMode;
  chat: DataMode;
}

/**
 * 数据源开关（唯一真源）。
 * 三模块默认 Mock（真实数据/后端就绪后翻 `'api'`）；
 * chat 默认 Mock，可用 `VITE_CHAT_MODE=api` 环境变量直连真实后端，
 * 也可在对话浮窗头部**运行时切换**（方便对着真后端验证）。
 */
export const dataConfig: DataSourceConfig = {
  kanban: 'mock',
  inquiry: 'mock',
  supplier: 'mock',
  chat: import.meta.env?.VITE_CHAT_MODE === 'api' ? 'api' : 'mock',
};

/** 切换某模块数据源（测试/演示/灰度用）。 */
export function setDataSource(module: keyof DataSourceConfig, mode: DataMode): void {
  dataConfig[module] = mode;
}

export interface ChatSendOptions {
  sessionId?: string;
  fileIds?: string[];
  signal?: AbortSignal;
  /** Mock 模式：指定展示哪一种 reply_type 的 fixture（演示/测试） */
  replyType?: ReplyType;
}

/** 流式回调（**Domain 形状** —— 原始 payload 的归一在适配器里做）。 */
export interface ChatStreamHandlers {
  onSession?: (sessionId: string) => void;
  onToken?: (delta: string) => void;
  onResult?: (res: ChatResponse) => void;
}

/**
 * 上传中的文件状态（页面 chips 用）。
 * `id` 是**客户端稳定键**（上传全程不变，轮询中按它更新同一条 chip）；
 * `fileId` 是服务端返回的 file_id（提交提问时传给 /chat 的 file_ids）。
 */
export interface UploadedFileState {
  id: string;
  fileId?: string;
  name: string;
  status: 'pending' | 'processing' | 'done' | 'error';
  /**
   * 上传响应回带的会话 id。
   * ⚠️ 必须采纳：**先传文件、后提问**时，是这次上传开了会话；丢掉它，
   * 后续提问会落到另一个新会话，文件归属对不上（`file_id 不属于本会话`）。
   */
  sessionId?: string;
  preview?: string;
  chunkCount?: number;
  error?: string;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const abortError = () => new DOMException('Aborted', 'AbortError');

/** mock 流式的每片间隔（毫秒）；测试里靠 waitFor 等待，不依赖精确时序。 */
const MOCK_CHUNK_DELAY = 14;

/**
 * Mock 模式的「假路由」：按问题关键词挑 fixture，模拟后端 router 的分支选择——
 * 否则演示时不管问什么都会回同一段知识问答。Phase 4 翻 `api` 后本函数不再被调用。
 */
function pickMockFixture(question: string): ChatRawResponse {
  if (/价格|报价|中标|多少钱|金额|预算|均价|采购|询价/.test(question)) {
    return mockChatFixtures.price_records;
  }
  if (/保证金|条款|依据|评分办法|工期|招标文件/.test(question)) {
    return mockChatFixtures.doc_qa;
  }
  return mockChatFixtures.kb_answer;
}

/** Mock 流式：按片吐 token，最后给终态结果 —— 没有后端也能演示打字机与慢路径。 */
async function mockStream(
  question: string,
  opts: ChatSendOptions,
  handlers: ChatStreamHandlers
): Promise<void> {
  const fixture = opts.replyType ? mockChatFixtures[opts.replyType] : pickMockFixture(question);
  const res = chatResponseAdapter(fixture);
  handlers.onSession?.(res.sessionId);

  for (const piece of chunkText(res.answer, 8)) {
    if (opts.signal?.aborted) throw abortError();
    handlers.onToken?.(piece);
    await sleep(MOCK_CHUNK_DELAY);
  }
  if (opts.signal?.aborted) throw abortError();
  handlers.onResult?.(res);
}

/** Mock 上传：直接走完 pending → processing → done，返回假的解析结果。 */
async function mockUpload(
  file: File,
  key: string,
  onUpdate: (f: UploadedFileState) => void,
  signal?: AbortSignal
): Promise<void> {
  // 与真实上传保持同一契约：回带 sessionId（先传文件后提问靠它归属会话）
  const sessionId = 'mock-session';
  onUpdate({ id: key, fileId: `mock_${key}`, sessionId, name: file.name, status: 'processing' });
  await sleep(420);
  if (signal?.aborted) return;
  onUpdate({
    id: key,
    fileId: `mock_${key}`,
    sessionId,
    name: file.name,
    status: 'done',
    chunkCount: 18,
    preview: `（演示）已解析 ${file.name}`,
  });
}

/** 真实上传：异步端点 + 1.5s 轮询（轮询端点已豁免限流）。 */
async function realUpload(
  file: File,
  key: string,
  sessionId: string | null,
  onUpdate: (f: UploadedFileState) => void,
  signal?: AbortSignal
): Promise<void> {
  const up = await backend.uploadFile(file, sessionId ?? undefined, { async: true, signal });
  const fileId = up.fileId;
  const sid = up.sessionId || undefined;
  onUpdate({ id: key, fileId, sessionId: sid, name: file.name, status: 'pending' });

  const deadline = Date.now() + 5 * 60 * 1000; // 轮询上限 ~5 分钟
  while (Date.now() < deadline) {
    await sleep(1500);
    if (signal?.aborted) return;

    let raw: Record<string, unknown>;
    try {
      raw = await backend.getFileStatus(fileId);
    } catch (err) {
      onUpdate({ id: key, fileId, name: file.name, status: 'error', error: (err as Error).message });
      return;
    }
    const status = String(raw.status ?? '');
    if (status === 'done') {
      onUpdate({
        id: key,
        fileId,
        sessionId: sid,
        name: file.name,
        status: 'done',
        preview: raw.preview === undefined ? undefined : String(raw.preview),
        chunkCount: raw.chunk_count === undefined ? undefined : Number(raw.chunk_count),
      });
      return;
    }
    if (status === 'error') {
      onUpdate({
        id: key,
        fileId,
        sessionId: sid,
        name: file.name,
        status: 'error',
        error: String(raw.error ?? '解析失败'),
      });
      return;
    }
    onUpdate({
      id: key,
      fileId,
      sessionId: sid,
      name: file.name,
      status: status === 'processing' ? 'processing' : 'pending',
    });
  }
  onUpdate({
    id: key,
    fileId,
    sessionId: sid,
    name: file.name,
    status: 'error',
    error: '解析超时（超过 5 分钟）',
  });
}

export const api = {
  kanban: {
    async getOverview(): Promise<KanbanOverview> {
      if (dataConfig.kanban === 'mock') return kanbanAdapter(mockKanbanRaw);
      return kanbanAdapter(await backend.getKanbanOverview());
    },
  },

  inquiry: {
    async getSummary(): Promise<InquirySummary> {
      if (dataConfig.inquiry === 'mock') return inquiryAdapter(mockInquiryRaw);
      return inquiryAdapter(await backend.getInquirySummary());
    },
  },

  supplier: {
    async getOverview(): Promise<SupplierOverview> {
      if (dataConfig.supplier === 'mock') return supplierAdapter(mockSupplierRaw);
      return supplierAdapter(await backend.getSuppliers());
    },
  },

  chat: {
    /** 非流式（一次性返回）。 */
    async send(question: string, opts: ChatSendOptions = {}): Promise<ChatResponse> {
      if (dataConfig.chat === 'mock') {
        const fixture = opts.replyType
          ? mockChatFixtures[opts.replyType]
          : pickMockFixture(question);
        return chatResponseAdapter(fixture);
      }
      return chatResponseAdapter(await backend.sendChat(question, opts));
    },

    /**
     * 流式（SSE）。mock 态用模拟流式（按片吐 token）—— 没后端也能演示打字机与慢路径占位；
     * api 态走真实 `/chat/stream`，原始 payload 经适配器归一后再回调。
     */
    async stream(
      question: string,
      opts: ChatSendOptions,
      handlers: ChatStreamHandlers
    ): Promise<void> {
      if (dataConfig.chat === 'mock') {
        await mockStream(question, opts, handlers);
        return;
      }
      await backend.streamChat(question, opts, {
        onSession: handlers.onSession,
        onToken: handlers.onToken,
        onResult: (raw) => handlers.onResult?.(chatResponseAdapter(raw)),
      });
    },

    /**
     * 上传 + 轮询到解析完成；状态变化经 onUpdate 回吐。
     * `key` 是客户端稳定键，全程不变，便于按它更新同一条 chip。
     */
    async upload(
      file: File,
      key: string,
      sessionId: string | null,
      onUpdate: (f: UploadedFileState) => void,
      signal?: AbortSignal
    ): Promise<void> {
      if (dataConfig.chat === 'mock') return mockUpload(file, key, onUpdate, signal);
      return realUpload(file, key, sessionId, onUpdate, signal);
    },
  },
};
