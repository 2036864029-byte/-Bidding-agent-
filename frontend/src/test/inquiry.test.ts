import { describe, expect, it } from 'vitest';
import {
  buildReference,
  fmtProjectAmount,
  fmtReferenceRange,
  pickAmounts,
  referenceStats,
} from '@/lib/inquiry';
import type { PriceRecordRow } from '@/types/chat';

/* 报价参考的纯逻辑：金额抽取（含 0 值语义）、区间统计、records → 契约、区间文案。 */

/** 与后端实测同形：金额是带千分位的字符串。 */
const row = (o: Partial<PriceRecordRow>): PriceRecordRow => o as PriceRecordRow;

const RECORDS: PriceRecordRow[] = [
  row({
    project_name: '智慧园区安防系统集成项目',
    project_number: 'ZTB-2026-GD-004517',
    purchaser: '广州开发区智慧园区管理委员会',
    successful_bidder: '杭州海康威视数字技术股份有限公司',
    winning_amount: '4,860,000.00',
    winning_date: '2026-06-18',
    project_category: '信息化设备',
    province: '广东省',
  }),
  row({
    project_name: '某市公安视频专网改造项目',
    project_number: 'ZTB-2025-ZJ-009842',
    purchaser: '杭州市公安局',
    successful_bidder: '杭州海康威视数字技术股份有限公司',
    winning_amount: '5,120,000.00',
    winning_date: '2025-11-04',
    project_category: '系统集成',
    province: '浙江省',
  }),
  row({
    project_name: '省级政务云视频监控扩容项目',
    project_number: 'ZTB-2024-GD-003176',
    purchaser: '广东省政务服务数据管理局',
    successful_bidder: '杭州海康威视数字技术股份有限公司',
    winning_amount: '2,880,000.00',
    winning_date: '2024-09-22',
    project_category: '系统集成',
    province: '广东省',
  }),
];

describe('pickAmounts', () => {
  it('认带千分位的字符串（后端实测形状）', () => {
    expect(pickAmounts(RECORDS)).toEqual([4860000, 5120000, 2880000]);
  });

  it('认数字与纯数字串', () => {
    expect(pickAmounts([row({ winning_amount: 1000 }), row({ winning_amount: '2500.5' })])).toEqual([
      1000, 2500.5,
    ]);
  });

  it('⚠️ 跳过 0 —— 0 的语义是「金额未公开」，计入会让区间下限塌成 0', () => {
    expect(pickAmounts([row({ winning_amount: 0 }), row({ winning_amount: '0.00' })])).toEqual([]);
  });

  it('跳过 null / undefined / 空串 / 非数字串', () => {
    expect(
      pickAmounts([
        row({ winning_amount: null }),
        row({ winning_amount: undefined }),
        row({ winning_amount: '' }),
        row({ winning_amount: '面议' }),
      ])
    ).toEqual([]);
  });

  it('没有金额列时返回空数组（不炸）', () => {
    expect(pickAmounts([row({ project_name: '某项目' })])).toEqual([]);
  });
});

describe('referenceStats', () => {
  it('空数组 → 全 0（不产生 NaN/Infinity）', () => {
    expect(referenceStats([])).toEqual({ min: 0, median: 0, max: 0, sampleCount: 0 });
  });

  it('奇数个：中位数取中间值', () => {
    expect(referenceStats([30, 10, 20])).toEqual({
      min: 10,
      median: 20,
      max: 30,
      sampleCount: 3,
    });
  });

  it('偶数个：中位数取中间两个的均值', () => {
    expect(referenceStats([10, 20, 30, 40])).toEqual({
      min: 10,
      median: 25,
      max: 40,
      sampleCount: 4,
    });
  });

  it('不修改入参数组', () => {
    const input = [3, 1, 2];
    referenceStats(input);
    expect(input).toEqual([3, 1, 2]);
  });
});

describe('buildReference（records → 报价参考契约）', () => {
  const ref = buildReference(RECORDS, '摄像机采购');

  it('字段一一映射 bid_project 的列', () => {
    expect(ref.projects[0]).toEqual({
      projectId: 'ZTB-2026-GD-004517',
      projectName: '智慧园区安防系统集成项目',
      region: '广东省',
      category: '信息化设备',
      purchaser: '广州开发区智慧园区管理委员会',
      winningBidder: '杭州海康威视数字技术股份有限公司',
      winningAmount: 4860000,
      winningDate: '2026-06-18',
    });
  });

  it('区间与样本数来自金额列', () => {
    expect(ref.sampleCount).toBe(3);
    expect(ref.minAmount).toBe(2880000);
    expect(ref.medianAmount).toBe(4860000);
    expect(ref.maxAmount).toBe(5120000);
    expect(ref.missingAmountCount).toBe(0);
  });

  it('缺列兜底（不出现 undefined）', () => {
    const bare = buildReference([row({ project_name: '只有一个名字' })]);
    expect(bare.projects[0]).toMatchObject({
      projectId: '#1',
      projectName: '只有一个名字',
      region: '—',
      winningAmount: 0,
      winningDate: '—',
    });
    expect(bare.projects[0].category).toBeUndefined();
    expect(bare.sampleCount).toBe(0);
  });

  it('金额未公开的行计入 missingAmountCount，但不进区间', () => {
    const mixed = buildReference([
      ...RECORDS,
      row({ project_name: '金额未公开的项目', winning_amount: 0 }),
    ]);
    expect(mixed.projects).toHaveLength(4);
    expect(mixed.sampleCount).toBe(3);
    expect(mixed.missingAmountCount).toBe(1);
  });

  it('title 缺省为「本次查询」', () => {
    expect(buildReference([]).title).toBe('本次查询');
  });

  it('空 records → 空参考（不炸）', () => {
    const empty = buildReference([]);
    expect(empty).toMatchObject({ sampleCount: 0, projects: [], missingAmountCount: 0 });
  });
});

describe('fmtReferenceRange', () => {
  it('按最大值定单位，两端同单位', () => {
    expect(fmtReferenceRange({ minAmount: 2880000, maxAmount: 5120000, sampleCount: 3 })).toBe(
      '288.00 ~ 512.00 万元'
    );
  });

  it('小额走「元」口径', () => {
    expect(fmtReferenceRange({ minAmount: 1200, maxAmount: 8500, sampleCount: 2 })).toBe(
      '1,200 ~ 8,500 元'
    );
  });

  it('大额走「亿元」口径', () => {
    expect(fmtReferenceRange({ minAmount: 120000000, maxAmount: 350000000, sampleCount: 2 })).toBe(
      '1.20 ~ 3.50 亿元'
    );
  });

  it('无有效金额时给明确文案（不是 0 ~ 0）', () => {
    expect(fmtReferenceRange({ minAmount: 0, maxAmount: 0, sampleCount: 0 })).toBe(
      '暂无可用的中标金额'
    );
  });
});

describe('fmtProjectAmount', () => {
  it('按量级折单位', () => {
    expect(fmtProjectAmount(4860000)).toBe('486.00 万元');
    expect(fmtProjectAmount(8500)).toBe('8,500 元');
  });

  it('0 / 负数 → 金额未公开', () => {
    expect(fmtProjectAmount(0)).toBe('金额未公开');
  });
});
