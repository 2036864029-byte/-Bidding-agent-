import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DeviationBadge } from '@/components/inquiry/DeviationBadge';
import { fmtInt } from '@/lib/format';
import {
  computeDeviation,
  deviationLevel,
  fmtDeviation,
  reductionToThreshold,
} from '@/lib/inquiry';
import type { InquiryItem, InquiryQuote } from '@/types/inquiry';

/**
 * QuoteDrawer —— 调价抽屉（右侧滑出）。
 *
 * 交互是**真的**：目标单价可编辑，实时算出新偏离度、是否回到阈值内、以及要降多少。
 * 不是假交互——这也是将来接询价业务后唯一需要替换成真实提交的地方。
 */
export function QuoteDrawer({
  open,
  onClose,
  item,
  quote,
}: {
  open: boolean;
  onClose: () => void;
  item: InquiryItem | null;
  quote: InquiryQuote | null;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [target, setTarget] = useState(0);
  const [noted, setNoted] = useState(false);

  // 打开时默认把目标价设到「阈值」（一个合理的谈判目标）
  useEffect(() => {
    if (open && item && quote) {
      setTarget(Math.min(quote.unitPrice, item.budgetThreshold || quote.unitPrice));
      setNoted(false);
    }
  }, [open, item, quote]);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const ready = open && item && quote;

  return (
    <AnimatePresence>
      {ready && (
        <motion.div
          key="quote-drawer"
          className="fixed inset-0 z-[80]"
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
            aria-label={`调价 · ${quote.supplierName}`}
            tabIndex={-1}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
            className="absolute right-0 top-0 flex h-full w-full max-w-[26rem] flex-col border-l border-[var(--color-rule)] bg-[var(--color-paper)] shadow-[var(--shadow-float)] outline-none"
          >
            <header className="flex items-start justify-between gap-3 border-b border-[var(--color-rule)] px-5 py-4">
              <div className="min-w-0">
                <p className="mono-label mb-1">调价</p>
                <h2 className="truncate text-[15px] font-semibold text-[var(--color-ink)]">
                  {quote.supplierName}
                </h2>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="关闭调价面板"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </header>

            <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
              {/* 标的 */}
              <section>
                <p className="mb-1.5 text-[12px] text-[var(--color-neutral)]">询价标的</p>
                <p className="text-[14px] font-medium text-[var(--color-ink)]">{item.itemName}</p>
                <p className="mt-0.5 text-[13px] text-[var(--color-muted)]">
                  {item.specifications} · {fmtInt(item.targetQuantity)} {item.unit}
                </p>
              </section>

              {/* 当前报价 */}
              <section className="rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-4 py-3">
                <p className="mb-2 text-[12px] text-[var(--color-neutral)]">当前报价</p>
                <div className="flex items-baseline gap-2">
                  <span className="text-[22px] font-semibold leading-none text-[var(--color-ink)]">
                    ¥{fmtInt(quote.unitPrice)}
                  </span>
                  <span className="text-[13px] text-[var(--color-neutral)]">/ {item.unit}</span>
                </div>
                <p className="mt-1.5 text-[13px] text-[var(--color-muted)]">
                  合计 ¥{fmtInt(quote.totalPrice)}
                </p>
                <div className="mt-2.5">
                  <DeviationBadge
                    level={deviationLevel(quote.unitPrice, item)}
                    rate={computeDeviation(quote.unitPrice, item.benchmarkUnitPrice)}
                  />
                </div>
                {quote.reason && (
                  <p className="mt-2.5 text-[12px] leading-relaxed text-[var(--color-muted)]">
                    {quote.reason}
                  </p>
                )}
              </section>

              {/* 基准与阈值 */}
              <section className="grid grid-cols-2 gap-3">
                <div className="rounded-xl border border-[var(--color-rule)] px-3.5 py-3">
                  <p className="text-[12px] text-[var(--color-neutral)]">内部基准单价</p>
                  <p className="tnum mt-1 text-[16px] font-semibold text-[var(--color-ink)]">
                    ¥{fmtInt(item.benchmarkUnitPrice)}
                  </p>
                </div>
                <div className="rounded-xl border border-[var(--color-rule)] px-3.5 py-3">
                  <p className="text-[12px] text-[var(--color-neutral)]">偏离阈值</p>
                  <p className="tnum mt-1 text-[16px] font-semibold text-[var(--color-ink)]">
                    ¥{fmtInt(item.budgetThreshold)}
                  </p>
                </div>
              </section>

              {/* 目标价试算 */}
              <section>
                <label
                  htmlFor="target-price"
                  className="mb-1.5 block text-[12px] text-[var(--color-neutral)]"
                >
                  目标单价（试算）
                </label>
                <div className="flex items-center gap-2">
                  <span className="text-[15px] text-[var(--color-neutral)]">¥</span>
                  <input
                    id="target-price"
                    type="number"
                    min={0}
                    value={Number.isFinite(target) ? target : 0}
                    onChange={(e) => setTarget(Number(e.target.value))}
                    className="tnum h-10 w-full rounded-[var(--radius-md)] border border-[var(--color-rule-2)] bg-[var(--color-paper)] px-3 text-[15px] text-[var(--color-ink)] transition-colors duration-[var(--dur-micro)] ease-out focus:border-[var(--color-accent)] focus:outline-none"
                  />
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <DeviationBadge
                    level={deviationLevel(target, item)}
                    rate={computeDeviation(target, item.benchmarkUnitPrice)}
                  />
                  <span className="text-[12px] text-[var(--color-muted)]">
                    合计 ¥{fmtInt(target * item.targetQuantity)}
                  </span>
                </div>

                <p className="mt-2.5 text-[12px] leading-relaxed text-[var(--color-muted)]">
                  {reductionToThreshold(quote.unitPrice, item) > 0
                    ? `要回到阈值内，单价需较当前下调 ¥${fmtInt(
                        reductionToThreshold(quote.unitPrice, item)
                      )}（${fmtDeviation(
                        computeDeviation(item.budgetThreshold, quote.unitPrice)
                      )}）。`
                    : '当前报价已在阈值之内，无需压价。'}
                </p>
              </section>
            </div>

            <footer className="border-t border-[var(--color-rule)] px-5 py-4">
              <Button className="w-full" onClick={() => setNoted(true)} disabled={noted}>
                {noted ? '已记录调价意向' : '记录调价意向'}
              </Button>
              {noted && (
                <p className="mt-2 text-center text-[12px] text-[var(--color-muted)]">
                  演示环境：真实提交将在询价业务接入后打通。
                </p>
              )}
            </footer>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
