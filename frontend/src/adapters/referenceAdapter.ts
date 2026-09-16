import { num, str } from '@/adapters/helpers';
import type {
  ReferenceOverview,
  ReferenceProduct,
  ReferenceProject,
  ReferenceRawProduct,
  ReferenceRawProject,
  ReferenceRawResponse,
} from '@/types/inquiry';

/**
 * referenceAdapter —— 项目概况参考 raw（snake_case）→ Domain（camelCase）。
 *
 * 宽容点（后端数据特征）：
 *   - 金额字段可能有、可能 null：万元统一 `num` 兜底 0；`stats` 整体缺失给 null
 *     （区别于空对象——UI 显示空态而不是「0~0 万」）；
 *   - `products` 可能缺失/空数组：一律给 []，UI 不必判空；
 *   - 单价 `unit_price_yuan` 是自由文本解析结果，可能 null：保留 null（不伪造 0）。
 */

const optStr = (v: unknown): string | undefined => {
  const s = str(v).trim();
  return s === '' ? undefined : s;
};

/** 单价（元）：null/undefined/非有限值 → null（不伪造 0）。 */
function toPriceOrNull(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = num(v, Number.NaN);
  return Number.isFinite(n) ? n : null;
}

function adaptProduct(p?: ReferenceRawProduct | null): ReferenceProduct {
  return {
    productName: str(p?.product_name, '（未命名产品）'),
    normalizedProductName: optStr(p?.normalized_product_name),
    brand: optStr(p?.brand),
    model: optStr(p?.model),
    quantity: optStr(p?.quantity),
    unit: optStr(p?.unit),
    unitPriceYuan: toPriceOrNull(p?.unit_price_yuan),
  };
}

function adaptProject(p?: ReferenceRawProject | null): ReferenceProject {
  return {
    announcementId: str(p?.announcement_id),
    projectNo: optStr(p?.project_no),
    projectName: str(p?.project_name, '（未命名项目）'),
    purchaser: optStr(p?.purchaser),
    region: str(p?.region, '—'),
    winningAmountWan: num(p?.winning_amount_wan),
    winningDate: optStr(p?.winning_date),
    sourceUrl: optStr(p?.source_url),
    score: num(p?.score),
    products: (p?.products ?? []).map(adaptProduct),
  };
}

export function referenceAdapter(raw?: ReferenceRawResponse | null): ReferenceOverview {
  const data = raw ?? {};
  const statsRaw = data.amount_stats ?? null;
  return {
    query: str(data.query),
    total: num(data.total),
    stats: statsRaw
      ? {
          minWan: num(statsRaw.min_wan),
          medianWan: num(statsRaw.median_wan),
          maxWan: num(statsRaw.max_wan),
          avgWan: num(statsRaw.avg_wan),
          sampleCount: num(statsRaw.sample_count),
        }
      : null,
    projects: (data.projects ?? []).map(adaptProject),
  };
}
