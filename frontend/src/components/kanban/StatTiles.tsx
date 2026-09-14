import { fmtAmountWan, fmtInt } from '@/lib/format';
import type { KanbanSummary } from '@/types/kanban';

/**
 * StatTiles —— 汇总指标卡（figures 形态：不是所有东西都该画成图）。
 *
 * 规范要点：大号独立数字用**比例数字**（不加 tabular-nums，等宽会让大字号显得松）；
 * 文字用文本色，不穿序列色。
 */
export function StatTiles({ summary }: { summary: KanbanSummary }) {
  const amount = fmtAmountWan(summary.totalAmount);

  const tiles = [
    { label: '收录项目', value: fmtInt(summary.totalProjects), unit: '个' },
    { label: '项目总金额', value: amount.value, unit: amount.unit },
    { label: '平均竞争度', value: summary.avgCompetition.toFixed(1), unit: '家 / 项' },
  ];

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {tiles.map((t) => (
        <div
          key={t.label}
          className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] px-5 py-4"
        >
          <p className="text-[12px] text-[var(--color-neutral)]">{t.label}</p>
          <p className="mt-1.5 flex items-baseline gap-1.5">
            <span className="text-[28px] font-semibold leading-none tracking-[-0.02em] text-[var(--color-ink)]">
              {t.value}
            </span>
            {t.unit && (
              <span className="text-[13px] text-[var(--color-neutral)]">{t.unit}</span>
            )}
          </p>
        </div>
      ))}
    </div>
  );
}
