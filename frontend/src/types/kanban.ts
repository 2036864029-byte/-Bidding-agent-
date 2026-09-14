/**
 * 商机看板领域契约（Domain Model）—— UI 唯一消费层。
 *
 * 维度口径（2026-09-14 用户拍板）：
 *  时间 = 月粒度，主用**中标日期**；地区 = 省级（规范化省名）；
 *  类别 = **项目类别**；**金额 = 万元**（新表口径，非元）；
 *  唯一性 = 项目编号 / 企业统一社会信用代码；**不做「进行中/已截止」状态**。
 *  看板以**走势图为主**，热力/占比为副。
 *
 * ⚠️ 单位不一致已知：现有 `bid_project` 表是「元」（对话模块 price_records 消费它），
 *    本模块新表是「万元」——跨模块比金额时必须显式换算。
 *
 * UI 只 import 本文件与 services/api.ts；后端原始字段（snake_case）经 adapters/ 隔离。
 */

/** 汇总指标卡（漏斗顶部）。金额单位：万元。 */
export interface KanbanSummary {
  totalProjects: number;
  /** 项目总金额（**万元**）；null = 缺失 */
  totalAmount: number | null;
  /** 平均竞争度（每项目平均投标家数，≥1） */
  avgCompetition: number;
}

/** 筛选器可用选项（地区 / 行业 / 项目类别）。 */
export interface KanbanFilters {
  regions: string[];
  industries: string[];
  categories: string[];
}

/** 热力图单元格 —— 省级 × 项目量 / 金额（万元）。 */
export interface KanbanHeatmapCell {
  region: string;
  count: number;
  /** 金额（**万元**） */
  amount: number;
}

/** 占比图项 —— 行业 × 项目量 / 金额占比。 */
export interface KanbanCompositionItem {
  industry: string;
  count: number;
  /** 金额（**万元**） */
  amount: number;
  /** 项目量占比 %（0~100） */
  pct: number;
}

/** 走势图点 —— 月 × 项目量 / 金额（万元）。 */
export interface KanbanTrendPoint {
  /** 'YYYY-MM'（月粒度，按中标日期） */
  month: string;
  count: number;
  /** 金额（**万元**） */
  amount: number;
}

/** 看板一屏数据（一次聚合快照）。 */
export interface KanbanOverview {
  summary: KanbanSummary;
  filters: KanbanFilters;
  heatmap: KanbanHeatmapCell[];
  composition: KanbanCompositionItem[];
  trend: KanbanTrendPoint[];
  /** 数据快照时间（ISO datetime） */
  updatedAt: string;
}

/** 后端原始快照（未来只读端点返回形态；字段键可漂移，由适配器宽容映射）。 */
export interface KanbanRawOverview {
  summary: {
    total_projects: number;
    /** 万元 */
    total_amount: number | null;
    avg_competition?: number;
  };
  filters: {
    regions: string[];
    industries: string[];
    categories: string[];
  };
  /** 万元 */
  heatmap: Array<{ region: string; count: number; amount: number }>;
  /** 万元 */
  composition: Array<{ industry: string; count: number; amount: number; pct: number }>;
  /** 万元 */
  trend: Array<{ month: string; count: number; amount: number }>;
  updated_at?: string;
}
