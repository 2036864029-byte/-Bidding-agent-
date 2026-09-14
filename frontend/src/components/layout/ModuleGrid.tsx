import type { ComponentType } from 'react';
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { ArrowRight, Building2, Scale, TrendingUp } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * ModuleGrid —— 三模块入口卡（D4）。
 * 增强：明确边框 + 高位阴影 + hover 上浮 + 边框变钴蓝轻发光（card-glow，非旋转渐变）。
 * 能力可视化：能力标签行 + 装饰 sparkline（数据未就绪，用能力点而非假数字）。
 */

interface ModuleDef {
  slug: 'inquiry' | 'supplier' | 'kanban';
  label: string;
  title: string;
  desc: string;
  icon: ComponentType<{ className?: string }>;
  chips: string[];
  /** sparkline 折线点（viewBox 0 0 128 36），纯装饰 */
  spark: string;
}

const MODULES: ModuleDef[] = [
  {
    slug: 'inquiry',
    label: 'INQUIRY',
    title: '智能询价',
    desc: '用语言或文件描述采购需求，双库比对历史中标金额，给出价格参考',
    icon: Scale,
    chips: ['双库比对', '偏离高亮', '历史中标'],
    spark: '0,30 16,26 32,28 48,20 64,22 80,14 96,17 112,9 128,12',
  },
  {
    slug: 'supplier',
    label: 'SUPPLIER',
    title: '供应商全景',
    desc: '资质 · 履约能力 · 风险标签多维筛选，雷达图一眼看透供应商画像',
    icon: Building2,
    chips: ['资质筛选', '雷达评分', '风险标签'],
    spark: '0,26 16,24 32,20 48,22 64,16 80,18 96,12 112,14 128,8',
  },
  {
    slug: 'kanban',
    label: 'KANBAN',
    title: '商机看板',
    desc: '地区热力 · 行业占比 · 时间走势，招投标机会一屏尽览',
    icon: TrendingUp,
    chips: ['地区热力', '行业占比', '时间走势'],
    spark: '0,28 16,30 32,22 48,26 64,18 80,20 96,12 112,16 128,6',
  },
];

export function ModuleGrid() {
  return (
    <section className="mt-7">
      <div className="grid grid-cols-1 gap-5 md:grid-cols-3">
        {MODULES.map((m, i) => (
          <ModuleCard key={m.slug} def={m} index={i} />
        ))}
      </div>
    </section>
  );
}

function ModuleCard({ def, index }: { def: ModuleDef; index: number }) {
  const Icon = def.icon;
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.42, ease: [0.16, 1, 0.3, 1], delay: index * 0.06 }}
      className="h-full"
    >
      <Link
        to={`/${def.slug}`}
        className={cn(
          'card-glow group flex h-full min-h-[15rem] flex-col justify-between rounded-2xl',
          'border border-[var(--color-rule-2)] bg-[var(--color-paper)] p-5',
          'shadow-[var(--shadow-float)]',
          'focus-visible:outline-offset-4'
        )}
      >
        <div>
          <div className="flex items-start justify-between">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
              <Icon className="h-5 w-5" aria-hidden />
            </span>
            <span className="mono-label text-[10px]">{def.label}</span>
          </div>
          <h2 className="mt-4 text-lg font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
            {def.title}
          </h2>
          <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--color-muted)]">{def.desc}</p>
        </div>

        <div className="mt-5">
          <div className="flex flex-wrap gap-1.5">
            {def.chips.map((c) => (
              <span
                key={c}
                className="rounded-md border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-2 py-1 text-[11px] text-[var(--color-ink-2)]"
              >
                {c}
              </span>
            ))}
          </div>
          <svg viewBox="0 0 128 36" className="mt-3 h-7 w-full" aria-hidden>
            <polyline
              points={def.spark}
              fill="none"
              stroke="var(--color-accent)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="mt-2.5 inline-flex items-center gap-1.5 font-mono text-[11px] font-medium tracking-wide text-[var(--color-accent)]">
            进入模块
            <ArrowRight
              className="h-3.5 w-3.5 transition-transform duration-[var(--dur-short)] ease-out group-hover:translate-x-0.5"
              aria-hidden
            />
          </span>
        </div>
      </Link>
    </motion.div>
  );
}
