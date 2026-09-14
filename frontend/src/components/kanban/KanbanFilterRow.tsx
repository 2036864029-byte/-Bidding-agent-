import type { KanbanFilters as Filters } from '@/types/kanban';

const ALL = '全部';

/**
 * KanbanFilters —— 筛选行（一整行置顶，作用于其下所有面板）。
 *
 * 规范要点：**筛选器不进图表卡内部**，一张筛选行统辖全部面板，所有图对同一份切片重绘。
 * 地区作用于热力图、行业作用于占比图；汇总与走势为全局聚合（需后端聚合端点才能下钻，
 * 见设计文档 §5「三模块需新增只读端点」）。
 */
export function KanbanFilterRow({
  filters,
  region,
  industry,
  onRegionChange,
  onIndustryChange,
  updatedAt,
}: {
  filters: Filters;
  region: string;
  industry: string;
  onRegionChange: (v: string) => void;
  onIndustryChange: (v: string) => void;
  updatedAt: string;
}) {
  const stamp = updatedAt ? new Date(updatedAt).toLocaleString('zh-CN', { hour12: false }) : '—';

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] px-4 py-3">
      <span className="mono-label">数据范围</span>

      <FilterSelect label="地区" value={region} options={filters.regions} onChange={onRegionChange} />
      <FilterSelect
        label="行业"
        value={industry}
        options={filters.industries}
        onChange={onIndustryChange}
      />

      <span className="ml-auto text-[12px] text-[var(--color-neutral)]">快照时间 {stamp}</span>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-1.5 text-[13px] text-[var(--color-muted)]">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-[var(--color-rule-2)] bg-[var(--color-paper)] px-2 py-1 text-[13px] text-[var(--color-ink)] transition-colors duration-[var(--dur-micro)] ease-out hover:border-[var(--color-accent)]"
      >
        <option value={ALL}>{ALL}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

export { ALL as FILTER_ALL };
