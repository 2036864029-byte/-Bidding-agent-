/**
 * 智能询价领域契约 —— 「项目概况 → 报价参考」。
 *
 * 语义：用户在左栏描述/上传项目情况，系统检索相似历史招标项目，
 * 右栏给出**中标价区间 + 相似项目清单**。
 *
 * ⚠️ 本契约**不是**一次独立的后端取数结果，而是**一次查询的派生视图**：
 * 数据源是左侧 `/chat` 返回的 `price_records.records`（真实来源 `ztb_clean.bid_project`），
 * 经 `lib/inquiry.ts` 的 `buildReference()` 纯函数换算。因此这里没有 `XxxRaw` 宽容形状
 * ——也就没有「后端字段漂移」这一层，字段映射只在 `buildReference` 一处。
 *
 * 字段一一对应 `bid_project` 的列（见 `PriceRecordsTable` 的列集）：
 *   project_number / project_name / province / project_category /
 *   purchaser / successful_bidder / winning_amount / winning_date
 *
 * 金额单位：**元**（与 `price_records` 一致），展示用 `fmtAmountYuan`。
 */

/** 相似项目（一条历史中标记录）。 */
export interface QuoteReferenceProject {
  /** 项目编号（← project_number） */
  projectId: string;
  projectName: string;
  /** 地区，省级（← province） */
  region: string;
  /** 项目类别（← project_category） */
  category?: string;
  /** 采购人（← purchaser） */
  purchaser?: string;
  /** 中标供应商（← successful_bidder） */
  winningBidder?: string;
  /** 中标金额（元）；0 = 金额未公开（见 buildReference） */
  winningAmount: number;
  winningDate: string;
}

/** 报价参考 —— 右栏的完整呈现模型。 */
export interface QuoteReference {
  /** 来源提问（截断后作卡片标题） */
  title: string;
  /** 有金额的样本数（区间统计的样本量） */
  sampleCount: number;
  /** 参考区间（元） */
  minAmount: number;
  medianAmount: number;
  maxAmount: number;
  /** 金额未公开的项目数（winning_amount 为 0 或空）—— 与 sampleCount 互补 */
  missingAmountCount: number;
  projects: QuoteReferenceProject[];
}

/* ─────────────────────────────────────────────────────────────────
 * 「项目概况 → 相似项目金额参考」（POST /inquiry/reference 独立链路）。
 *
 * 与上面 `QuoteReference` **并行**：它由 `/chat` 的 price_records 派生（元口径、
 * 无原文链接）；这一组是**独立端点**直连 `bidding.announcement` 语义检索
 * （金额统一**万元**、带公告原文 `source_url` 与产品明细），见 docs/plan1/
 * 项目概况相似检索与金额参考执行方案_20260916.md §5~§7。
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
