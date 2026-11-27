import type { ComponentType } from 'react';
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { ArrowRight, Building2, Scale, TrendingUp } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * ModuleGrid —— 三模块入口卡（V2 · 政务蓝）。
 *
 * 变化（2026-11-24）：
 * - 三模块入口从首屏下移，现为独立路由 `/modules` 的主体（`ModulesBoard` 内），
 *   本组件只负责三张卡
 * - 圆角/描边/硬投影为贴纸语言；三张卡的色块走蓝系三档（淡蓝 / 冰蓝 / 淡靛），
 *   不再使用高饱和的青柠与粉色
 * - 删除原装饰 sparkline —— 那条折线不携带任何真实数据
 *
 * 三卡的 <Link> 名称即模块名，外部（测试/导航）按 title 定位，勿改。
 */

interface ModuleDef {
  slug: 'inquiry' | 'supplier' | 'kanban';
  index: string;
  label: string;
  title: string;
  desc: string;
  icon: ComponentType<{ className?: string }>;
  chips: string[];
  /** 卡片色块语义：淡蓝 / 冰蓝 / 淡靛（均为低饱和蓝系） */
  tone: 'mist' | 'ice' | 'indigo';
}

const MODULES: ModuleDef[] = [
  {
    slug: 'inquiry',
    index: '01',
    label: 'INQUIRY',
    title: '智能询价',
    desc: '用语言或文件描述采购需求，双库比对历史中标金额，给出价格参考',
    icon: Scale,
    chips: ['双库比对', '偏离高亮', '历史中标'],
    tone: 'mist',
  },
  {
    slug: 'supplier',
    index: '02',
    label: 'SUPPLIER',
    title: '供应商全景',
    desc: '按地区、行业、关键词筛出企业档案，工商信息与中标业绩直接可核',
    icon: Building2,
    chips: ['资质筛选', '中标业绩', '风险标签'],
    tone: 'ice',
  },
  {
    slug: 'kanban',
    index: '03',
    label: 'KANBAN',
    title: '商机看板',
    desc: '地区热力 · 行业占比 · 时间走势，招投标机会一屏尽览',
    icon: TrendingUp,
    chips: ['地区热力', '行业占比', '时间走势'],
    tone: 'indigo',
  },
];

const TONE_BG: Record<ModuleDef['tone'], string> = {
  mist: 'bg-[var(--color-mist)]',
  ice: 'bg-[var(--color-ice)]',
  indigo: 'bg-[var(--color-indigo)]',
};

export function ModuleGrid() {
  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
      {MODULES.map((m, i) => (
        <ModuleCard key={m.slug} def={m} index={i} />
      ))}
    </div>
  );
}

function ModuleCard({ def, index }: { def: ModuleDef; index: number }) {
  const Icon = def.icon;
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.36, ease: [0.16, 1, 0.3, 1], delay: index * 0.04 }}
      className="h-full"
    >
      <Link
        to={`/${def.slug}`}
        className={cn(
          'sticker group flex h-full min-h-[16rem] flex-col justify-between rounded-[var(--radius-sticker)] bg-white p-6',
          'transition-transform duration-[var(--dur-short)] ease-out hover:-translate-x-0.5 hover:-translate-y-1',
          'focus-visible:outline-offset-4'
        )}
      >
        <div className="min-w-0">
          <div className="flex items-start justify-between gap-3">
            <span
              className={cn(
                'flex h-11 w-11 items-center justify-center rounded-2xl border-2 border-[var(--color-edge)] text-[var(--color-azure-dp)]',
                TONE_BG[def.tone]
              )}
            >
              <Icon className="h-5 w-5" aria-hidden />
            </span>
            <span className="flex items-baseline gap-2">
              <span className="mono-label text-[10px]">{def.label}</span>
              <span className="tnum text-[22px] font-extrabold leading-none tracking-[-0.02em] text-[var(--color-edge)]/25">
                {def.index}
              </span>
            </span>
          </div>

          <h3 className="mt-4 text-[1.18rem] font-extrabold tracking-[-0.01em] text-[var(--color-edge)]">
            {def.title}
          </h3>
          <p className="mt-2 text-[13px] leading-relaxed text-[var(--color-muted)]">{def.desc}</p>
        </div>

        <div className="mt-5">
          <div className="flex flex-wrap gap-1.5">
            {def.chips.map((c) => (
              <span
                key={c}
                className="rounded-full border border-[var(--color-rule-2)] bg-[var(--color-paper-2)] px-2.5 py-1 text-[11px] font-medium text-[var(--color-ink-2)]"
              >
                {c}
              </span>
            ))}
          </div>
          <span className="mt-4 inline-flex items-center gap-1.5 text-[12.5px] font-bold text-[var(--color-azure)]">
            进入模块
            <ArrowRight
              className="h-3.5 w-3.5 transition-transform duration-[var(--dur-short)] ease-out group-hover:translate-x-1"
              aria-hidden
            />
          </span>
        </div>
      </Link>
    </motion.div>
  );
}
