import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { fmtInt } from '@/lib/format';
import type { KanbanCompositionItem } from '@/types/kanban';

/**
 * IndustryBars —— 行业占比（横向条形）。
 *
 * 规范要点：行业是**名义分类**（换顺序不改变含义），所以**所有条同一个颜色**
 * （slot 1）—— 按值深浅上色是反模式（把长度已表达的信息再用色相编码一遍，还过不了
 * 分类校验）。占比用**条端直接标注**给出，不用每点都标。
 * 条粗 14px（≤24）、数据端 4px 圆角、基线端方角。
 */

function BarTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: KanbanCompositionItem }>;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div className="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)] px-3 py-2 shadow-[var(--shadow-lift)]">
      <p className="mb-0.5 text-[12px] font-medium text-[var(--color-ink)]">{d.industry}</p>
      <p className="tnum text-[12px] text-[var(--color-muted)]">
        {fmtInt(d.count)} 个项目 · 占比 {d.pct.toFixed(1)}%
      </p>
    </div>
  );
}

export function IndustryBars({ items }: { items: KanbanCompositionItem[] }) {
  const data = [...items]
    .sort((a, b) => b.count - a.count)
    .map((d) => ({ ...d, pctLabel: `${d.pct.toFixed(1)}%` }));

  // ⚠️ `interval={0}` 必须显式给：recharts 默认会在刻度拥挤时**丢掉一半标签**
  // （实测 12 个类别只渲染 6 个纵轴标签，条形图就变成了"不知道哪根是哪根"）。
  // 高度按类别数给足，宁可卡片高一点也不要缺标签。
  const height = Math.max(288, data.length * 26 + 24);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 0, right: 52, bottom: 0, left: 0 }}
        barCategoryGap={7}
      >
        <XAxis type="number" hide />
        <YAxis
          type="category"
          dataKey="industry"
          width={96}
          interval={0}
          tickLine={false}
          axisLine={false}
          tick={{ fill: 'var(--color-ink-2)', fontSize: 12 }}
        />
        <Tooltip content={<BarTooltip />} cursor={{ fill: 'var(--color-paper-2)' }} />
        <Bar dataKey="count" fill="var(--chart-series)" barSize={14} radius={[0, 4, 4, 0]} isAnimationActive={false}>
          <LabelList
            dataKey="pctLabel"
            position="right"
            fill="var(--color-ink-2)"
            fontSize={12}
            offset={8}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
