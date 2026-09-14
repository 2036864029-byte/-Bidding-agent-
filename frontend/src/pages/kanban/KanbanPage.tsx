import { ModulePageLayout } from '@/components/layout/ModulePageLayout';

/**
 * 商机看板 —— Phase 1 占位。
 * Phase 3 落地：漏斗指标栏（地区/行业/产品筛选）+ 热力图 + 占比图 + 时间线走势（Recharts）+ Mock 注入空态。
 */
export function KanbanPage() {
  return (
    <ModulePageLayout
      name="商机看板"
      eyebrow="OPPORTUNITY KANBAN"
      description="地区热力 · 行业占比 · 时间走势 —— 招投标商机一屏尽览。数据为演示形态，Phase 3 以 Mock + 适配器先行。"
    >
      <div className="flex h-full min-h-[18rem] flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--color-rule-2)] px-8 py-14 text-center">
        <p className="mono-label mb-3">PHASE 3 · MOCK + RECHARTS</p>
        <p className="max-w-md text-[14px] leading-relaxed text-[var(--color-muted)]">
          这里将出现筛选栏、地区热力图、行业占比图与时间线走势图。
        </p>
      </div>
    </ModulePageLayout>
  );
}
