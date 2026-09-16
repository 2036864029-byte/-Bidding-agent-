import { TrendingUp } from 'lucide-react';
import { fmtAmountWan, fmtWanFixed } from '@/lib/format';
import type { ReferenceStats } from '@/types/inquiry';

/**
 * ProjectReferenceSummary —— 项目概况参考的金额区间卡（万元口径）。
 *
 * 与聊天派生的 `ReferenceSummary`（元口径）**并行**：这是独立端点
 * `/inquiry/reference` 的结果，金额统一万元（方案 §7，字段名 `_wan` 即契约）。
 * 区间文案固定 2 位小数（`fmtWanFixed`），四档指标走紧凑展示（`fmtAmountWan`）。
 */

export function ProjectReferenceSummary({ stats }: { stats: ReferenceStats | null }) {
  if (!stats) return null;
  return (
    <section className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="mono-label flex items-center gap-1.5">
          <TrendingUp className="h-3 w-3" aria-hidden />
          金额参考
        </p>
        <span className="shrink-0 rounded-full border border-[var(--color-rule-2)] px-2.5 py-1 text-[12px] text-[var(--color-muted)]">
          {stats.sampleCount} 个相似项目
        </span>
      </div>

      <p className="mt-4 text-[26px] font-semibold leading-none tracking-[-0.02em] text-[var(--color-ink)]">
        {fmtWanFixed(stats.minWan)} ~ {fmtWanFixed(stats.maxWan)}
      </p>

      <dl className="mt-4 grid grid-cols-4 gap-3 border-t border-[var(--color-rule)] pt-3.5">
        {(
          [
            ['最低', stats.minWan],
            ['中位', stats.medianWan],
            ['平均', stats.avgWan],
            ['最高', stats.maxWan],
          ] as const
        ).map(([label, value]) => {
          const v = fmtAmountWan(value);
          return (
            <div key={label}>
              <dt className="text-[12px] text-[var(--color-neutral)]">{label}</dt>
              <dd className="mt-0.5 flex items-baseline gap-1">
                <span className="tnum text-[14px] font-medium text-[var(--color-ink-2)]">{v.value}</span>
                <span className="text-[11.5px] text-[var(--color-neutral)]">{v.unit}</span>
              </dd>
            </div>
          );
        })}
      </dl>
      <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-neutral)]">
        金额为历史中标公告的 `award_amount`（万元），仅作定价参考，非正式预算依据。
      </p>
    </section>
  );
}
