import { Calculator } from 'lucide-react';
import { fmtAmountYuan } from '@/lib/format';
import { fmtReferenceRange } from '@/lib/inquiry';
import type { QuoteReference } from '@/types/inquiry';

/**
 * ReferenceSummary —— 报价参考的区间卡（右栏顶部）。
 *
 * 三档价位用 `fmtAmountYuan`（**元**口径）各自成列，另外给一条连续区间文案
 * ——两者并存是有意的：区间文案回答"大概多少钱"，三档回答"下限/中位/上限各是多少"。
 */
export function ReferenceSummary({ reference }: { reference: QuoteReference }) {
  return (
    <section className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="mono-label mb-1 flex items-center gap-1.5">
            <Calculator className="h-3 w-3" aria-hidden />
            报价参考
          </p>
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
            {reference.title}
          </h2>
        </div>
        <span className="shrink-0 rounded-full border border-[var(--color-rule-2)] px-2.5 py-1 text-[12px] text-[var(--color-muted)]">
          {reference.sampleCount} 个相似项目
        </span>
      </div>

      <p className="mt-4 text-[26px] font-semibold leading-none tracking-[-0.02em] text-[var(--color-ink)]">
        {fmtReferenceRange(reference)}
      </p>

      <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-[var(--color-rule)] pt-3.5">
        {(
          [
            ['最低', reference.minAmount],
            ['中位', reference.medianAmount],
            ['最高', reference.maxAmount],
          ] as const
        ).map(([label, amount]) => {
          const v = fmtAmountYuan(amount);
          return (
            <div key={label}>
              <dt className="text-[12px] text-[var(--color-neutral)]">{label}</dt>
              <dd className="mt-0.5 flex items-baseline gap-1">
                <span className="tnum text-[15px] font-medium text-[var(--color-ink-2)]">
                  {v.value}
                </span>
                <span className="text-[11.5px] text-[var(--color-neutral)]">{v.unit}</span>
              </dd>
            </div>
          );
        })}
      </dl>

      {reference.missingAmountCount > 0 && (
        <p className="mt-3 text-[12px] text-[var(--color-neutral)]">
          另有 {reference.missingAmountCount} 条记录未公开中标金额，未计入区间。
        </p>
      )}
    </section>
  );
}
