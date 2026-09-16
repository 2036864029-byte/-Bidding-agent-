import { useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import { fmtAmountYuan, fmtInt } from '@/lib/format';
import type { Supplier } from '@/types/supplier';

/**
 * SupplierDetailModal —— 供应商详情：**工商信息 + 中标情况**。
 *
 * 2026-09-15 重塑：去掉履约能力雷达 / 评分 / 成功率（无数据源），
 * 改为展示可核验的工商档案与中标记录。
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
                  {supplier.creditCode ?? '信用代码未收录'}
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
              {/* 中标概况 */}
              <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="中标次数" value={fmtInt(supplier.winCount)} suffix="次" />
                <Stat
                  label="中标总额"
                  value={fmtAmountYuan(supplier.winAmountTotal > 0 ? supplier.winAmountTotal : null).value}
                  suffix={fmtAmountYuan(supplier.winAmountTotal > 0 ? supplier.winAmountTotal : null).unit}
                />
                <Stat label="首次中标" value={supplier.firstWinDate ?? '—'} suffix="" />
                <Stat label="最近中标" value={supplier.lastWinDate ?? '—'} suffix="" />
              </section>

              {/* 工商信息 */}
              <section>
                <p className="mb-2 text-[13px] font-medium text-[var(--color-ink-2)]">工商信息</p>
                {supplier.province || supplier.industry ? (
                  <dl className="divide-y divide-[var(--color-rule)] rounded-xl border border-[var(--color-rule)]">
                    <Row label="注册地" value={[supplier.province, supplier.city, supplier.district].filter(Boolean).join(' ') || '—'} />
                    <Row label="所属行业" value={supplier.industry ?? '—'} />
                    <Row label="法定代表人" value={supplier.legalPerson ?? '—'} />
                    <Row label="注册资本" value={supplier.registeredCapital ?? '—'} />
                    <Row label="成立日期" value={supplier.establishDate ?? '—'} />
                    <Row label="经营状态" value={supplier.businessStatus ?? '—'} />
                    <Row label="企业类型" value={supplier.companyType ?? '—'} />
                    <Row label="企业规模" value={supplier.companyLevel ?? '—'} />
                    <Row label="注册地址" value={supplier.address ?? '—'} />
                    <Row label="经营范围" value={supplier.businessScope ?? '—'} />
                  </dl>
                ) : (
                  <p className="rounded-xl border border-dashed border-[var(--color-rule-2)] px-3.5 py-4 text-[12.5px] text-[var(--color-neutral)]">
                    暂无工商信息 —— 该企业来自新采集公告（CCGP 侧），尚未并入工商档案。
                    新数据里的企业普遍缺少工商字段，这是数据来源的限制，不是展示问题。
                  </p>
                )}
              </section>

              {/* 中标记录 */}
              <section>
                <p className="mb-1 text-[13px] font-medium text-[var(--color-ink-2)]">
                  近期中标项目
                </p>
                <p className="mb-2.5 text-[12px] text-[var(--color-neutral)]">
                  共 {fmtInt(supplier.winCount)} 条，此处显示最近 {supplier.recentWins.length} 条
                </p>
                {supplier.recentWins.length ? (
                  <ul className="divide-y divide-[var(--color-rule)] rounded-xl border border-[var(--color-rule)]">
                    {supplier.recentWins.map((w) => (
                      <li
                        key={`${w.projectNo}|${w.winningDate}`}
                        className="flex items-baseline gap-3 px-3.5 py-2.5"
                      >
                        <div className="min-w-0 flex-1">
                          <p
                            className="truncate text-[13px] text-[var(--color-ink-2)]"
                            title={w.projectName}
                          >
                            {w.projectName}
                          </p>
                          <p className="mt-0.5 text-[11.5px] text-[var(--color-neutral)]">
                            {w.region}
                            {w.category ? ` · ${w.category}` : ''} · {w.winningDate}
                          </p>
                        </div>
                        <span className="tnum shrink-0 text-[13px] text-[var(--color-ink-2)]">
                          {w.winningAmount === null
                            ? '金额未公开'
                            : (() => {
                                const a = fmtAmountYuan(w.winningAmount);
                                return `${a.value} ${a.unit}`;
                              })()}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="rounded-xl border border-dashed border-[var(--color-rule-2)] px-3.5 py-4 text-[12.5px] text-[var(--color-neutral)]">
                    暂无公开中标记录。
                  </p>
                )}
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
        <span className="text-[17px] font-semibold leading-none text-[var(--color-ink)]">
          {value}
        </span>
        {suffix && <span className="text-[12px] text-[var(--color-neutral)]">{suffix}</span>}
      </p>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3 px-3.5 py-2.5">
      <dt className="w-20 shrink-0 text-[12.5px] text-[var(--color-neutral)]">{label}</dt>
      <dd className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-[var(--color-ink-2)]">
        {value}
      </dd>
    </div>
  );
}
