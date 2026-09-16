import { MapPin, Trophy } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { fmtAmountYuan, fmtInt } from '@/lib/format';
import type { Supplier } from '@/types/supplier';

/**
 * SupplierCard —— 供应商卡片：**工商基本信息 + 中标概况**。
 *
 * 2026-09-15 重塑：去掉了评分/雷达/风险标签（无数据源），改为可核验的字段。
 * CCGP 来源的企业没有工商信息，卡片会显示「暂无工商信息」而不是留空或填假值。
 */
export function SupplierCard({
  supplier,
  onOpenDetail,
}: {
  supplier: Supplier;
  onOpenDetail: (s: Supplier) => void;
}) {
  const amount = fmtAmountYuan(supplier.winAmountTotal > 0 ? supplier.winAmountTotal : null);
  const hasProfile = Boolean(supplier.province || supplier.industry || supplier.legalPerson);

  return (
    <article className="flex flex-col rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-5">
      <header className="min-w-0">
        <h3
          className="truncate text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]"
          title={supplier.name}
        >
          {supplier.name}
        </h3>
        <p className="mono-label mt-1 truncate">
          {supplier.creditCode ?? '统一社会信用代码未收录'}
        </p>
      </header>

      {/* 工商信息：有则展示，无则**明说没有**（CCGP 侧 1,058 家都属于后者） */}
      {hasProfile ? (
        <dl className="mt-3.5 flex flex-wrap gap-1.5">
          {supplier.province && <Tag>{supplier.province}</Tag>}
          {supplier.industry && <Tag>{supplier.industry}</Tag>}
          {supplier.companyLevel && <Tag>{supplier.companyLevel}</Tag>}
          {supplier.businessStatus && <Tag>{supplier.businessStatus}</Tag>}
        </dl>
      ) : (
        <p className="mt-3.5 rounded-lg border border-dashed border-[var(--color-rule-2)] px-3 py-2 text-[12px] text-[var(--color-neutral)]">
          暂无工商信息（该企业来自新采集公告，尚未并入工商档案）
        </p>
      )}

      <div className="mt-4 flex items-center gap-4 border-t border-[var(--color-rule)] pt-3.5">
        <div className="flex items-center gap-1.5">
          <Trophy className="h-3.5 w-3.5 text-[var(--color-accent)]" aria-hidden />
          <span className="text-[12px] text-[var(--color-neutral)]">中标</span>
          <span className="tnum text-[15px] font-medium text-[var(--color-ink-2)]">
            {fmtInt(supplier.winCount)}
          </span>
          <span className="text-[12px] text-[var(--color-neutral)]">次</span>
        </div>
        <div className="flex items-baseline gap-1">
          <span className="text-[12px] text-[var(--color-neutral)]">总额</span>
          <span className="tnum text-[15px] font-medium text-[var(--color-ink-2)]">
            {amount.value}
          </span>
          <span className="text-[11.5px] text-[var(--color-neutral)]">{amount.unit}</span>
        </div>
      </div>

      <div className="mt-3.5 flex items-center justify-between gap-2">
        {supplier.mainRegion ? (
          <span className="flex min-w-0 items-center gap-1 text-[12px] text-[var(--color-neutral)]">
            <MapPin className="h-3 w-3 shrink-0" aria-hidden />
            <span className="truncate">主要中标地区 {supplier.mainRegion}</span>
          </span>
        ) : (
          <span />
        )}
        <Button variant="secondary" size="sm" onClick={() => onOpenDetail(supplier)}>
          查看详情
        </Button>
      </div>
    </article>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full bg-[var(--color-accent-soft)] px-2.5 py-1 text-[12px] text-[var(--color-accent)]">
      {children}
    </span>
  );
}
