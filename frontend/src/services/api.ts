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
import { mockChatFixtures } from '@/mock/mockChat';
import { mockInquiryRaw } from '@/mock/mockInquiry';
import { mockKanbanRaw } from '@/mock/mockKanban';
import { mockSupplierRaw } from '@/mock/mockSupplier';
import * as backend from '@/services/backend';
import type { ChatResponse, ReplyType } from '@/types/chat';
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
 * 三模块默认 Mock（真实数据/后端就绪后翻 `'api'`）；chat 在 Phase 4 翻 `'api'` 接真后端。
 */
export const dataConfig: DataSourceConfig = {
  kanban: 'mock',
  inquiry: 'mock',
  supplier: 'mock',
  chat: 'mock',
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
    async send(question: string, opts: ChatSendOptions = {}): Promise<ChatResponse> {
      if (dataConfig.chat === 'mock') {
        const fixture = opts.replyType ? mockChatFixtures[opts.replyType] : mockChatFixtures.kb_answer;
        return chatResponseAdapter(fixture);
      }
      return chatResponseAdapter(await backend.sendChat(question, opts));
    },
  },
};
