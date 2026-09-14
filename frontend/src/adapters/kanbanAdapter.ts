import { num, str } from '@/adapters/helpers';
import type { KanbanOverview, KanbanRawOverview } from '@/types/kanban';

/**
 * kanbanAdapter —— 商机看板 raw → Domain。
 *
 * 宽容点：缺省汇总字段回落、可选状态字段缺省时按总量兜底、
 * 金额 `null` 保留缺失语义（0 = 零元，二者不混）。
 */
export function kanbanAdapter(raw?: KanbanRawOverview | null): KanbanOverview {
  const data = raw ?? ({} as KanbanRawOverview);
  const summary = data.summary ?? ({} as KanbanRawOverview['summary']);
  const filters = data.filters ?? ({} as KanbanRawOverview['filters']);
  const totalProjects = num(summary.total_projects);

  return {
    summary: {
      totalProjects,
      // 金额：null 表示缺失（区别于 0 = 零元）
      totalAmount: summary.total_amount === null || summary.total_amount === undefined
        ? null
        : num(summary.total_amount),
      activeProjects: num(summary.active_projects, totalProjects),
      closedProjects: num(summary.closed_projects),
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
