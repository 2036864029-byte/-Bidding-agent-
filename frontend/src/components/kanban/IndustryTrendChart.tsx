import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { fmtInt, fmtMonthTick } from '@/lib/format';
import type { KanbanIndustryTrend } from '@/types/kanban';

/**
 * IndustryTrendChart —— **多序列**行业（交易类型）× 月走势。
 *
 * 与 `TrendChart`（单序列面积）的关键差别在于**颜色承担"这是哪一类"的身份**，
 * 因此必须配图例，且受 all-pairs 的 CVD 硬约束：**最多 3 条**（后端已取前 3）。
 * 用线不用面积 —— 3 条半透明面积叠在一起会糊成一片，读不出交叉。
 *
 * 规范要点：线 2px；网格发丝实线且只留横向；文字用文本色不穿序列色。
 */

/** 分类色：`--cat-1..3` 是项目已验证过 CVD 的三色（橙绿在红色盲下会糊，故用了青）。 */
const SERIES_COLORS = ['var(--cat-1)', 'var(--cat-2)', 'var(--cat-3)'];

function TrendTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name?: string; value?: number; color?: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 shadow-[var(--shadow-lift)]">
      <p className="mono-label mb-1 text-[10px]">{label}</p>
      {payload.map((p) => (
        <p key={p.name} className="flex items-center gap-1.5 text-[12px]">
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: p.color }}
            aria-hidden
          />
          <span className="text-[var(--color-muted)]">{p.name}</span>
          <span className="tnum ml-auto pl-3 font-medium text-[var(--color-ink)]">
            {fmtInt(p.value ?? 0)}
          </span>
        </p>
      ))}
    </div>
  );
}

export function IndustryTrendChart({
  trend,
  height = 220,
}: {
  trend: KanbanIndustryTrend;
  height?: number;
}) {
  // recharts 要「一行一月份、每条序列一个键」
  const rows = trend.months.map((month, i) => {
    const row: Record<string, string | number> = { month };
    for (const s of trend.series) row[s.name] = s.values[i] ?? 0;
    return row;
  });

  if (!trend.months.length || !trend.series.length) return null;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeWidth={1} />
        <XAxis
          dataKey="month"
          tickFormatter={fmtMonthTick}
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
          tick={{ fill: 'var(--color-neutral)', fontSize: 11 }}
          minTickGap={8}
        />
        <YAxis
          tickFormatter={(v: number) => fmtInt(v)}
          tickLine={false}
          axisLine={false}
          width={54}
          tick={{ fill: 'var(--color-neutral)', fontSize: 11 }}
        />
        <Tooltip content={<TrendTooltip />} cursor={{ stroke: 'var(--chart-axis)', strokeWidth: 1 }} />
        {/* 多序列必须有图例（颜色承担身份） */}
        <Legend
          verticalAlign="top"
          align="right"
          height={22}
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 12, color: 'var(--color-muted)' }}
        />
        {trend.series.map((s, i) => (
          <Line
            key={s.name}
            type="monotone"
            dataKey={s.name}
            stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            dot={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
