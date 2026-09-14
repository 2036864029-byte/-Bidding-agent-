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
        const fixture = opts.replyType
          ? mockChatFixtures[opts.replyType]
          : pickMockFixture(question);
        return chatResponseAdapter(fixture);
      }
      return chatResponseAdapter(await backend.sendChat(question, opts));
    },
  },
};
