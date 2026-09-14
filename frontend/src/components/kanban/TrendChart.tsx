import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { fmtInt, fmtMonthTick } from '@/lib/format';

/**
 * TrendChart —— 单序列面积走势图（dataviz 规范）。
 *
 * 为什么是**两个图**而不是一个双 Y 轴图：本模块走势有两个量纲（项目数 vs 金额，
 * 量级差 ~1000×），双 Y 轴会凭空造出并不存在的相关性——规范里明确禁止。
 * 故拆成两张共享 X 轴的**单序列**小多图。
 *
 * 规范要点：线 2px；面积 ~10% 淡洗；网格发丝实线（非虚线）且只留横向；
 * 单序列**不要图例**（标题已说明画的是什么）；末端点直接标注（不是每个点都标）；
 * 文字一律用文本色，不穿序列色。
 */

export interface TrendPointVM {
  month: string;
  value: number;
}

function TrendTooltip({
  active,
  payload,
  label,
  unit,
}: {
  active?: boolean;
  payload?: Array<{ value?: number }>;
  label?: string;
  unit: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 shadow-[var(--shadow-lift)]">
      <p className="mono-label mb-0.5 text-[10px]">{label}</p>
      <p className="tnum text-[13px] font-semibold text-[var(--color-ink)]">
        {fmtInt(payload[0]?.value ?? 0)} <span className="font-normal text-[var(--color-neutral)]">{unit}</span>
      </p>
    </div>
  );
}

export function TrendChart({
  data,
  unit,
  height = 190,
}: {
  data: TrendPointVM[];
  unit: string;
  height?: number;
}) {
  const last = data[data.length - 1];

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 18, right: 56, bottom: 0, left: 0 }}>
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
        <Tooltip
          content={<TrendTooltip unit={unit} />}
          cursor={{ stroke: 'var(--chart-axis)', strokeWidth: 1 }}
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke="var(--chart-series)"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="var(--chart-series-wash)"
          isAnimationActive={false}
          dot={false}
        />
        {/* 末端点直接标注（仅末点，不是每点都标）*/}
        {last && (
          <ReferenceDot
            x={last.month}
            y={last.value}
            r={4}
            fill="var(--chart-series)"
            stroke="var(--color-paper)"
            strokeWidth={2}
            label={{
              value: fmtInt(last.value),
              position: 'top',
              fill: 'var(--color-ink-2)',
              fontSize: 11,
            }}
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  );
}
