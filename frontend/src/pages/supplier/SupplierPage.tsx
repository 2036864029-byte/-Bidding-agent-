import { useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { Building2, SearchX, WifiOff } from 'lucide-react';
import { ModulePageLayout } from '@/components/layout/ModulePageLayout';
import { EmptyState } from '@/components/common/EmptyState';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SupplierCard } from '@/components/supplier/SupplierCard';
import { SupplierComparePanel } from '@/components/supplier/SupplierComparePanel';
import { SupplierDetailModal } from '@/components/supplier/SupplierDetailModal';
import { SupplierFilters } from '@/components/supplier/SupplierFilters';
import { useAsyncData } from '@/hooks/use-async-data';
import {
  EMPTY_FILTERS,
  filterSuppliers,
  MAX_COMPARE,
  riskCount,
  toggleCompare,
  type SupplierFilterState,
} from '@/lib/supplier';
import { api } from '@/services/api';
import type { Supplier } from '@/types/supplier';

/**
 * 供应商全景 —— Phase 3 第三个模块（收官）。
 *
 * 数据链路：只依赖 `api.supplier` 与 `@/types/supplier`；评分与雷达分**由后端给**，
 * 前端只做透传（口径已于 2026-09-14 拍板，不在前端计算）。
 *
 * 本模块是唯一「多色」的模块：色相承载「这是哪一家」的身份信息（多方对比），
 * 与看板/询价那些单序列图不同；对比上限 3 家 —— 这是规范对 all-pairs 图形的硬约束。
 */
export function SupplierPage() {
  const { data, error, loading } = useAsyncData(() => api.supplier.getOverview());
  const [filters, setFilters] = useState<SupplierFilterState>(EMPTY_FILTERS);
  const [detail, setDetail] = useState<Supplier | null>(null);

  // 对比选择与 URL 双向绑定：`/supplier?compare=SUP-001,SUP-002` 可分享、刷新不丢。
  // 初值用惰性初始化（只算一次），之后以组件状态为准。
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedIds, setSelectedIds] = useState<string[]>(() =>
    (searchParams.get('compare') ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, MAX_COMPARE)
  );

  const updateSelection = (ids: string[]) => {
    setSelectedIds(ids);
    const next = new URLSearchParams(searchParams);
    if (ids.length) next.set('compare', ids.join(','));
    else next.delete('compare');
    setSearchParams(next, { replace: true });
  };

  const badge = (
    <Badge variant="accent">
      <Building2 className="h-3 w-3" aria-hidden />
      Mock 数据 · 评分口径由后端给
    </Badge>
  );

  const shell = (body: ReactNode) => (
    <ModulePageLayout
      name="供应商全景"
      eyebrow="SUPPLIER INTELLIGENCE"
      description="多维筛选 · 履约能力雷达 · 风险预警 —— 支持最多三家横向对比。评分与雷达分由后端计算，前端只做呈现。"
      badge={badge}
    >
      {body}
    </ModulePageLayout>
  );

  if (loading) {
    return shell(
      <div className="space-y-4" aria-busy="true" aria-live="polite">
        <div className="h-40 animate-pulse rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)]" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-[26rem] animate-pulse rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)]"
            />
          ))}
        </div>
      </div>
    );
  }

  if (error || !data) {
    return shell(
      <EmptyState
        icon={WifiOff}
        title="暂时拿不到供应商数据"
        description={error?.message ?? '数据源返回为空，请稍后重试。'}
      />
    );
  }

  const visible = filterSuppliers(data.suppliers, filters);
  // 已选对比的供应商取自**全量**列表 —— 调筛选不应把已选的对比对象悄悄踢掉
  const selected = selectedIds
    .map((id) => data.suppliers.find((s) => s.id === id))
    .filter((s): s is Supplier => Boolean(s));

  return shell(
    <div className="space-y-4">
      <SupplierFilters
        filters={data.filters}
        state={filters}
        onChange={setFilters}
        resultCount={visible.length}
      />

      {selected.length > 0 && (
        <SupplierComparePanel
          suppliers={selected}
          onRemove={(id) => updateSelection(selectedIds.filter((x) => x !== id))}
          onClear={() => updateSelection([])}
        />
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <p className="text-[12.5px] text-[var(--color-neutral)]">
          共 <span className="tnum font-medium text-[var(--color-ink-2)]">{visible.length}</span> 家
          {riskCount(visible) > 0 && (
            <>
              {' · '}
              其中 <span className="tnum font-medium text-[var(--color-ink-2)]">{riskCount(visible)}</span>{' '}
              家有风险提示
            </>
          )}
        </p>
        <p className="text-[12px] text-[var(--color-neutral)]">
          勾选卡片上的「对比」可横向比较，最多 {MAX_COMPARE} 家
        </p>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          icon={SearchX}
          title="没有符合条件的供应商"
          description="当前筛选组合没有结果。放宽条件试试，或直接重置全部筛选。"
          action={
            <Button variant="ghost" size="sm" onClick={() => setFilters(EMPTY_FILTERS)}>
              重置筛选
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((s) => (
            <SupplierCard
              key={s.id}
              supplier={s}
              selectedIds={selectedIds}
              onToggleCompare={(id) => updateSelection(toggleCompare(selectedIds, id))}
              onOpenDetail={setDetail}
            />
          ))}
        </div>
      )}

      <SupplierDetailModal supplier={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
