/**
 * 商机看板领域契约（Domain Model）—— UI 唯一消费层。
 *
 * 维度口径（对齐《前端架构与UI版面设计方案_20260914》§5.3 建议默认，数据入库前需与产品确认）：
 *  时间 = 月粒度（保留 'YYYY-MM'）；地区 = 省级（规范化编码）；
 *  类别 = 项目类别；金额 = 元（null=缺失，0=零元）。
 *
 * UI 只 import 本文件与 services/api.ts；后端原始字段（snake_case）经 adapters/ 隔离。
 */

/** 汇总指标卡（漏斗顶部）。 */
export interface KanbanSummary {
  totalProjects: number;
  /** 项目总金额（元）；null = 缺失 */
  totalAmount: number | null;
  /** 进行中项目数（数据无状态语义时 = totalProjects） */
  activeProjects: number;
  /** 已截止项目数 */
  closedProjects: number;
  /** 平均竞争度（每项目平均投标家数，≥1） */
  avgCompetition: number;
}

/** 筛选器可用选项（地区 / 行业 / 项目类别）。 */
export interface KanbanFilters {
  regions: string[];
  industries: string[];
  categories: string[];
}

/** 热力图单元格 —— 省级 × 项目量 / 金额。 */
export interface KanbanHeatmapCell {
  region: string;
  count: number;
  amount: number;
}

/** 占比图项 —— 行业 × 项目量 / 金额占比。 */
export interface KanbanCompositionItem {
  industry: string;
  count: number;
  amount: number;
  /** 项目量占比 %（0~100） */
  pct: number;
}

/** 走势图点 —— 月 × 项目量 / 金额。 */
export interface KanbanTrendPoint {
  month: string;
  count: number;
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
    total_amount: number | null;
    active_projects?: number;
    closed_projects?: number;
    avg_competition?: number;
  };
  filters: {
    regions: string[];
    industries: string[];
    categories: string[];
  };
  heatmap: Array<{ region: string; count: number; amount: number }>;
  composition: Array<{ industry: string; count: number; amount: number; pct: number }>;
  trend: Array<{ month: string; count: number; amount: number }>;
  updated_at?: string;
}
