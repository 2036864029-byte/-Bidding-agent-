import { clamp, num, str, strArray } from '@/adapters/helpers';
import type {
  Supplier,
  SupplierOverview,
  SupplierRawItem,
  SupplierRawOverview,
} from '@/types/supplier';

/** 单条供应商 raw → Domain（评分统一钳制到 0~100）。 */
function adaptSupplier(s: SupplierRawItem): Supplier {
  const stats = s.stats ?? {};

  return {
    id: str(s.id),
    creditCode: str(s.credit_code),
    name: str(s.name, '未命名供应商'),
    region: str(s.region, '未标注'),
    categories: strArray(s.categories),
    qualifications: strArray(s.qualifications),
    score: clamp(num(s.score), 0, 100),
    radar: {
      dimensions: (s.radar?.dimensions ?? []).map((d) => ({
        label: str(d.label),
        value: clamp(num(d.value), 0, 100),
      })),
    },
    riskTags: strArray(s.risk_tags),
    coreCapability: str(s.core_capability, '-'),
    stats: {
      totalBids: num(stats.total_bids),
      wins: num(stats.wins),
      bidWinRate: clamp(num(stats.bid_win_rate), 0, 100),
      activeYears: num(stats.active_years),
      priceHistory: (stats.price_history ?? []).map((p) => ({
        year: num(p.year),
        avgAmount: num(p.avg_amount),
        count: num(p.count),
      })),
    },
  };
}

/**
 * supplierAdapter —— 供应商全景 raw → Domain。
 *
 * 宽容点：评分/雷达维度越界钳制；筛选器选项缺失回落空数组；
 * 价格历史缺字段逐项归零。评分口径待定（见 types/supplier.ts 文件头）。
 */
export function supplierAdapter(raw?: SupplierRawOverview | null): SupplierOverview {
  const data = raw ?? {};
  const filters = data.filters ?? {};

  return {
    filters: {
      qualifications: filters.qualifications ?? [],
      categories: filters.categories ?? [],
      regions: filters.regions ?? [],
      scoreRange: filters.score_range ?? [0, 100],
    },
    suppliers: (data.suppliers ?? []).map(adaptSupplier),
  };
}
