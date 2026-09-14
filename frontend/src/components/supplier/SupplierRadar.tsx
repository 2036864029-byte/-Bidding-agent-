import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import { CATEGORICAL_COLORS, radarCompareData } from '@/lib/supplier';
import type { Supplier } from '@/types/supplier';

/**
 * SupplierRadar —— 供应商能力雷达（单家或多家叠加）。
 *
 * 规范要点：**多系列时必须给图例**（≥2 系列不能只靠颜色配对），系列色取自
 * 经 all-pairs CVD 校验的分类色板（≤3 个系列）；折线 2px。
 * 多系列**只描边、不填充** —— 三块半透明填充叠起来会糊成一团，
 * 轮廓线才是可比的那个东西。单系列才加一层淡淡的填充。
 * 单系列时不需要图例（标题已说明画的是谁）。
 */
export function SupplierRadar({
  suppliers,
  height = 236,
  showLegend,
}: {
  suppliers: Supplier[];
  height?: number;
  /** 默认：多系列时显示。对比面板会自带一条统一图例，那里传 false 避免重复。 */
  showLegend?: boolean;
}) {
  if (!suppliers.length) return null;
  const data = radarCompareData(suppliers);
  const multi = suppliers.length > 1;
  const legend = showLegend ?? multi;

  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <RadarChart data={data} outerRadius="68%">
          <PolarGrid stroke="var(--chart-grid)" />
          <PolarAngleAxis dataKey="label" tick={{ fill: 'var(--color-muted)', fontSize: 11 }} />
          <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} tickCount={5} />
          {suppliers.map((s, i) => (
            <Radar
              key={s.id}
              name={s.name}
              dataKey={s.name}
              stroke={CATEGORICAL_COLORS[i % CATEGORICAL_COLORS.length]}
              fill={CATEGORICAL_COLORS[i % CATEGORICAL_COLORS.length]}
              fillOpacity={multi ? 0 : 0.12}
              strokeWidth={2}
              isAnimationActive={false}
            />
          ))}
          <Tooltip
            contentStyle={{
              borderRadius: 10,
              border: '1px solid var(--color-rule)',
              background: 'var(--color-paper)',
              fontSize: 12,
            }}
            labelStyle={{ color: 'var(--color-neutral)', fontSize: 11 }}
          />
        </RadarChart>
      </ResponsiveContainer>

      {legend && (
        <ul className="mt-1 flex flex-wrap justify-center gap-x-4 gap-y-1.5">
          {suppliers.map((s, i) => (
            <li key={s.id} className="flex items-center gap-1.5 text-[12px] text-[var(--color-ink-2)]">
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                style={{ background: CATEGORICAL_COLORS[i % CATEGORICAL_COLORS.length] }}
              />
              {s.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
