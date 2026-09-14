/**
 * 供应商全景领域契约（Domain Model）。
 *
 * 评分口径 ⚠️ 2026-09-14 用户拍板：**由后端给**（前端只透传，不做任何评分计算）——
 * mock 里的 `score` 与 `radar.dimensions` 即"后端会返回的字段"，雷达轴现定为
 * 技术能力 / 交付能力 / 售后服务 / 价格竞争力 / 财务状况（后端按此计算）。
 * 唯一键 = 统一社会信用代码；**汇总金额单位「万元」**；地区省级。
 */

/** 雷达图维度（0~100）。 */
export interface RadarDimension {
  label: string;
  value: number;
}

export interface SupplierRadar {
  dimensions: RadarDimension[];
}

/** 中标历史价格分布（详情 Modal）。 */
export interface SupplierPriceHistory {
  year: number;
  /** 年均中标金额（**万元**） */
  avgAmount: number;
  /** 年中标次数 */
  count: number;
}

/** 供应商履约统计（详情 Modal）。 */
export interface SupplierStats {
  totalBids: number;
  wins: number;
  /** 参与投标成功率 %（0~100） */
  bidWinRate: number;
  /** 合作年限 */
  activeYears: number;
  priceHistory: SupplierPriceHistory[];
}

/** 供应商卡片。 */
export interface Supplier {
  id: string;
  /** 统一社会信用代码（唯一键） */
  creditCode: string;
  name: string;
  /** 省级 */
  region: string;
  /** 主营类别 */
  categories: string[];
  /** 资质标签 */
  qualifications: string[];
  /** 历史合作评分 0~100（口径待定，见文件头注释） */
  score: number;
  radar: SupplierRadar;
  /** 风险预警标签 */
  riskTags: string[];
  /** 核心履约能力（一句话） */
  coreCapability: string;
  stats: SupplierStats;
}

/** 供应商一屏 + 筛选器选项。 */
export interface SupplierOverview {
  filters: {
    qualifications: string[];
    categories: string[];
    regions: string[];
    /** 历史合作评分筛选区间 */
    scoreRange: [number, number];
  };
  suppliers: Supplier[];
}

/** 后端原始供应商记录（未来 read 端点返回形态；字段键可漂移）。 */
export interface SupplierRawItem {
  id?: string;
  credit_code?: string;
  name?: string;
  region?: string;
  categories?: string[];
  qualifications?: string[];
  score?: number;
  core_capability?: string;
  risk_tags?: string[];
  radar?: { dimensions?: Array<{ label?: string; value?: number }> };
  stats?: {
    total_bids?: number;
    wins?: number;
    bid_win_rate?: number;
    active_years?: number;
    price_history?: Array<{ year?: number; avg_amount?: number; count?: number }>;
  };
}

/** 后端原始供应商全景（含筛选器选项）。 */
export interface SupplierRawOverview {
  filters?: {
    qualifications?: string[];
    categories?: string[];
    regions?: string[];
    score_range?: [number, number];
  };
  suppliers?: SupplierRawItem[];
}
