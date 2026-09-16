/**
 * 智能询价领域契约 —— 「项目概况 → 相似项目金额参考」。
 *
 * 唯一链路是 `POST /inquiry/reference` 独立端点：粘贴一段招标项目概况 → 语义
 * 检索 `bidding.announcement` → 相似中标 + 金额区间（万元）+ 公告原文链接 + 产品
 * 明细。见 docs/plan1/项目概况相似检索与金额参考执行方案_20260916.md。
 *
 * 单位约定（方案 §7，字段名即契约）：
 *   - `winningAmountWan` / `ReferenceStats.*Wan` —— **万元**；
 *   - 产品 `unitPriceYuan` —— 保留**元**（Phase 3 既定口径）。
 */

/** 产品明细（参考链，单价元）。 */
export interface ReferenceProduct {
  productName: string;
  normalizedProductName?: string;
  brand?: string;
  model?: string;
  quantity?: string;
  unit?: string;
  /** 单价（元）；自由文本解析不出为 null（不伪造 0） */
  unitPriceYuan: number | null;
}

/** 相似项目（参考链，金额万元）。 */
export interface ReferenceProject {
  announcementId: string;
  projectNo?: string;
  projectName: string;
  purchaser?: string;
  region: string;
  /** 中标金额（万元） */
  winningAmountWan: number;
  winningDate?: string;
  /** 补全后的公告原文链接（ccgp.gov.cn 具体公告页） */
  sourceUrl?: string;
  score: number;
  products: ReferenceProduct[];
}

/** 金额统计（万元）。 */
export interface ReferenceStats {
  minWan: number;
  medianWan: number;
  maxWan: number;
  avgWan: number;
  sampleCount: number;
}

/** 参考链完整响应（Domain）。 */
export interface ReferenceOverview {
  query: string;
  total: number;
  stats: ReferenceStats | null;
  projects: ReferenceProject[];
}

/** 参考链原始形状（snake_case，后端直接返回）。 */
export interface ReferenceRawProduct {
  product_name?: string | null;
  normalized_product_name?: string | null;
  brand?: string | null;
  model?: string | null;
  quantity?: string | null;
  unit?: string | null;
  unit_price_yuan?: number | null;
}

export interface ReferenceRawProject {
  announcement_id?: string | null;
  project_no?: string | null;
  project_name?: string | null;
  purchaser?: string | null;
  region?: string | null;
  winning_amount_wan?: number | null;
  winning_date?: string | null;
  source_url?: string | null;
  score?: number | null;
  products?: ReferenceRawProduct[] | null;
}

export interface ReferenceRawResponse {
  query?: string | null;
  total?: number | null;
  amount_stats?: {
    min_wan?: number | null;
    median_wan?: number | null;
    max_wan?: number | null;
    avg_wan?: number | null;
    sample_count?: number | null;
  } | null;
  projects?: ReferenceRawProject[] | null;
}
