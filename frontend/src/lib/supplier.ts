import type { Supplier } from '@/types/supplier';

/**
 * 供应商筛选与对比数据整形的纯逻辑（可单测）。
 */

/** 对比席位上限：雷达叠加/多线对比属 all-pairs 图形，规范上限 3 个系列。 */
export const MAX_COMPARE = 3;

/** 分类色板（与 styles/index.css 的 --cat-N 同源，最多 3 个系列）。 */
export const CATEGORICAL_COLORS = ['var(--cat-1)', 'var(--cat-2)', 'var(--cat-3)'] as const;

export interface SupplierFilterState {
  qualifications: string[];
  categories: string[];
  regions: string[];
  scoreRange: [number, number];
}

export const EMPTY_FILTERS: SupplierFilterState = {
  qualifications: [],
  categories: [],
  regions: [],
  scoreRange: [0, 100],
};

/**
 * 多维筛选：**维度内 OR、维度间 AND** —— 标签组的常规语义
 * （勾了「浙江」和「广东」＝任意其一；同时又勾了资质＝还要满足资质）。
 */
export function filterSuppliers(
  suppliers: Supplier[],
  f: SupplierFilterState
): Supplier[] {
  return suppliers.filter((s) => {
    if (f.qualifications.length && !f.qualifications.some((q) => s.qualifications.includes(q))) {
      return false;
    }
    if (f.categories.length && !f.categories.some((c) => s.categories.includes(c))) {
      return false;
    }
    if (f.regions.length && !f.regions.includes(s.region)) {
      return false;
    }
    if (s.score < f.scoreRange[0] || s.score > f.scoreRange[1]) {
      return false;
    }
    return true;
  });
}

/** 标签开/关（返回新数组，不改原数组）。 */
export function toggleValue(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** 某维度是否处于「全部」态（未选任何标签）。 */
export const isAllSelected = (list: string[]): boolean => list.length === 0;

/** 对比席位是否还能再加（达到上限返回 false）。 */
export function canAddCompare(selectedIds: string[], id: string): boolean {
  return selectedIds.includes(id) || selectedIds.length < MAX_COMPARE;
}

/** 切换对比席位；超上限时忽略新增（返回原数组）。 */
export function toggleCompare(selectedIds: string[], id: string): string[] {
  if (selectedIds.includes(id)) return selectedIds.filter((x) => x !== id);
  if (selectedIds.length >= MAX_COMPARE) return selectedIds;
  return [...selectedIds, id];
}

/** 有风险标签的供应商数（卡片上的汇总用）。 */
export const riskCount = (suppliers: Supplier[]): number =>
  suppliers.filter((s) => s.riskTags.length > 0).length;

export type RiskSeverity = 'critical' | 'warning';

/** 风险标签的严重度（用于选语义色；无论哪档都配图标 + 文字，不靠颜色单独表意）。 */
export function riskSeverity(tag: string): RiskSeverity {
  return /司法|诉讼|异常|失信|处罚|违规/.test(tag) ? 'critical' : 'warning';
}

/**
 * 雷达对比数据：把 N 家供应商的雷达维度对齐成 Recharts 需要的行式数据。
 * 维度取并集（不同来源的维度名可能不完全一致）。
 */
export function radarCompareData(
  suppliers: Supplier[]
): Array<Record<string, string | number>> {
  const labels: string[] = [];
  for (const s of suppliers) {
    for (const d of s.radar.dimensions) {
      if (!labels.includes(d.label)) labels.push(d.label);
    }
  }
  return labels.map((label) => {
    const row: Record<string, string | number> = { label };
    for (const s of suppliers) {
      row[s.name] = s.radar.dimensions.find((d) => d.label === label)?.value ?? 0;
    }
    return row;
  });
}

/**
 * 中标均价对比数据（多线）。
 * **缺该年份的供应商不写 0** —— 写 0 会在图上画出一条跌到零的假线；
 * 留空键让折线断开，才是诚实的表达。
 */
export function priceCompareData(
  suppliers: Supplier[]
): Array<Record<string, string | number>> {
  const years = [...new Set(suppliers.flatMap((s) => s.stats.priceHistory.map((p) => p.year)))].sort(
    (a, b) => a - b
  );
  return years.map((year) => {
    const row: Record<string, string | number> = { year: String(year) };
    for (const s of suppliers) {
      const point = s.stats.priceHistory.find((p) => p.year === year);
      if (point) row[s.name] = point.avgAmount;
    }
    return row;
  });
}
