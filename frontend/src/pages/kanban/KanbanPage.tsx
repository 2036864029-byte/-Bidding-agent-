import { useState, type ReactNode } from 'react';
import { BarChart3, WifiOff } from 'lucide-react';
import { ModulePageLayout } from '@/components/layout/ModulePageLayout';
import { ChartCard, DataTable } from '@/components/common/ChartCard';
import { EmptyState } from '@/components/common/EmptyState';
import { StatTiles } from '@/components/kanban/StatTiles';
import { FILTER_ALL, KanbanFilterRow } from '@/components/kanban/KanbanFilterRow';
import { IndustryTrendChart } from '@/components/kanban/IndustryTrendChart';
import { ChinaMapHeatmap } from '@/components/kanban/ChinaMapHeatmap';
import { IndustryBars } from '@/components/kanban/IndustryBars';
import { Badge } from '@/components/ui/badge';
import { useAsyncData } from '@/hooks/use-async-data';
import { fmtInt } from '@/lib/format';
import { api, dataConfig } from '@/services/api';

/**
 * 商机看板 —— 数量口径。
 *
 * 数据链路：页面只依赖 `api.kanban`（Mock↔API 开关）与 `@/types/kanban` 领域契约，
 * 不触碰任何后端原始字段（换成真数据只改 adapters/services，本文件零改动）。
 *
 * 版面：汇总指标 → 筛选行 → **地区分布（3D 中国地图，主角）** → 行业走势 → 行业占比。
 * 地区分布置顶：它回答"哪里有机会"，是最先要看的一屏；行业走势回答"趋势如何"。
 *
 * **无金额维度**（数据只有单月 8 天）；**无产品榜**（产品频率分布无信号，
 * 最高 3 次 / 94% 只出现 1 次，任何排序都是并列时按名称排的假象，2026-09-15 撤掉）。
 * **无「商机走势」单序列图** —— 被 2023-10 单月 8,727 条（安徽采集倾倒）压成一条直线，
 * 已换成 `trade_stat_fact` 的**交易类型 × 月**多序列图（有真实月度起伏）。
 * **无金额维度**：当前数据只有单月 8 天，金额走势只有一个点，且看板聚合源
 * `trade_stat_fact` 本就没有金额指标（理由详见 types/kanban.ts 文件头）。
 */
export function KanbanPage() {
  const { data, error, loading } = useAsyncData(() => api.kanban.getOverview());
  const [region, setRegion] = useState(FILTER_ALL);
  const [industry, setIndustry] = useState(FILTER_ALL);

  // 徽章/描述跟随**数据源开关**，不写死 —— 否则翻了开关文案还在撒谎
  const live = dataConfig.kanban === 'api';

  const badge = (
    <Badge variant="accent">
      <BarChart3 className="h-3 w-3" aria-hidden />
      {live ? '真实数据 · 统一宽表 + 交易统计' : 'Mock 数据'}
    </Badge>
  );

  const shell = (body: ReactNode) => (
    <ModulePageLayout
      name="商机看板"
      eyebrow="OPPORTUNITY KANBAN"
      description={
        live
          ? '行业走势 · 地区热力 · 行业占比 —— 招投标商机一屏尽览。'
          : '行业走势 · 地区热力 · 行业占比 —— 招投标商机一屏尽览。当前为演示数据（Mock + 适配器）。'
      }
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

  // ⚠️ 热力图**不随地区筛选变化**：它是「地区分布」这个整体视图，
  // 按单省筛选后只剩一格，等于把图变成数字。它也不是本项目收录的项目，
  // 而是 `trade_stat_fact` 的省级交易统计（见 types/kanban.ts）。
  const heatmap = data.heatmap;
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

      {/* ── 地区分布（主角）：真实省界 + 立体台地，置顶 ── */}
      <ChartCard
        title="地区分布"
        subtitle={
          data.heatmapMonth
            ? `省级 · 公共资源交易项目数（${data.heatmapMonth.slice(0, 7)}）· 颜色深浅表示数量`
            : '省级 · 公共资源交易项目数 · 颜色深浅表示数量'
        }
        table={
          <DataTable
            columns={['地区', '项目数（个）']}
            rows={heatmap.map((c) => [c.region, fmtInt(c.count)])}
          />
        }
      >
        <ChinaMapHeatmap cells={heatmap} />
      </ChartCard>

      {/* ── 行业走势：交易类型 × 月，多序列（≤3 条） ── */}
      <ChartCard
        title="行业走势"
        subtitle={
          live
            ? '按交易类型 · 公共资源交易项目数（月度）· 多线对比最多 3 条（dataviz 的 all-pairs 约束）'
            : '按交易类型 · 项目数（月度）'
        }
        table={
          <DataTable
            columns={['月份', ...data.industryTrend.series.map((s) => s.name)]}
            rows={data.industryTrend.months.map((m, i) => [
              m,
              ...data.industryTrend.series.map((s) => fmtInt(s.values[i] ?? 0)),
            ])}
          />
        }
      >
        <IndustryTrendChart trend={data.industryTrend} height={230} />
      </ChartCard>

      {/* ── 行业占比 ── */}
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
  );
}
