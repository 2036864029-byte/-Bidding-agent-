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
