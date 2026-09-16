import { useState } from 'react';
import { ChevronDown, ExternalLink, FileText } from 'lucide-react';
import { fmtWanFixed } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { ReferenceProject } from '@/types/inquiry';

/**
 * ProjectReferenceList —— 项目概况参考的相似项目卡列表。
 *
 * 每张卡 = 项目名 + 中标金额（万元）+ 地区/日期/相似度 + 采购人 +
 * 「查看原文」外链（补全后的 ccgp 公告页）+ 产品明细折叠。
 * 无产品明细/无原文链接时降级为对应占位，不伪造。
 */

function ProjectCard({ project }: { project: ReferenceProject }) {
  const [productsOpen, setProductsOpen] = useState(false);
  const hasProducts = project.products.length > 0;

  return (
    <article className="rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3
            className="text-[14px] font-semibold leading-snug tracking-[-0.01em] text-[var(--color-ink)]"
            title={project.projectName}
          >
            {project.projectName}
          </h3>
          <p className="mt-1 text-[12px] text-[var(--color-neutral)]">
            {project.region}
            {project.winningDate ? ` · ${project.winningDate}` : ''}
            {project.purchaser ? ` · ${project.purchaser}` : ''}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="tnum text-[16px] font-semibold text-[var(--color-ink-2)]">
            {fmtWanFixed(project.winningAmountWan)}
          </p>
          <p className="mt-0.5 text-[11.5px] text-[var(--color-muted)]">
            相似度 {Math.round(project.score * 100)}%
          </p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {project.sourceUrl ? (
          <a
            href={project.sourceUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded-md border border-[var(--color-accent)] px-2 py-1 text-[12px] font-medium text-[var(--color-accent)] transition-colors duration-[var(--dur-short)] hover:bg-[var(--color-accent-soft)]"
          >
            <ExternalLink className="h-3 w-3" aria-hidden />
            查看原文
          </a>
        ) : (
          <span className="text-[12px] text-[var(--color-neutral)]">原文链接缺失</span>
        )}

        {hasProducts && (
          <button
            type="button"
            onClick={() => setProductsOpen((o) => !o)}
            aria-expanded={productsOpen}
            className="inline-flex items-center gap-1 rounded-md border border-[var(--color-rule-2)] px-2 py-1 text-[12px] font-medium text-[var(--color-muted)] transition-colors duration-[var(--dur-short)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
          >
            <FileText className="h-3 w-3" aria-hidden />
            产品明细（{project.products.length}）
            <ChevronDown
              className={cn('h-3 w-3 transition-transform duration-[var(--dur-short)]', productsOpen && 'rotate-180')}
              aria-hidden
            />
          </button>
        )}
      </div>

      {hasProducts && productsOpen && (
        <ul className="mt-3 space-y-1.5 border-t border-[var(--color-rule)] pt-3">
          {project.products.map((p, i) => (
            <li key={i} className="flex flex-wrap items-baseline justify-between gap-x-3 text-[12.5px]">
              <span className="min-w-0 truncate text-[var(--color-ink-2)]">
                {p.productName}
                {p.model ? <span className="text-[var(--color-muted)]">（{p.model}）</span> : null}
              </span>
              <span className="tnum shrink-0 text-[var(--color-muted)]">
                {p.quantity ? `${p.quantity}${p.unit ?? ''} · ` : ''}
                {p.unitPriceYuan !== null
                  ? `单价 ${p.unitPriceYuan.toLocaleString('zh-CN')} 元`
                  : '单价未公开'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

export function ProjectReferenceList({ projects }: { projects: ReferenceProject[] }) {
  if (!projects.length) return null;
  return (
    <section className="space-y-2.5">
      {projects.map((p) => (
        <ProjectCard key={p.announcementId} project={p} />
      ))}
    </section>
  );
}
