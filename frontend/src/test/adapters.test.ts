import { describe, expect, it } from 'vitest';
import { chatResponseAdapter } from '@/adapters/chatAdapter';
import { kanbanAdapter } from '@/adapters/kanbanAdapter';
import { referenceAdapter } from '@/adapters/referenceAdapter';
import { supplierAdapter } from '@/adapters/supplierAdapter';
import { mockChatFixtures } from '@/mock/mockChat';
import { mockKanbanRaw } from '@/mock/mockKanban';
import { mockReferenceRaw } from '@/mock/mockReference';
import { mockSupplierRaw } from '@/mock/mockSupplier';
import type { ReplyType } from '@/types/chat';
import type { KanbanRawOverview } from '@/types/kanban';

/* 适配器层（Phase 2 硬验收）：
 * ① 同一份 mock raw 经适配器得到**稳定** Domain 对象（纯函数，不改入参）；
 * ② 宽容映射：另一套字段名 / 缺字段 / 越界值都能安全归一。 */

describe('kanbanAdapter', () => {
  it('同份 raw 适配两次结果深等，且不改入参', () => {
    const before = JSON.stringify(mockKanbanRaw);
    expect(kanbanAdapter(mockKanbanRaw)).toEqual(kanbanAdapter(mockKanbanRaw));
    expect(JSON.stringify(mockKanbanRaw)).toBe(before);
  });

  it('snake_case → camelCase 映射正确（纯数量口径）', () => {
    const r = kanbanAdapter(mockKanbanRaw);
    expect(r.summary).toEqual({
      totalProjects: 18497,
      recentProjects: 755,
      regions: 32,
      industries: 30,
    });
    expect(r.filters.regions).toContain('上海市');
    expect(r.heatmap[0]).toEqual({ region: '上海市', count: 4698 });
    // 热力图取自哪个月必须带出来（面板要如实标注窗口）
    expect(r.heatmapMonth).toBe('2026-09-01');
    expect(r.composition[0]).toEqual({ industry: '学校', count: 3246, pct: 20.1 });
  });

  it('行业走势：months 与各序列 values 长度对齐', () => {
    const t = kanbanAdapter(mockKanbanRaw).industryTrend;
    expect(t.months).toHaveLength(10);
    expect(t.series).toHaveLength(3); // all-pairs 硬约束：最多 3 条
    expect(t.series[0].name).toBe('政府采购');
    for (const s of t.series) expect(s.values).toHaveLength(t.months.length);
  });

  it('⚠️ 序列长度与 months 对不齐时补 0/截断 —— 错位比缺数更糟', () => {
    const t = kanbanAdapter({
      industry_trend: {
        months: ['2026-01', '2026-02', '2026-03'],
        series: [
          { name: '甲', values: [1] }, // 太短 → 补 0
          { name: '乙', values: [1, 2, 3, 4, 5] }, // 太长 → 截断
          { name: '', values: [9, 9, 9] }, // 无名序列 → 丢弃
        ],
      },
    } as unknown as KanbanRawOverview).industryTrend;
    expect(t.series.map((s) => s.name)).toEqual(['甲', '乙']);
    expect(t.series[0].values).toEqual([1, 0, 0]);
    expect(t.series[1].values).toEqual([1, 2, 3]);
  });

  it('缺字段一律安全回落（汇总归零 / 数组空 / 走势空）', () => {
    const empty = kanbanAdapter({ summary: { total_projects: 5 } } as KanbanRawOverview);
    expect(empty.summary).toEqual({
      totalProjects: 5,
      recentProjects: 0,
      regions: 0,
      industries: 0,
    });
    expect(empty.heatmap).toEqual([]);
    expect(empty.heatmapMonth).toBeNull();
    expect(empty.industryTrend).toEqual({ months: [], series: [] });
  });
});

