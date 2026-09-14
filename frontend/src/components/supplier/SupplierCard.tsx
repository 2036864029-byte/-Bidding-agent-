import { ArrowUpRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { RiskTags } from '@/components/supplier/RiskTags';
import { ScoreMeter } from '@/components/supplier/ScoreMeter';
import { SupplierRadar } from '@/components/supplier/SupplierRadar';
import { canAddCompare } from '@/lib/supplier';
import { cn } from '@/lib/utils';
import type { Supplier } from '@/types/supplier';

/**
 * SupplierCard —— 网格化供应商卡（设计文档 §4.5）。
 *
 * 卡片内是「一家」供应商 —— 单系列，所以雷达用品牌色、**不需要图例**。
 * 多色对比留给对比面板（那里才有「多家」）。
 */
export function SupplierCard({
  supplier,
  selectedIds,
  onToggleCompare,
  onOpenDetail,
}: {
  supplier: Supplier;
  selectedIds: string[];
  onToggleCompare: (id: string) => void;
  onOpenDetail: (supplier: Supplier) => void;
}) {
  const selected = selectedIds.includes(supplier.id);
  const selectable = canAddCompare(selectedIds, supplier.id);

  return (
    <article
      className={cn(
        'flex flex-col rounded-2xl border bg-[var(--color-paper)] p-5 transition-colors duration-[var(--dur-short)] ease-out',
        selected ? 'border-[var(--color-accent)]' : 'border-[var(--color-rule)]'
      )}
    >
      <header className="mb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-[14.5px] font-semibold leading-snug tracking-[-0.01em] text-[var(--color-ink)]">
              {supplier.name}
            </h3>
            <p className="mono-label mt-1">
              {supplier.region} · {supplier.creditCode}
            </p>
          </div>
          <label
            className={cn(
              'flex shrink-0 cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-[12px] transition-colors duration-[var(--dur-micro)] ease-out',
              selected
                ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                : 'border-[var(--color-rule-2)] text-[var(--color-neutral)] hover:border-[var(--color-accent)]',
              !selectable && 'cursor-not-allowed opacity-40'
            )}
            title={selectable ? '加入对比' : '对比最多 3 家'}
          >
            <input
              type="checkbox"
              className="sr-only"
              checked={selected}
              disabled={!selectable}
              onChange={() => onToggleCompare(supplier.id)}
            />
            对比
          </label>
        </div>
        <ScoreMeter score={supplier.score} className="mt-3" />
      </header>

      <SupplierRadar suppliers={[supplier]} height={152} />

      <p className="mt-3 text-[12.5px] leading-relaxed text-[var(--color-muted)]">
        {supplier.coreCapability}
      </p>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {supplier.categories.map((c) => (
          <Badge key={c} variant="accent">
            {c}
          </Badge>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        <RiskTags tags={supplier.riskTags} />
      </div>

      <footer className="mt-4 border-t border-[var(--color-rule)] pt-3">
        <button
          type="button"
          onClick={() => onOpenDetail(supplier)}
          className="inline-flex items-center gap-1 text-[13px] text-[var(--color-accent)] transition-colors duration-[var(--dur-micro)] ease-out hover:underline"
        >
          查看详情
          <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
        </button>
      </footer>
    </article>
  );
}
