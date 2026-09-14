import { ModulePageLayout } from '@/components/layout/ModulePageLayout';

/**
 * 智能询价 —— Phase 1 占位。
 * Phase 3/4 落地：对话式需求描述区（接真实 price_inquiry）+ 价格对比表 + 偏离度高亮 + 调价 Drawer。
 */
export function InquiryPage() {
  return (
    <ModulePageLayout
      name="智能询价"
      eyebrow="SMART INQUIRY"
      description="语言/文件描述采购需求，双库比对历史中标金额；多方报价横向对比，偏离度超阈值荧光高亮。"
    >
      <div className="flex h-full min-h-[18rem] flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--color-rule-2)] px-8 py-14 text-center">
        <p className="mono-label mb-3">PHASE 3 · MOCK INQUIRY + DRAWER</p>
        <p className="max-w-md text-[14px] leading-relaxed text-[var(--color-muted)]">
          这里将出现需求描述对话区、供应商横向对比表与调价抽屉。
        </p>
      </div>
    </ModulePageLayout>
  );
}
