import { describe, expect, it } from 'vitest';
import { chatResponseAdapter } from '@/adapters/chatAdapter';
import { inquiryAdapter } from '@/adapters/inquiryAdapter';
import { kanbanAdapter } from '@/adapters/kanbanAdapter';
import { supplierAdapter } from '@/adapters/supplierAdapter';
import { mockChatFixtures } from '@/mock/mockChat';
import { mockInquiryRaw } from '@/mock/mockInquiry';
import { mockKanbanRaw } from '@/mock/mockKanban';
import { mockSupplierRaw } from '@/mock/mockSupplier';
import type { ReplyType } from '@/types/chat';
import type { InquiryRawSummary } from '@/types/inquiry';

/* 适配器层（Phase 2 硬验收）：
 * ① 同一份 mock raw 经适配器得到**稳定** Domain 对象（纯函数，不改入参）；
 * ② 宽容映射：另一套字段名 / 缺字段 / 越界值都能安全归一。 */

describe('kanbanAdapter', () => {
  it('同份 raw 适配两次结果深等，且不改入参', () => {
    const before = JSON.stringify(mockKanbanRaw);
    expect(kanbanAdapter(mockKanbanRaw)).toEqual(kanbanAdapter(mockKanbanRaw));
    expect(JSON.stringify(mockKanbanRaw)).toBe(before);
  });

  it('snake_case → camelCase 映射正确（金额单位：万元）', () => {
    const r = kanbanAdapter(mockKanbanRaw);
    expect(r.summary.totalProjects).toBe(1243);
    expect(r.summary.totalAmount).toBe(864_200);
    expect(r.summary.avgCompetition).toBe(6.8);
    expect(r.filters.regions).toContain('广东省');
    expect(r.heatmap[0]).toEqual({ region: '广东省', count: 218, amount: 164_200 });
    expect(r.trend).toHaveLength(11);
  });

  it('金额缺失保留 null（区别于 0），汇总缺省回落', () => {
    const empty = kanbanAdapter({
      summary: { total_projects: 5, total_amount: null },
      filters: { regions: [], industries: [], categories: [] },
      heatmap: [],
      composition: [],
      trend: [],
    });
    expect(empty.summary.totalAmount).toBeNull();
    expect(empty.summary.avgCompetition).toBe(1);
  });
});

describe('inquiryAdapter', () => {
  it('报价总价 = 单价 × 数量；偏离度 / 推荐标记 / 阈值映射', () => {
    const r = inquiryAdapter(mockInquiryRaw);
    expect(r.status).toBe('IN_PROGRESS');
    const item = r.items[0];
    expect(item.itemId).toBe('ITM-01');
    expect(item.budgetThreshold).toBe(1000);
    const best = item.quotes.find((q) => q.isRecommended);
    expect(best?.unitPrice).toBe(820);
    expect(best?.totalPrice).toBe(820 * 120);
    expect(best?.deviationRate).toBe(-3.5);
  });

  it('宽容映射：另一套字段名 + 阈值缺省（基准单价上浮 15%）', () => {
    const alt: InquiryRawSummary = {
      inquiryId: 'X',
      title: 'T',
      status: 'completed',
      totalBudget: 100,
      createdAt: '2026-01-01',
      items: [
        {
          itemId: 'I1',
          itemName: '项',
          specifications: 's',
          unit: '台',
          targetQuantity: 2,
          benchmarkUnitPrice: 100,
          quotes: [
            { supplierId: 'S1', supplierName: '甲', unitPrice: 90, isRecommended: true, deviationRate: -10 },
          ],
        },
      ],
    };
    const r = inquiryAdapter(alt);
    expect(r.inquiryId).toBe('X');
    expect(r.status).toBe('COMPLETED');
    expect(r.items[0].budgetThreshold).toBe(115);
    expect(r.items[0].quotes[0].totalPrice).toBe(180);
    expect(r.items[0].quotes[0].isRecommended).toBe(true);
  });
});

