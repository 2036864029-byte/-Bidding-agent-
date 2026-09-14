import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { X } from 'lucide-react';
import { SupplierRadar } from '@/components/supplier/SupplierRadar';
import { fmtInt } from '@/lib/format';
import { CATEGORICAL_COLORS, MAX_COMPARE, priceCompareData } from '@/lib/supplier';
import type { Supplier } from '@/types/supplier';

/**
 * SupplierComparePanel —— 多方对比面板（本模块「多色」的正当用场）。
 *
 * 色相在这里承载的是**身份**（这是哪一家），所以用分类色板而不是单色 ——
 * 与看板/询价那些「单序列」的图不同。规范硬约束：雷达与多线属 all-pairs 形式，
 * **最多 3 个系列**（再多只能折叠或拆图），对比席位上限即由此而来。
 * 两张图共用一条图例（同一套供应商↔颜色映射，不重复画两份）。
 */
export function SupplierComparePanel({
  suppliers,
  onRemove,
  onClear,
}: {
  suppliers: Supplier[];
  onRemove: (id: string) => void;
  onClear: () => void;
}) {
  if (!suppliers.length) return null;

  const colorOf = (id: string) => {
    const i = suppliers.findIndex((s) => s.id === id);
    return CATEGORICAL_COLORS[i % CATEGORICAL_COLORS.length];
  };

  return (
    <section className="rounded-2xl border border-[var(--color-accent)] bg-[var(--color-paper)] p-5">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
            多方对比
          </h2>
          <span className="mono-label">
            {suppliers.length} / {MAX_COMPARE}
          </span>
        </div>
        <button
          type="button"
          onClick={onClear}
          className="text-[12px] text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:text-[var(--color-accent)]"
        >
          清空对比
        </button>
      </header>

      {/* 统一图例：两张图共用同一套「供应商 ↔ 颜色」映射 */}
      <ul className="mb-4 flex flex-wrap gap-2">
        {suppliers.map((s) => (
          <li key={s.id}>
            <span className="flex items-center gap-2 rounded-full border border-[var(--color-rule)] py-1 pl-2.5 pr-1.5 text-[12.5px] text-[var(--color-ink-2)]">
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                style={{ background: colorOf(s.id) }}
              />
              {s.name}
              <button
                type="button"
                onClick={() => onRemove(s.id)}
                aria-label={`从对比中移除 ${s.name}`}
                className="flex h-5 w-5 items-center justify-center rounded-full text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            </span>
          </li>
        ))}
      </ul>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div>
          <p className="mb-1 text-[13px] font-medium text-[var(--color-ink-2)]">履约能力对比</p>
          <p className="mb-2 text-[12px] text-[var(--color-neutral)]">五维评分（0~100）</p>
          <SupplierRadar suppliers={suppliers} height={272} showLegend={false} />
        </div>

        <div>
          <p className="mb-1 text-[13px] font-medium text-[var(--color-ink-2)]">中标均价走势</p>
          <p className="mb-2 text-[12px] text-[var(--color-neutral)]">
            单位：万元 · 缺该年份不连线（不画假跌线）
          </p>
          <PriceCompareChart suppliers={suppliers} />
        </div>
      </div>
    </section>
  );
}

function PriceCompareChart({ suppliers }: { suppliers: Supplier[] }) {
  return (
    <ResponsiveContainer width="100%" height={272}>
      <LineChart data={priceCompareData(suppliers)} margin={{ top: 12, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="year"
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
          tick={{ fill: 'var(--color-neutral)', fontSize: 11 }}
        />
        <YAxis
          tickFormatter={(v: number) => fmtInt(v)}
          tickLine={false}
          axisLine={false}
          width={52}
          tick={{ fill: 'var(--color-neutral)', fontSize: 11 }}
        />
        <Tooltip
          cursor={{ stroke: 'var(--chart-axis)', strokeWidth: 1 }}
          contentStyle={{
            borderRadius: 10,
            border: '1px solid var(--color-rule)',
            background: 'var(--color-paper)',
            fontSize: 12,
          }}
          labelStyle={{ color: 'var(--color-neutral)', fontSize: 11 }}
          labelFormatter={(l) => `${l} 年`}
        />
        {suppliers.map((s, i) => {
          const color = CATEGORICAL_COLORS[i % CATEGORICAL_COLORS.length];
          return (
            <Line
              key={s.id}
              type="monotone"
              dataKey={s.name}
              stroke={color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={{ r: 4, strokeWidth: 2, stroke: 'var(--color-paper)', fill: color }}
              activeDot={{ r: 5, strokeWidth: 2, stroke: 'var(--color-paper)' }}
              connectNulls={false}
              isAnimationActive={false}
            />
          );
        })}
      </LineChart>
    </ResponsiveContainer>
  );
}
