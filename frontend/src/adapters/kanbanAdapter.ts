import { num, str } from '@/adapters/helpers';
import type { KanbanOverview, KanbanRawOverview } from '@/types/kanban';

/**
 * kanbanAdapter —— 商机看板 raw → Domain。
 *
 * 宽容点：缺省汇总字段回落、金额 `null` 保留缺失语义（0 = 零，二者不混）。
 * 金额单位：**万元**（新表口径，见 types/kanban.ts 文件头）。
 */
export function kanbanAdapter(raw?: KanbanRawOverview | null): KanbanOverview {
  const data = raw ?? ({} as KanbanRawOverview);
  const summary = data.summary ?? ({} as KanbanRawOverview['summary']);
  const filters = data.filters ?? ({} as KanbanRawOverview['filters']);

  return {
    summary: {
      totalProjects: num(summary.total_projects),
      // 金额：null 表示缺失（区别于 0 = 零元）
      totalAmount:
        summary.total_amount === null || summary.total_amount === undefined
          ? null
          : num(summary.total_amount),
      avgCompetition: num(summary.avg_competition, 1),
    },
    filters: {
      regions: filters.regions ?? [],
      industries: filters.industries ?? [],
      categories: filters.categories ?? [],
    },
    heatmap: (data.heatmap ?? []).map((c) => ({
      region: str(c.region),
      count: num(c.count),
      amount: num(c.amount),
    })),
    composition: (data.composition ?? []).map((c) => ({
      industry: str(c.industry),
      count: num(c.count),
      amount: num(c.amount),
      pct: num(c.pct),
    })),
    trend: (data.trend ?? []).map((t) => ({
      month: str(t.month),
      count: num(t.count),
      amount: num(t.amount),
    })),
    updatedAt: str(data.updated_at),
  };
}