describe('supplierAdapter', () => {
  it('同份 raw 两次深等，筛选器与雷达透传', () => {
    const r1 = supplierAdapter(mockSupplierRaw);
    expect(r1).toEqual(supplierAdapter(mockSupplierRaw));
    expect(r1.suppliers).toHaveLength(7);
    expect(r1.filters.scoreRange).toEqual([60, 98]);
    expect(r1.suppliers[0].radar.dimensions).toHaveLength(5);
  });

  it('评分越界钳制到 0~100；缺 stats 归零、缺标签回落空数组', () => {
    const r = supplierAdapter({
      suppliers: [
        { id: 's1', name: '甲', score: 120 },
        { id: 's2', name: '乙', score: -5 },
      ],
    });
    expect(r.suppliers[0].score).toBe(100);
    expect(r.suppliers[1].score).toBe(0);
    expect(r.suppliers[0].stats.totalBids).toBe(0);
    expect(r.suppliers[0].riskTags).toEqual([]);
    expect(r.suppliers[0].region).toBe('未标注');
  });
});

describe('chatResponseAdapter', () => {
  const allTypes: ReplyType[] = [
    'kb_answer',
    'price_records',
    'doc_qa',
    'deep_agent',
    'chat',
    'safety',
    'fallback',
  ];

  for (const t of allTypes) {
    it(`分支 ${t}：kind ≡ replyType`, () => {
      const res = chatResponseAdapter(mockChatFixtures[t]);
      expect(res.replyType).toBe(t);
      expect(res.data.kind).toBe(t);
    });
  }

  it('kb_answer：citations 映射（contextIndex / docName / score）', () => {
    const res = chatResponseAdapter(mockChatFixtures.kb_answer);
    expect(res.citations).toHaveLength(2);
    expect(res.citations[0]).toMatchObject({
      contextIndex: 1,
      docName: '中华人民共和国招标投标法',
      score: 0.9775,
    });
  });

  it('price_records：records / tables / meta 映射', () => {
    const res = chatResponseAdapter(mockChatFixtures.price_records);
    if (res.data.kind !== 'price_records') throw new Error('分支不符');
    expect(res.data.records).toHaveLength(3);
    expect(res.data.records[0].project_name).toBe('智慧园区安防系统集成项目');
    expect(res.data.tables).toEqual(['bid_project']);
    expect(res.data.meta?.totalHits).toBe(3);
    expect(res.data.intent?.subRoute).toBe('bidding_query');
  });

  it('doc_qa：mode / sources / retrieval / attachedDocs 映射', () => {
    const res = chatResponseAdapter(mockChatFixtures.doc_qa);
    if (res.data.kind !== 'doc_qa') throw new Error('分支不符');
    expect(res.data.mode).toBe('retrieval');
    expect(res.data.sources).toHaveLength(2);
    expect(res.data.retrieval?.blocks).toBe(8);
    expect(res.data.retrieval?.notes).toEqual([]);
    expect(res.data.attachedDocs).toEqual(['智慧园区智慧化改造项目招标文件.pdf']);
  });

  it('deep_agent：trace 的 plan / steps / reflection 映射', () => {
    const res = chatResponseAdapter(mockChatFixtures.deep_agent);
    if (res.data.kind !== 'deep_agent') throw new Error('分支不符');
    expect(res.data.trace.plan).toHaveLength(2);
    expect(res.data.trace.steps).toHaveLength(2);
    expect(res.data.trace.steps[0].sources[0]).toContain('智慧园区');
    expect(res.data.trace.reflection?.verdict).toBe('adequate');
    expect(res.data.draftAnswer).not.toBe('');
  });

  it('未知 reply_type → fallback（不把成功作答渲染成空白）', () => {
    const res = chatResponseAdapter({ reply_type: 'weird_new_type', answer: 'x' });
    expect(res.replyType).toBe('fallback');
    expect(res.data.kind).toBe('fallback');
  });
});
