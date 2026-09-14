import { SlidersHorizontal } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { DeviationBadge } from '@/components/inquiry/DeviationBadge';
import { fmtInt } from '@/lib/format';
import { computeDeviation, deviationLevel, itemStats, sortQuotes } from '@/lib/inquiry';
import type { InquiryItem, InquiryQuote } from '@/types/inquiry';

/**
 * ItemCompareCard —— 单项多方报价对比表 + 偏离高亮。
 *
 * 行序按单价升序（最便宜的在最上面）；超阈值的行整行加警示底 + 加徽章 ——
 * **不是只靠颜色**：底色只是强化，徽章带图标与文字，去掉颜色信息依然完整。
 */
export function ItemCompareCard({
  item,
  onAdjust,
}: {
  item: InquiryItem;
  onAdjust: (quote: InquiryQuote) => void;
}) {
  const quotes = sortQuotes(item.quotes);
  const stats = itemStats(item);

  return (
    <section className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-5">
      <header className="mb-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
            {item.itemName}
          </h3>
          {stats.overThreshold > 0 ? (
            <Badge variant="danger" className="whitespace-nowrap">
              {stats.overThreshold} 项报价超阈值
            </Badge>
          ) : (
            <Badge variant="success" className="whitespace-nowrap">
              全部在阈值内
            </Badge>
          )}
        </div>
        <p className="mt-1 text-[13px] text-[var(--color-muted)]">
          {item.specifications} · 数量 {fmtInt(item.targetQuantity)} {item.unit}
        </p>
        <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1.5 text-[12.5px]">
          <div className="flex items-center gap-1.5">
            <dt className="text-[var(--color-neutral)]">内部基准单价</dt>
            <dd className="tnum font-medium text-[var(--color-ink-2)]">
              ¥{fmtInt(item.benchmarkUnitPrice)}
            </dd>
          </div>
          <div className="flex items-center gap-1.5">
            <dt className="text-[var(--color-neutral)]">偏离阈值</dt>
            <dd className="tnum font-medium text-[var(--color-ink-2)]">
              ¥{fmtInt(item.budgetThreshold)}
            </dd>
          </div>
          <div className="flex items-center gap-1.5">
            <dt className="text-[var(--color-neutral)]">报价家数</dt>
            <dd className="tnum font-medium text-[var(--color-ink-2)]">{stats.quoteCount}</dd>
          </div>
        </dl>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-[var(--color-rule)]">
              <th scope="col" className="px-3 py-2 text-left font-medium text-[var(--color-neutral)]">
                供应商
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium text-[var(--color-neutral)]">
                单价
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium text-[var(--color-neutral)]">
                总价
              </th>
              <th scope="col" className="px-3 py-2 text-left font-medium text-[var(--color-neutral)]">
                偏离
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium text-[var(--color-neutral)]">
                <span className="sr-only">操作</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {quotes.map((q) => {
              const level = deviationLevel(q.unitPrice, item);
              const over = level === 'over';
              return (
                <tr
                  key={q.supplierId}
                  className={`border-b border-[var(--color-rule)] last:border-0 ${
                    over ? 'bg-[oklch(97%_0.025_25)]' : ''
                  }`}
                >
                  <td className="px-3 py-2.5 text-[var(--color-ink-2)]">
                    <span className="flex items-center gap-2">
                      {q.supplierName}
                      {q.isRecommended && (
                        <Badge variant="accent" className="shrink-0 whitespace-nowrap">
                          推荐
                        </Badge>
                      )}
                    </span>
                  </td>
                  <td className="tnum px-3 py-2.5 text-right font-medium text-[var(--color-ink)]">
                    ¥{fmtInt(q.unitPrice)}
                  </td>
                  <td className="tnum px-3 py-2.5 text-right text-[var(--color-ink-2)]">
                    ¥{fmtInt(q.totalPrice)}
                  </td>
                  <td className="px-3 py-2.5">
                    <DeviationBadge
                      level={level}
                      rate={computeDeviation(q.unitPrice, item.benchmarkUnitPrice)}
                    />
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <button
                      type="button"
                      onClick={() => onAdjust(q)}
                      className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[12px] text-[var(--color-accent)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-accent-soft)]"
                    >
                      <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />
                      调价
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
