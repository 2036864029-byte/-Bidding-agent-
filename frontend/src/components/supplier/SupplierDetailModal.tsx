import { useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { X } from 'lucide-react';
import { RiskTags } from '@/components/supplier/RiskTags';
import { ScoreMeter } from '@/components/supplier/ScoreMeter';
import { SupplierRadar } from '@/components/supplier/SupplierRadar';
import { fmtInt } from '@/lib/format';
import type { Supplier } from '@/types/supplier';

/**
 * SupplierDetailModal —— 供应商详情（设计文档 §4.5）。
 *
 * 中标历史价格分布是**单序列**（一家供应商逐年）→ 用品牌色即可，不需要分类色。
 * 分类色只在该模块「多家对比」时才出场。
 */
export function SupplierDetailModal({
  supplier,
  onClose,
}: {
  supplier: Supplier | null;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!supplier) return;
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [supplier, onClose]);

  return (
    <AnimatePresence>
      {supplier && (
        <motion.div
          key="supplier-detail"
          className="fixed inset-0 z-[80] flex items-center justify-center px-4 py-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2, ease: 'easeOut' }}
        >
          <div
            className="absolute inset-0 bg-[oklch(24%_0.02_258/0.22)] backdrop-blur-sm"
            onClick={onClose}
            aria-hidden
          />
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={`供应商详情 · ${supplier.name}`}
            tabIndex={-1}
            initial={{ opacity: 0, y: 16, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.99 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="relative z-10 max-h-[88vh] w-full max-w-[46rem] overflow-y-auto rounded-[var(--radius-xl)] border border-[var(--color-rule)] bg-[var(--color-paper)] shadow-[var(--shadow-float)] outline-none"
          >
            <header className="flex items-start justify-between gap-3 border-b border-[var(--color-rule)] px-6 py-4">
              <div className="min-w-0">
                <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                  {supplier.name}
                </h2>
                <p className="mono-label mt-1">
                  {supplier.region} · {supplier.creditCode}
                </p>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭详情"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </header>

            <div className="space-y-6 px-6 py-5">
              <p className="text-[13px] leading-relaxed text-[var(--color-muted)]">
                {supplier.coreCapability}
              </p>

              <div className="flex flex-wrap gap-1.5">
                {supplier.categories.map((c) => (
                  <span
                    key={c}
                    className="rounded-full bg-[var(--color-accent-soft)] px-2.5 py-1 text-[12px] text-[var(--color-accent)]"
                  >
                    {c}
                  </span>
                ))}
                <RiskTags tags={supplier.riskTags} />
              </div>

              {/* 履约统计 */}
              <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="历史合作评分" value={String(supplier.score)} suffix="分" />
                <Stat
                  label="参与投标成功率"
                  value={supplier.stats.bidWinRate.toFixed(1)}
                  suffix="%"
                />
                <Stat label="累计投标" value={fmtInt(supplier.stats.totalBids)} suffix="次" />
                <Stat label="合作年限" value={String(supplier.stats.activeYears)} suffix="年" />
              </section>

              <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                <div>
                  <p className="mb-1 text-[13px] font-medium text-[var(--color-ink-2)]">
                    履约能力雷达
                  </p>
                  <p className="mb-2 text-[12px] text-[var(--color-neutral)]">五维评分（0~100）</p>
                  <SupplierRadar suppliers={[supplier]} height={230} />
                </div>
                <div>
                  <p className="mb-1 text-[13px] font-medium text-[var(--color-ink-2)]">
                    中标历史价格分布
                  </p>
                  <p className="mb-2 text-[12px] text-[var(--color-neutral)]">年均中标金额（万元）</p>
                  <ResponsiveContainer width="100%" height={230}>
                    <BarChart
                      data={supplier.stats.priceHistory.map((p) => ({
                        year: String(p.year),
                        value: p.avgAmount,
                      }))}
                      margin={{ top: 12, right: 8, bottom: 0, left: 0 }}
                    >
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
                        width={48}
                        tick={{ fill: 'var(--color-neutral)', fontSize: 11 }}
                      />
                      <Tooltip
                        cursor={{ fill: 'var(--color-paper-2)' }}
                        contentStyle={{
                          borderRadius: 10,
                          border: '1px solid var(--color-rule)',
                          background: 'var(--color-paper)',
                          fontSize: 12,
                        }}
                        labelStyle={{ color: 'var(--color-neutral)', fontSize: 11 }}
                        labelFormatter={(l) => `${l} 年`}
                        formatter={(v) => [`${fmtInt(Number(v))} 万元`, '年均中标额']}
                      />
                      <Bar
                        dataKey="value"
                        fill="var(--chart-series)"
                        radius={[4, 4, 0, 0]}
                        barSize={24}
                        isAnimationActive={false}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </section>

              <section>
                <p className="mb-2 text-[12px] text-[var(--color-neutral)]">参与投标成功率</p>
                <ScoreMeter
                  score={supplier.stats.bidWinRate}
                  ariaLabel="参与投标成功率"
                />
              </section>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Stat({ label, value, suffix }: { label: string; value: string; suffix: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-rule)] px-3.5 py-3">
      <p className="text-[12px] text-[var(--color-neutral)]">{label}</p>
      <p className="mt-1 flex items-baseline gap-1">
        <span className="text-[19px] font-semibold leading-none text-[var(--color-ink)]">
          {value}
        </span>
        <span className="text-[12px] text-[var(--color-neutral)]">{suffix}</span>
      </p>
    </div>
  );
}
