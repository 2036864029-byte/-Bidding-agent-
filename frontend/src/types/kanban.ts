/**
 * 商机看板领域契约（Domain Model）—— UI 唯一消费层。
 *
 * 维度口径（2026-09-14 用户拍板，2026-09-15 两次调整）：
 *  时间 = 月粒度；地区 = 省级（规范化省名）；类别 = `category`；
 *  **不做「进行中/已截止」状态**。
 *
 * ⚠️ **两个板块的数据来源不同，且必须如实标注**：
 *   - 「地区分布」与「行业走势」来自 **`trade_stat_fact`**（省级公共资源交易统计：
 *     32 省 × 10 月 × 11 类交易），**不是**本项目收录的项目 —— 收录数据 65% 压在
 *     安徽（采集偏向），画不成分布图。
 *   - 「行业占比」来自 `project_unified` 的 `category`（粒度较粗：学校/政府/货物）。
 *   - 这里的「行业」实际是**交易类型**（工程建设/政府采购/国有产权…）——
 *     项目表里根本没有行业字段（旧数据只有粗类，新数据 0%）。
 *
 * ⚠️ **没有金额维度**（用户拍板）：新数据只有 8 天，月度金额走势只有一个点。
 * ⚠️ **没有产品榜**（2026-09-15 撤掉）：产品明细频率分布无信号
 *    （最高 3 次、94% 只出现 1 次），任何排序都是"并列时按名称排"的假象。
 *
 * UI 只 import 本文件与 services/api.ts；后端原始字段（snake_case）经 adapters/ 隔离。
 */

/** 汇总指标卡 —— 全部为**数量口径**。 */
export interface KanbanSummary {
  /** 收录项目总数 */
  totalProjects: number;
  /** 近一月新增项目数 */
  recentProjects: number;
  /** 覆盖省级地区数 */
  regions: number;
  /** 覆盖行业数 */
  industries: number;
}

/** 筛选器可用选项（地区 / 行业 / 项目类别）。 */
export interface KanbanFilters {
  regions: string[];
  industries: string[];
  categories: string[];
}

/** 热力图单元格 —— 省级 × 项目量（来源：`trade_stat_fact`）。 */
export interface KanbanHeatmapCell {
  region: string;
  count: number;
}

/** 占比图项 —— 行业 × 项目量 / 占比（来源：`project_unified.category`）。 */
export interface KanbanCompositionItem {
  industry: string;
  count: number;
  /** 项目量占比 %（0~100） */
  pct: number;
}

/**
 * 行业走势 —— **交易类型** × 月（来源：`trade_stat_fact`）。
 *
 * ⚠️ `series` **最多 3 条**：多线属 all-pairs 图形（任意两条线都可能相邻），
 * dataviz 规范对此有硬约束 —— 超过 3 条只能折叠或拆小多图，绝不新造色相。
 * 后端已按总量取前 3。
 */
export interface KanbanIndustryTrend {
  /** 'YYYY-MM'，升序 */
  months: string[];
  /** 每条序列的 `values` 与 `months` 一一对应 */
  series: Array<{ name: string; values: number[] }>;
}

/** 看板一屏数据（一次聚合快照）。 */
export interface KanbanOverview {
  summary: KanbanSummary;
  filters: KanbanFilters;
  heatmap: KanbanHeatmapCell[];
  /**
   * 热力图取自哪个月（'YYYY-MM-DD'）。
   *
   * ⚠️ 热力图**不是**来自本项目收录的项目，而是 `trade_stat_fact`。
   * 面板必须**如实标注窗口与来源**，否则会被当成"我们收录的项目分布"。
   */
  heatmapMonth: string | null;
  composition: KanbanCompositionItem[];
  industryTrend: KanbanIndustryTrend;
  /** 数据快照时间（ISO datetime） */
  updatedAt: string;
}

/** 后端原始快照（未来只读端点返回形态；字段键可漂移，由适配器宽容映射）。 */
export interface KanbanRawOverview {
  summary: {
    total_projects: number;
    recent_projects?: number;
    regions?: number;
    industries?: number;
  };
  filters: {
    regions: string[];
    industries: string[];
    categories: string[];
  };
  heatmap: Array<{ region: string; count: number }>;
  /** 热力图所属月份（'YYYY-MM-DD'）；见 `KanbanOverview.heatmapMonth` */
  heatmap_month?: string | null;
  composition: Array<{ industry: string; count: number; pct: number }>;
  industry_trend?: {
    months?: string[];
    series?: Array<{ name?: string; values?: number[] }>;
  };
  updated_at?: string;
}
