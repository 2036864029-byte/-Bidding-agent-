/**
 * 智能询价领域契约（Domain Model）。
 *
 * 基准：规划方案 §4.1 的 InquiryItem / InquirySummary；本契约增补
 * 偏离高亮所需字段（budgetThreshold / deviationRate）。金额单位一律为元（§5.3 口径）。
 *
 * ⚠️ 「询价单 / 对比明细」是未来域模型：当前 /chat 的 price_records 返回的是
 * 「中标记录表」，不是本契约的 InquirySummary（见设计文档 §1 修正 4）。
 */

/** 供应商报价（单项）。 */
export interface InquiryQuote {
  supplierId: string;
  supplierName: string;
  /** 报价单价（元） */
  unitPrice: number;
  /** 报价总价（元）= 单价 × 目标数量 */
  totalPrice: number;
  /** 是否为推荐方案 */
  isRecommended: boolean;
  /** 偏离度 %：相对基准单价（负 = 低于基准，正 = 超基准） */
  deviationRate: number;
  /** 推荐理由 / 备注 */
  reason?: string;
}

/** 询价单项。 */
export interface InquiryItem {
  itemId: string;
  itemName: string;
  /** 规格型号 */
  specifications: string;
  unit: string;
  targetQuantity: number;
  /** 内部测算基准单价（元） */
  benchmarkUnitPrice: number;
  /** 偏离阈值（单价，元）—— 超阈值 → 偏离高亮 */
  budgetThreshold: number;
  quotes: InquiryQuote[];
}

export type InquiryStatus = 'DRAFT' | 'IN_PROGRESS' | 'COMPLETED';

/** 询价单。 */
export interface InquirySummary {
  inquiryId: string;
  title: string;
  status: InquiryStatus;
  /** 预算总额（元） */
  totalBudget: number;
  createdAt: string;
  items: InquiryItem[];
}

/** 原始单项报价（字段键双写：规划方案 §4.2 的宽容映射示例）。 */
export interface InquiryRawQuote {
  supplier_id?: string;
  supplierId?: string;
  supplier_name?: string;
  supplierName?: string;
  price?: number;
  unitPrice?: number;
  is_best?: boolean;
  isRecommended?: boolean;
  deviation?: number;
  deviationRate?: number;
  reason?: string;
}

/** 原始询价单项。 */
export interface InquiryRawItem {
  item_id?: string;
  itemId?: string;
  name?: string;
  itemName?: string;
  spec?: string;
  specifications?: string;
  unit?: string;
  qty?: number;
  targetQuantity?: number;
  base_price?: number;
  benchmarkUnitPrice?: number;
  budget_threshold?: number;
  supplier_quotes?: InquiryRawQuote[];
  /** 备用键名（规划方案 §4.2 宽容映射示例：`item.supplier_quotes || item.quotes`） */
  quotes?: InquiryRawQuote[];
}

/** 后端原始询价单（未来询价业务提供；字段键可漂移）。 */
export interface InquiryRawSummary {
  id?: string;
  inquiryId?: string;
  title?: string;
  status?: string;
  budget?: number;
  totalBudget?: number;
  created_at?: string;
  createdAt?: string;
  items?: InquiryRawItem[];
}
