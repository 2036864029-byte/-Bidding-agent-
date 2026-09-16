import { num, str } from '@/adapters/helpers';
import type { KanbanOverview, KanbanRawOverview } from '@/types/kanban';

/** 行业走势里一条序列的宽容映射（name 空则丢弃，values 按 months 长度补齐）。 */
function adaptSeries(
  raw: KanbanRawOverview['industry_trend'],
  monthCount: number
): Array<{ name: string; values: number[] }> {
  const list = raw?.series ?? [];
  return list
    .map((s) => {
      const values = (s.values ?? []).map((v) => num(v));
      // 长度对不齐会导致图上错位 —— 补齐/截断到 months 长度
      while (values.length < monthCount) values.push(0);
      return { name: str(s.name), values: values.slice(0, monthCount) };
    })
    .filter((s) => s.name !== '');
}

/**
 * kanbanAdapter —— 商机看板 raw → Domain。
 *
 * 宽容点：缺省汇总字段回落、缺数组回落 `[]`、行业走势序列与月份长度对齐。
 * 维度：**纯数量口径**（金额按用户决定移除，理由见 types/kanban.ts 文件头）。
 */
export function kanbanAdapter(raw?: KanbanRawOverview | null): KanbanOverview {
  const data = raw ?? ({} as KanbanRawOverview);
  const summary = data.summary ?? ({} as KanbanRawOverview['summary']);
  const filters = data.filters ?? ({} as KanbanRawOverview['filters']);
  const months = (data.industry_trend?.months ?? []).map((m) => str(m));

  return {
    summary: {
      totalProjects: num(summary.total_projects),
      recentProjects: num(summary.recent_projects),
      regions: num(summary.regions),
      industries: num(summary.industries),
    },
    filters: {
      regions: filters.regions ?? [],
      industries: filters.industries ?? [],
      categories: filters.categories ?? [],
    },
    heatmap: (data.heatmap ?? []).map((c) => ({
      region: str(c.region),
      count: num(c.count),
    })),
    heatmapMonth: data.heatmap_month ? str(data.heatmap_month) : null,
    composition: (data.composition ?? []).map((c) => ({
      industry: str(c.industry),
      count: num(c.count),
      pct: num(c.pct),
    })),
    industryTrend: { months, series: adaptSeries(data.industry_trend, months.length) },
    updatedAt: str(data.updated_at),
  };
}
