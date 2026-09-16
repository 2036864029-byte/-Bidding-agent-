import { fmtInt } from '@/lib/format';
import type { KanbanHeatmapCell } from '@/types/kanban';

/**
 * RegionHeatmap —— 省级热力（单色相顺序色阶，magnitude 编码）。
 *
 * 规范要点：顺序色阶 = **一个色相，浅→深**（绝不彩虹）；离散格的色阶地板取
 * 2.06:1（最浅一档，避免融进纸面）；格内文字按填充明度取墨/白两色
 * （唯一允许文字进填充色的场景，保证始终可读）；格与格之间用 **2px 面间隙**
 * 分隔，不画描边。
 */

interface Bin {
  min: number;
  fill: string;
  ink: string;
}

/** 分档阈值（固定，颜色含义不随数据漂移）；浅→深对应量级递增。 */
const BINS: Bin[] = [
  { min: 150, fill: 'var(--seq-4)', ink: '#ffffff' },
  { min: 100, fill: 'var(--seq-3)', ink: '#ffffff' },
  { min: 50, fill: 'var(--seq-2)', ink: 'var(--color-ink)' },
  { min: 0, fill: 'var(--seq-1)', ink: 'var(--color-ink)' },
];

const binOf = (count: number): Bin => BINS.find((b) => count >= b.min) ?? BINS[BINS.length - 1];

export function RegionHeatmap({ cells }: { cells: KanbanHeatmapCell[] }) {
  const sorted = [...cells].sort((a, b) => b.count - a.count);

  return (
    <div>
      <div className="grid grid-cols-2 gap-0.5 sm:grid-cols-3 lg:grid-cols-4">
        {sorted.map((c) => {
          const bin = binOf(c.count);
          return (
            <div
              key={c.region}
              className="flex flex-col justify-between rounded-lg px-3 py-2.5"
              style={{ background: bin.fill, color: bin.ink }}
              title={`${c.region}：${fmtInt(c.count)} 个项目`}
            >
              <span className="text-[12px] font-medium opacity-90">{c.region}</span>
              <span className="mt-1.5 text-[17px] font-semibold leading-none">{fmtInt(c.count)}</span>
            </div>
          );
        })}
      </div>

      {/* 色阶图例（连续量级必须有刻度说明）*/}
      <div className="mt-3 flex items-center justify-end gap-2">
        <span className="mono-label text-[10px]">项目数</span>
        <span className="text-[11px] text-[var(--color-neutral)]">少</span>
        <span className="flex overflow-hidden rounded-[3px]">
          {['var(--seq-1)', 'var(--seq-2)', 'var(--seq-3)', 'var(--seq-4)'].map((f) => (
            <span key={f} className="h-2.5 w-5" style={{ background: f }} />
          ))}
        </span>
        <span className="text-[11px] text-[var(--color-neutral)]">多</span>
      </div>
    </div>
  );
}
