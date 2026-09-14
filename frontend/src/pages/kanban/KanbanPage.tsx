import { useState, type ReactNode } from 'react';
import { BarChart3, WifiOff } from 'lucide-react';
import { ModulePageLayout } from '@/components/layout/ModulePageLayout';
import { ChartCard, DataTable } from '@/components/common/ChartCard';
import { EmptyState } from '@/components/common/EmptyState';
import { StatTiles } from '@/components/kanban/StatTiles';
import { FILTER_ALL, KanbanFilterRow } from '@/components/kanban/KanbanFilterRow';
import { TrendChart } from '@/components/kanban/TrendChart';
import { RegionHeatmap } from '@/components/kanban/RegionHeatmap';
import { IndustryBars } from '@/components/kanban/IndustryBars';
import { Badge } from '@/components/ui/badge';
import { useAsyncData } from '@/hooks/use-async-data';
import { fmtInt } from '@/lib/format';
import { api } from '@/services/api';

/**
 * 商机看板 —— Phase 3 首个落地模块。
 *
 * 数据链路：页面只依赖 `api.kanban`（Mock↔API 开关）与 `@/types/kanban` 领域契约，
 * 不触碰任何后端原始字段（换成真数据只改 adapters/services，本文件零改动）。
 *
 * 版面：汇总指标 → 筛选行 → **走势（主角）** → 地区热力 / 行业占比。
 * 走势图按规范拆成两张单序列图（项目数与金额量纲差 ~1000×，绝不双 Y 轴）。
 */
export function KanbanPage() {
  const { data, error, loading } = useAsyncData(() => api.kanban.getOverview());
  const [region, setRegion] = useState(FILTER_ALL);
  const [industry, setIndustry] = useState(FILTER_ALL);

  const badge = (
    <Badge variant="accent">
      <BarChart3 className="h-3 w-3" aria-hidden />
      Mock 数据
    </Badge>
  );

  const shell = (body: ReactNode) => (
    <ModulePageLayout
      name="商机看板"
      eyebrow="OPPORTUNITY KANBAN"
      description="按月走势 · 地区热力 · 行业占比 —— 招投标商机一屏尽览。当前为演示数据（Mock + 适配器），后端聚合端点就绪后切开关即可换真。"
      badge={badge}
    >
      {body}
    </ModulePageLayout>
  );

  if (loading) {
    return shell(
      <div className="space-y-4" aria-busy="true" aria-live="polite">
        {[96, 320, 260].map((h, i) => (
          <div
            key={i}
            className="animate-pulse rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper-2)]"
            style={{ height: h }}
          />
        ))}
      </div>
    );
  }

  if (error || !data) {
    return shell(
      <EmptyState
        icon={WifiOff}
        title="暂时拿不到看板数据"
        description={error?.message ?? '数据源返回为空，请稍后重试。'}
      />
    );
  }

  const trendByAmount = data.trend.map((t) => ({ month: t.month, value: t.amount }));
  const trendByCount = data.trend.map((t) => ({ month: t.month, value: t.count }));

  const heatmap = region === FILTER_ALL ? data.heatmap : data.heatmap.filter((c) => c.region === region);
  const composition =
    industry === FILTER_ALL ? data.composition : data.composition.filter((c) => c.industry === industry);

  return shell(
    <div className="space-y-4">
      <StatTiles summary={data.summary} />

      <KanbanFilterRow
        filters={data.filters}
        region={region}
        industry={industry}
        onRegionChange={setRegion}
        onIndustryChange={setIndustry}
        updatedAt={data.updatedAt}
      />

      {/* ── 走势（主角）：两张单序列图，共享同一时间轴 ── */}
      <ChartCard
        title="商机走势"
        subtitle="按月 · 项目数量与中标金额分两张图 —— 两者量纲相差约千倍，合并到双 Y 轴会造出并不存在的相关性"
        table={
          <DataTable
            columns={['月份', '项目数（个）', '金额（万元）']}
            rows={data.trend.map((t) => [t.month, fmtInt(t.count), fmtInt(t.amount)])}
          />
        }
      >
        <div className="space-y-5">
          <div>
            <p className="mb-1 text-[13px] font-medium text-[var(--color-ink-2)]">
              中标金额 <span className="font-normal text-[var(--color-neutral)]">（万元）</span>
            </p>
            <TrendChart data={trendByAmount} unit="万元" />
          </div>
          <div>
            <p className="mb-1 text-[13px] font-medium text-[var(--color-ink-2)]">
              项目数量 <span className="font-normal text-[var(--color-neutral)]">（个）</span>
            </p>
            <TrendChart data={trendByCount} unit="个" height={150} />
          </div>
        </div>
      </ChartCard>

      {/* ── 副图：地区热力 + 行业占比 ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard
          title="地区分布"
          subtitle="省级 · 颜色深浅表示项目数量（单色相顺序色阶）"
          table={
            <DataTable
              columns={['地区', '项目数（个）', '金额（万元）']}
              rows={heatmap.map((c) => [c.region, fmtInt(c.count), fmtInt(c.amount)])}
            />
          }
        >
          <RegionHeatmap cells={heatmap} />
        </ChartCard>

        <ChartCard
          title="行业占比"
          subtitle="按项目数量 · 条端标注为占比"
          table={
            <DataTable
              columns={['行业', '项目数（个）', '占比']}
              rows={composition.map((c) => [c.industry, fmtInt(c.count), `${c.pct.toFixed(1)}%`])}
            />
          }
        >
          <IndustryBars items={composition} />
        </ChartCard>
      </div>
    </div>
  );
}
