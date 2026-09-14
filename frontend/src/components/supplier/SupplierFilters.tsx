import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { isAllSelected, toggleValue, type SupplierFilterState } from '@/lib/supplier';

/**
 * SupplierFilters —— 多维标签筛选行（设计文档 §4.5）。
 *
 * 规范要点：筛选器**一整行置顶**、不进任何卡片内部，所有面板对同一份切片重绘。
 * 语义：维度内多选为 OR，维度之间为 AND；未选任何标签 = 该维度不限制。
 */
export function SupplierFilters({
  filters,
  state,
  onChange,
  resultCount,
}: {
  filters: {
    qualifications: string[];
    categories: string[];
    regions: string[];
  };
  state: SupplierFilterState;
  onChange: (next: SupplierFilterState) => void;
  resultCount: number;
}) {
  const set = (patch: Partial<SupplierFilterState>) => onChange({ ...state, ...patch });

  return (
    <section className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] px-5 py-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="mono-label">筛选</span>
        <span className="text-[12px] text-[var(--color-neutral)]">
          符合条件 <span className="tnum font-medium text-[var(--color-ink-2)]">{resultCount}</span> 家
        </span>
      </div>

      <div className="space-y-2.5">
        <TagGroup
          label="资质"
          options={filters.qualifications}
          selected={state.qualifications}
          onToggle={(v) => set({ qualifications: toggleValue(state.qualifications, v) })}
        />
        <TagGroup
          label="主营类别"
          options={filters.categories}
          selected={state.categories}
          onToggle={(v) => set({ categories: toggleValue(state.categories, v) })}
        />
        <TagGroup
          label="地区"
          options={filters.regions}
          selected={state.regions}
          onToggle={(v) => set({ regions: toggleValue(state.regions, v) })}
        />
        <TagGroup
          label="历史合作评分"
          options={['90 分以上', '85 分以上', '80 分以上']}
          selected={scoreSelected(state.scoreRange)}
          onToggle={(v) => {
            const current = scoreSelected(state.scoreRange)[0];
            set({ scoreRange: current === v ? [0, 100] : scoreRangeOf(v) });
          }}
        />
      </div>

      <div className="mt-3 flex justify-end">
        <button
          type="button"
          onClick={() => onChange({ qualifications: [], categories: [], regions: [], scoreRange: [0, 100] })}
          className="text-[12px] text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:text-[var(--color-accent)]"
        >
          重置筛选
        </button>
      </div>
    </section>
  );
}

const SCORE_BANDS: Array<{ label: string; range: [number, number] }> = [
  { label: '90 分以上', range: [90, 100] },
  { label: '85 分以上', range: [85, 100] },
  { label: '80 分以上', range: [80, 100] },
];

const scoreSelected = (range: [number, number]): string[] => {
  const hit = SCORE_BANDS.find((b) => b.range[0] === range[0] && b.range[1] === range[1]);
  return hit ? [hit.label] : [];
};
const scoreRangeOf = (label: string): [number, number] =>
  SCORE_BANDS.find((b) => b.label === label)?.range ?? [0, 100];

function TagGroup({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string;
  options: string[];
  selected: string[];
  onToggle: (v: string) => void;
}) {
  const all = isAllSelected(selected);
  return (
    <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
      <span className="w-[6.5rem] shrink-0 pt-1 text-[12.5px] text-[var(--color-neutral)]">
        {label}
      </span>
      <div className="flex flex-wrap gap-1.5">
        <Chip active={all} onClick={() => selected.forEach(onToggle)}>
          全部
        </Chip>
        {options.map((o) => (
          <Chip key={o} active={selected.includes(o)} onClick={() => onToggle(o)}>
            {o}
          </Chip>
        ))}
      </div>
    </div>
  );
}

function Chip({
  children,
  active,
  onClick,
}: {
  children: ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'rounded-full border px-2.5 py-1 text-[12.5px] transition-colors duration-[var(--dur-micro)] ease-out',
        active
          ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
          : 'border-[var(--color-rule-2)] text-[var(--color-muted)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]'
      )}
    >
      {children}
    </button>
  );
}