describe('supplierAdapter', () => {
  it('同份 raw 两次深等；筛选器与分页计数透传', () => {
    const r1 = supplierAdapter(mockSupplierRaw);
    expect(r1).toEqual(supplierAdapter(mockSupplierRaw));
    expect(r1.suppliers).toHaveLength(3);
    expect(r1.filters.provinces).toContain('安徽省');
    expect(r1.total).toBe(39969);
    expect(r1.returned).toBe(3);
  });

  it('工商字段 + 中标业绩映射（金额单位：元）', () => {
    const s = supplierAdapter(mockSupplierRaw).suppliers[0];
    expect(s).toMatchObject({
      id: 'LEGACY:1024',
      source: 'LEGACY',
      name: '安徽海纳信息科技有限公司',
      province: '安徽省',
      industry: '软件和信息技术服务业',
      legalPerson: '郭振',
      registeredCapital: '500万人民币',
      winCount: 12,
      winAmountTotal: 48620000,
      mainRegion: '安徽省',
    });
    expect(s.recentWins[0]).toEqual({
      projectNo: 'ZTB-2025-AH-004182',
      projectName: '宿州市政务服务中心安防监控系统升级项目',
      region: '安徽省',
      category: '信息化设备',
      winningAmount: 4860000,
      winningDate: '2025-11-04',
    });
  });

  it('⚠️ 缺工商字段一律 undefined —— 不伪造空串/0（CCGP 侧那 1,058 家就是这样）', () => {
    const ccgp = supplierAdapter(mockSupplierRaw).suppliers[2];
    expect(ccgp.source).toBe('CCGP');
    expect(ccgp.province).toBeUndefined();
    expect(ccgp.industry).toBeUndefined();
    expect(ccgp.legalPerson).toBeUndefined();
    expect(ccgp.winCount).toBe(1); // 工商没有，但中标业绩要有
  });

  it('中标金额缺失保留 null（区别于 0）；容忍带千分位的字符串金额', () => {
    const r = supplierAdapter({
      suppliers: [
        {
          id: 's1',
          recent_wins: [
            { project_name: 'X', winning_amount: '1,280,000.00', winning_date: '2026-01-01' },
            { project_name: 'Y', winning_amount: null, winning_date: '2026-01-02' },
          ],
        },
      ],
    });
    expect(r.suppliers[0].recentWins[0].winningAmount).toBe(1280000);
    expect(r.suppliers[0].recentWins[1].winningAmount).toBeNull();
  });

  it('缺省字段安全回落（名称兜底 / 数组空 / 计数 0）', () => {
    const r = supplierAdapter({ suppliers: [{}] });
    expect(r.suppliers[0].name).toBe('未命名企业');
    expect(r.suppliers[0].recentWins).toEqual([]);
    expect(r.suppliers[0].winCount).toBe(0);
    expect(r.filters.provinces).toEqual([]);
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
    expect(res.data.tables).toEqual(['ztb_clean.bid_project']);
    expect(res.data.meta?.totalHits).toBe(3);
    expect(res.data.intent?.subRoute).toBe('bidding_query');
  });

  it('price_records：columns 列定义映射（服务端权威「机器名 ↔ 中文表头」）', () => {
    const res = chatResponseAdapter(mockChatFixtures.price_records);
    if (res.data.kind !== 'price_records') throw new Error('分支不符');
    expect(res.data.columns[0]).toEqual({ key: 'project_name', label: '项目名称' });
    expect(res.data.columns.map((c) => c.key)).toContain('winning_amount');
  });

  it('price_records：缺 columns（旧后端）→ 回落空数组，不炸', () => {
    const res = chatResponseAdapter({
      reply_type: 'price_records',
      data: { records: [{ project_name: '甲' }] },
    });
    if (res.data.kind !== 'price_records') throw new Error('分支不符');
    expect(res.data.columns).toEqual([]);
  });

  it('⚠️ price_records 列名归一：真后端返回**中文列名**，必须转成英文键', () => {
    const res = chatResponseAdapter({
      reply_type: 'price_records',
      data: {
        records: [
          {
            项目名称: '某高速公路改扩建工程',
            项目编号: 'HBSJ-202307GL-056001001',
            中标金额: '417000000.00',
            中标日期: '2024-02-15',
            采购人: '湖北省公路事业发展中心',
            中标供应商: '湖北楚天智能交通股份有限公司',
            项目类别: '市政',
            省份: '湖北省',
            城市: '武汉市',
          },
        ],
        tables: ['ztb_clean.bid_project'],
      },
    });
    if (res.data.kind !== 'price_records') throw new Error('分支不符');

    expect(res.data.records[0]).toEqual({
      project_name: '某高速公路改扩建工程',
      project_number: 'HBSJ-202307GL-056001001',
      winning_amount: '417000000.00',
      winning_date: '2024-02-15',
      purchaser: '湖北省公路事业发展中心',
      successful_bidder: '湖北楚天智能交通股份有限公司',
      project_category: '市政',
      province: '湖北省',
      city: '武汉市',
    });
  });

  it('price_records：未收录的列名原样保留（不丢字段）', () => {
    const res = chatResponseAdapter({
      reply_type: 'price_records',
      data: { records: [{ 未知列: 'x', project_name: '甲' }] },
    });
    if (res.data.kind !== 'price_records') throw new Error('分支不符');
    expect(res.data.records[0]).toEqual({ 未知列: 'x', project_name: '甲' });
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

describe('referenceAdapter', () => {
  it('同份 raw 两次深等，且不改入参', () => {
    const before = JSON.stringify(mockReferenceRaw);
    expect(referenceAdapter(mockReferenceRaw)).toEqual(referenceAdapter(mockReferenceRaw));
    expect(JSON.stringify(mockReferenceRaw)).toBe(before);
  });

  it('snake_case → camelCase：金额万元、原文链接、产品明细、相似度', () => {
    const r = referenceAdapter(mockReferenceRaw);
    expect(r.query).toBe(mockReferenceRaw.query);
    expect(r.total).toBe(4);
    expect(r.stats).toEqual({
      minWan: 42.5,
      medianWan: 133.94,
      maxWan: 597.92,
      avgWan: 231.35,
      sampleCount: 4,
    });
    const p = r.projects[0];
    expect(p).toMatchObject({
      announcementId: 't20260911_27310629',
      projectName: '校园安防视频监控系统升级改造项目',
      region: '广东省',
      winningAmountWan: 133.94,
      winningDate: '2026-09-11',
      sourceUrl: expect.stringContaining('ccgp.gov.cn'),
      score: 0.87,
    });
    expect(p.products[0]).toEqual({
      productName: '400万星光级网络摄像机',
      normalizedProductName: '网络摄像机',
      brand: '海康威视',
      model: 'DS-2CD3T46WDV3',
      quantity: '120',
      unit: '台',
      unitPriceYuan: 1650,
    });
  });

  it('⚠️ 缺 stats / 缺 products / 单价 null —— 安全回落，不伪造', () => {
    const r = referenceAdapter({
      projects: [{ announcement_id: 'x' }],
    } as unknown as Parameters<typeof referenceAdapter>[0]);
    expect(r.stats).toBeNull();
    expect(r.projects[0].products).toEqual([]);
    expect(r.projects[0].score).toBe(0);
    // 单价 null 保留 null（不是 0）
    const withPrice = referenceAdapter({
      projects: [{ products: [{ product_name: 'p', unit_price_yuan: null }] }],
    } as never);
    expect(withPrice.projects[0].products[0].unitPriceYuan).toBeNull();
  });

  it('完全空 raw → 空 Domain（不炸）', () => {
    const r = referenceAdapter(undefined);
    expect(r.query).toBe('');
    expect(r.total).toBe(0);
    expect(r.stats).toBeNull();
    expect(r.projects).toEqual([]);
  });
});
