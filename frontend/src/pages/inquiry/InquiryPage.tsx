import { useState, type ReactNode } from 'react';
import { Calculator, WifiOff } from 'lucide-react';
import { ModulePageLayout } from '@/components/layout/ModulePageLayout';
import { EmptyState } from '@/components/common/EmptyState';
import { Badge } from '@/components/ui/badge';
import { InquiryChat } from '@/components/inquiry/InquiryChat';
import { ItemCompareCard } from '@/components/inquiry/ItemCompareCard';
import { QuoteDrawer } from '@/components/inquiry/QuoteDrawer';
import { useAsyncData } from '@/hooks/use-async-data';
import { fmtAmountYuan } from '@/lib/format';
import { api } from '@/services/api';
import type { InquiryItem, InquiryQuote, InquiryStatus } from '@/types/inquiry';

const STATUS_LABEL: Record<InquiryStatus, string> = {
  DRAFT: '草稿',
  IN_PROGRESS: '询价中',
  COMPLETED: '已完成',
};

/**
 * 智能询价 —— Phase 3 第二个模块。
 *
 * 左栏：对话式需求描述（**接真实 `/chat`** 的价格查询路由，走 `api.chat`）。
 * 右栏：多方报价对比 + 偏离高亮 + 调价抽屉（询价单是未来域模型，当前走 `api.inquiry` 的 Mock）。
 */
export function InquiryPage() {
  const { data, error, loading } = useAsyncData(() => api.inquiry.getSummary());
  const [active, setActive] = useState<{ item: InquiryItem; quote: InquiryQuote } | null>(null);

  const badge = (
    <Badge variant="accent">
      <Calculator className="h-3 w-3" aria-hidden />
      对话区接真实 /chat · 对比表为 Mock
    </Badge>
  );

  const shell = (body: ReactNode) => (
    <ModulePageLayout
      name="智能询价"
      eyebrow="INTELLIGENT INQUIRY"
      description="用自然语言描述采购需求，系统调取历史中标价给出价格参考，并对多方报价做偏离校验。"
      badge={badge}
    >
      {body}
    </ModulePageLayout>
  );

  if (loading) {
    return shell(
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
        {[0, 1].map((i) => (
          <div
            key={i}
            className="h-[32rem] animate-pulse rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)]"
          />
        ))}
      </div>
    );
  }

  if (error || !data) {
    return shell(
      <EmptyState
        icon={WifiOff}
        title="暂时拿不到询价单"
        description={error?.message ?? '数据源返回为空，请稍后重试。'}
      />
    );
  }

  // ⚠️ 询价金额是「元」口径（见 types/inquiry.ts），必须用 fmtAmountYuan 而非 fmtAmountWan
  const budget = fmtAmountYuan(data.totalBudget);

  return shell(
    <>
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)]">
        <InquiryChat />

        <div className="space-y-4">
          {/* 询价单抬头 */}
          <section className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] px-5 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                {data.title}
              </h2>
              <Badge variant={data.status === 'COMPLETED' ? 'success' : 'neutral'}>
                {STATUS_LABEL[data.status]}
              </Badge>
            </div>
            <p className="mono-label mt-1.5">{data.inquiryId}</p>
            <dl className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-2">
              <div className="flex items-baseline gap-1.5">
                <dt className="text-[12px] text-[var(--color-neutral)]">预算总额</dt>
                <dd className="flex items-baseline gap-1">
                  <span className="text-[20px] font-semibold leading-none text-[var(--color-ink)]">
                    {budget.value}
                  </span>
                  <span className="text-[12px] text-[var(--color-neutral)]">{budget.unit}</span>
                </dd>
              </div>
              <div className="flex items-baseline gap-1.5">
                <dt className="text-[12px] text-[var(--color-neutral)]">询价项</dt>
                <dd className="tnum text-[15px] font-medium text-[var(--color-ink-2)]">
                  {data.items.length}
                </dd>
              </div>
            </dl>
          </section>

          {data.items.map((item) => (
            <ItemCompareCard
              key={item.itemId}
              item={item}
              onAdjust={(quote) => setActive({ item, quote })}
            />
          ))}
        </div>
      </div>

      <QuoteDrawer
        open={active !== null}
        onClose={() => setActive(null)}
        item={active?.item ?? null}
        quote={active?.quote ?? null}
      />
    </>
  );
}
