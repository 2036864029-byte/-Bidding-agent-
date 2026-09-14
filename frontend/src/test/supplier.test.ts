import { describe, expect, it } from 'vitest';
import { supplierAdapter } from '@/adapters/supplierAdapter';
import { mockSupplierRaw } from '@/mock/mockSupplier';
import {
  canAddCompare,
  EMPTY_FILTERS,
  filterSuppliers,
  MAX_COMPARE,
  priceCompareData,
  radarCompareData,
  riskCount,
  riskSeverity,
  toggleCompare,
  toggleValue,
  type SupplierFilterState,
} from '@/lib/supplier';
import type { Supplier } from '@/types/supplier';

/* 供应商筛选与对比整形的纯逻辑。 */

const overview = supplierAdapter(mockSupplierRaw);
const all = overview.suppliers;

const withFilters = (patch: Partial<SupplierFilterState>) =>
  filterSuppliers(all, { ...EMPTY_FILTERS, ...patch });

describe('filterSuppliers', () => {
  it('空筛选返回全部', () => {
    expect(withFilters({})).toHaveLength(7);
  });

  it('按地区筛选', () => {
    expect(withFilters({ regions: ['浙江省'] }).map((s) => s.name)).toHaveLength(3);
  });

  it('维度内多选是 OR', () => {
    // 安防工程企业一级（海康、大华）或 CMMI5（大华/新华三/太极/东软/中软）
    expect(withFilters({ qualifications: ['安防工程企业一级', 'CMMI5'] })).toHaveLength(6);
  });

  it('维度之间是 AND', () => {
    const r = withFilters({ regions: ['浙江省'], qualifications: ['CMMI5'] });
    expect(r.map((s) => s.name).sort()).toEqual(
      ['新华三技术有限公司', '浙江大华技术股份有限公司'].sort()
    );
  });

  it('按评分区间筛选', () => {
    const r = withFilters({ scoreRange: [90, 100] });
    expect(r).toHaveLength(3);
    expect(r.every((s) => s.score >= 90)).toBe(true);
  });

  it('条件互斥时返回空数组（页面据此出空态）', () => {
    expect(withFilters({ regions: ['辽宁省'], scoreRange: [95, 100] })).toHaveLength(0);
  });
});

describe('toggleValue / toggleCompare', () => {
  it('toggleValue 开/关且不改原数组', () => {
    const src = ['a'];
    expect(toggleValue(src, 'b')).toEqual(['a', 'b']);
    expect(toggleValue(src, 'a')).toEqual([]);
    expect(src).toEqual(['a']);
  });

  it('对比席位上限 3：超限忽略新增', () => {
    let ids: string[] = [];
    for (const id of ['s1', 's2', 's3']) ids = toggleCompare(ids, id);
    expect(ids).toHaveLength(MAX_COMPARE);
    expect(toggleCompare(ids, 's4')).toHaveLength(MAX_COMPARE); // 第 4 个被忽略
    expect(canAddCompare(ids, 's4')).toBe(false);
    expect(canAddCompare(ids, 's2')).toBe(true); // 已在席位内 → 可切换（实为移除）
  });

  it('再次点击已选项 = 移除', () => {
    expect(toggleCompare(['s1', 's2'], 's1')).toEqual(['s2']);
  });
});

describe('riskSeverity / riskCount', () => {
  it('司法类判为 critical，其余为 warning', () => {
    expect(riskSeverity('司法风险 1 条（合同纠纷）')).toBe('critical');
    expect(riskSeverity('经营异常待核实')).toBe('critical');
    expect(riskSeverity('2025 年劳动争议 1 条')).toBe('warning');
    expect(riskSeverity('报价高于同类均值 15%')).toBe('warning');
  });

  it('统计有风险提示的家数', () => {
    expect(riskCount(all)).toBe(4); // 大华 / 捷顺 / 太极 / 中软国际
  });
});

describe('radarCompareData', () => {
  it('把多家雷达对齐成行式数据，维度取并集', () => {
    const two = all.slice(0, 2);
    const rows = radarCompareData(two);
    expect(rows).toHaveLength(5);
    expect(rows[0].label).toBe('技术能力');
    expect(Object.keys(rows[0])).toContain(two[0].name);
    expect(Object.keys(rows[0])).toContain(two[1].name);
  });
});

describe('priceCompareData', () => {
  it('年份取并集并按年排序', () => {
    const rows = priceCompareData(all.slice(0, 3));
    expect(rows.map((r) => r.year)).toEqual(['2022', '2023', '2024', '2025']);
  });

  it('缺该年份的供应商**不写 0**，留空键以免画出假跌线', () => {
    const base = all[0];
    const trimmed: Supplier = {
      ...base,
      id: 'X',
      name: '缺年供应商',
      stats: {
        ...base.stats,
        priceHistory: [{ year: 2024, avgAmount: 500, count: 3 }],
      },
    };
    const rows = priceCompareData([all[0], trimmed]);
    const y2022 = rows.find((r) => r.year === '2022');
    expect(y2022).toBeDefined();
    expect(y2022).toHaveProperty(base.name);
    expect(y2022).not.toHaveProperty('缺年供应商'); // 不是 0，是「没有」
  });
});
