import { ModulePageLayout } from '@/components/layout/ModulePageLayout';

/**
 * 供应商全景 —— Phase 1 占位。
 * Phase 3 落地：多维多选 Tag 筛选 + 网格卡 + 履约能力雷达图 + 风险标签 + 详情 Modal。
 */
export function SupplierPage() {
  return (
    <ModulePageLayout
      name="供应商全景"
      eyebrow="SUPPLIER INTELLIGENCE"
      description="资质 · 历史合作评分 · 主营类别 · 地区多维筛选；网格卡 + 雷达图，识别履约能力与风险。"
    >
      <div className="flex h-full min-h-[18rem] flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--color-rule-2)] px-8 py-14 text-center">
        <p className="mono-label mb-3">PHASE 3 · MOCK SUPPLIER + RADAR</p>
        <p className="max-w-md text-[14px] leading-relaxed text-[var(--color-muted)]">
          这里将出现多维 Tag 筛选、网格化供应商卡片、履约能力雷达图与详情弹窗。
        </p>
      </div>
    </ModulePageLayout>
  );
}
