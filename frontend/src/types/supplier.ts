/**
 * 供应商全景领域契约（Domain Model）。
 *
 * 2026-09-15 重塑：原来的「能力评分卡」（score + 五维雷达 + 资质/风险标签）
 * **没有数据源**，已整体去掉。改为**可核验的工商档案 + 中标业绩**：
 *
 * - 工商信息（省份/行业/法人/注册资本/成立日期/经营范围…）来自
 *   `ztb_clean.company_info`（38,911 家，字段覆盖 99%+），经 `company_unified` 下发
 * - 中标业绩来自 `company_win` 关联表（按公司名归一化匹配）
 *
 * ⚠️ 两条口径必须如实呈现，别让 UI 装作很全：
 *   1. **工商字段只有 LEGACY 侧有**；CCGP 侧那 1,058 家是 8 天内的新公司，没有工商数据，
 *      `province` / `industry` 为 undefined，按地区/行业筛选时不会出现。
 *   2. 中标业绩是**名称匹配**的结果，不是权威关联；多中标人的项目金额已按人数均摊。
 *
 * 金额单位：**元**（与 `project_unified` / `price_records` 一致），展示用 `fmtAmountYuan`。
 */

/** 一条中标记录。 */
export interface SupplierWinRecord {
  projectNo: string;
  projectName: string;
  region: string;
  category?: string;
  /** 中标金额（元）；null = 未公开 */
  winningAmount: number | null;
  winningDate: string;
}

export interface Supplier {
  id: string;
  /** LEGACY = 工商档案（有省份/行业）；CCGP = 新数据（无工商信息） */
  source: string;
  name: string;

  // ── 工商信息（仅 LEGACY 可能有）──
  creditCode?: string;
  province?: string;
  city?: string;
  district?: string;
  industry?: string;
  legalPerson?: string;
  registeredCapital?: string;
  establishDate?: string;
  businessStatus?: string;
  companyType?: string;
  companyLevel?: string;
  address?: string;
  businessScope?: string;

  // ── 中标业绩 ──
  winCount: number;
  /** 中标总额（元） */
  winAmountTotal: number;
  firstWinDate?: string;
  lastWinDate?: string;
  /** 中标最多的省份（≠ 注册地） */
  mainRegion?: string;
  recentWins: SupplierWinRecord[];
}

export interface SupplierOverview {
  filters: {
    provinces: string[];
    industries: string[];
  };
  /** 符合条件的总数（分页用；前端一页只拿到 60 家） */
  total: number;
  /** 本页实际返回数 */
  returned: number;
  suppliers: Supplier[];
}

/** 后端原始记录（snake_case；字段可漂移，由适配器宽容映射）。 */
export interface SupplierRawItem {
  id?: string;
  source?: string;
  name?: string;
  credit_code?: string;
  province?: string;
  city?: string;
  district?: string;
  industry?: string;
  legal_person?: string;
  registered_capital?: string;
  registered_capital_cny?: number | string | null;
  establish_date?: string;
  business_status?: string;
  company_type?: string;
  company_level?: string;
  address?: string;
  business_scope?: string;
  trade_types?: string;
  win_count?: number | string;
  win_amount_total?: number | string;
  first_win_date?: string;
  last_win_date?: string;
  main_region?: string;
  recent_wins?: Array<{
    project_no?: string;
    project_name?: string;
    region?: string;
    category?: string;
    winning_amount?: number | string | null;
    winning_date?: string;
  }>;
}

export interface SupplierRawOverview {
  filters?: { provinces?: string[]; industries?: string[] };
  total?: number;
  returned?: number;
  suppliers?: SupplierRawItem[];
}
