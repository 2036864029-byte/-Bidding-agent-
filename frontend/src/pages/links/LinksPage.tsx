import { Link } from 'react-router';
import { ArrowUpRight, ShieldCheck } from 'lucide-react';
import { SiteHeader } from '@/components/layout/SiteHeader';
import { Brand } from '@/components/common/Brand';
import { GOVERNMENT_LINKS, VERIFIED_ON, type GovLink } from './linksData';

/**
 * 友情链接 —— 招投标领域的政府级网站索引。
 *
 * Hallmark · pre-emit critique: P4 H4 E4 S5 R4 V4
 * Hallmark · genre: modern-minimal · macrostructure: Index-First · theme: cobalt-tuned
 *          · enrichment: none (typography only) · nav: N1（既有 SiteHeader，共享外壳）
 *
 * 版式：Index-First —— 页面本身就是一份链接索引，没有叙事、没有大标题。
 * 每条都外链跳转；收录范围与核验日期写在开头，政府网站改版频繁，读者需要知道这页有多新。
 *
 * 收录门槛（见 linksData.ts）：只收 .gov.cn 与国家级行业平台，
 * 不收 .com 域名的跳转占位页与主办单位不明的站点。
 */

function Row({ item }: { item: GovLink }) {
  return (
    <li className="border-b border-[var(--color-rule)]">
      <a
        href={item.url}
        target="_blank"
        rel="noreferrer noopener"
        className="group grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 py-4 transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)]"
      >
        <div className="min-w-0 pl-0.5">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[14px] font-medium text-[var(--color-ink)] group-hover:text-[var(--color-accent)]">
              {item.name}
            </span>
            {item.region && (
              <span className="rounded-[var(--radius-sm)] border border-[var(--color-rule-2)] px-1.5 py-px text-[11px] text-[var(--color-muted)]">
                {item.region}
              </span>
            )}
          </p>
          <p className="mt-1 font-mono text-[11.5px] tracking-[0.02em] text-[var(--color-neutral)]">
            {item.domain}
          </p>
          <p className="mt-1.5 max-w-2xl text-[12.5px] leading-relaxed text-[var(--color-muted)]">
            {item.note}
          </p>
        </div>
        <ArrowUpRight
          className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out group-hover:text-[var(--color-accent)]"
          aria-hidden
        />
        <span className="sr-only">（在新窗口打开外部网站）</span>
      </a>
    </li>
  );
}

export function LinksPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />

      <main className="flex-1">
        <div className="mx-auto w-full max-w-[72rem] px-6 py-12">
          {/* Index-First 的开头是一段说明，不是标语 */}
          <h1 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
            友情链接
          </h1>
          <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-[var(--color-muted)]">
            招投标领域的政府级网站索引，分国家级与省市两组。
            这些是公告原文与政策法规的权威出处 —— 本系统检索到的中标公告，原文链接大多指向其中的平台。
          </p>
          <p className="mt-2 flex items-center gap-1.5 text-[12.5px] text-[var(--color-neutral)]">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden />
            收录 {GOVERNMENT_LINKS.reduce((n, g) => n + g.items.length, 0)} 个站点，均于 {VERIFIED_ON} 逐个核验可达
          </p>

          <div className="mt-10 grid gap-x-14 gap-y-10 lg:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
            {/* 左侧：分组标签（桌面吸顶）／移动端收纳为一行 */}
            <nav aria-label="链接分组" className="lg:sticky lg:top-24 lg:self-start">
              <ul className="flex gap-4 lg:flex-col lg:gap-3">
                {GOVERNMENT_LINKS.map((g) => (
                  <li key={g.id} className="min-w-0">
                    <a
                      href={`#${g.id}`}
                      className="text-[13px] font-medium text-[var(--color-ink)] transition-colors duration-[var(--dur-micro)] ease-out hover:text-[var(--color-accent)]"
                    >
                      {g.name}
                      <span className="ml-1.5 font-mono text-[11px] text-[var(--color-neutral)]">
                        {g.items.length}
                      </span>
                    </a>
                    <p className="mt-1 hidden text-[12px] leading-relaxed text-[var(--color-muted)] lg:block">
                      {g.blurb}
                    </p>
                  </li>
                ))}
              </ul>
            </nav>

            {/* 右侧：链接列表 */}
            <div className="min-w-0 space-y-10">
              {GOVERNMENT_LINKS.map((g) => (
                <section key={g.id} id={g.id} className="scroll-mt-24">
                  <div className="flex items-baseline justify-between gap-4 border-b border-[var(--color-rule-2)] pb-2">
                    <h2 className="text-[14.5px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                      {g.name}
                    </h2>
                    <span className="font-mono text-[11px] tracking-[0.06em] text-[var(--color-neutral)]">
                      {g.items.length} 站
                    </span>
                  </div>
                  <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--color-muted)] lg:hidden">
                    {g.blurb}
                  </p>
                  <ul>
                    {g.items.map((it) => (
                      <Row key={it.url} item={it} />
                    ))}
                  </ul>
                </section>
              ))}

              <p className="text-[12px] leading-relaxed text-[var(--color-neutral)]">
                链接均指向外部网站，内容由各主办单位负责，与本系统无关。
                政府网站改版或迁移较频繁，若发现链接失效，请以站内检索结果中的原文链接为准。
              </p>
            </div>
          </div>

          <div className="mt-10 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-[var(--color-rule)] pt-6">
            <Link
              to="/chat"
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-rule-2)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--color-ink)] transition-colors duration-[var(--dur-micro)] ease-out hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
            >
              回对话里提问
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
            <Link
              to="/faq"
              className="text-[12.5px] text-[var(--color-muted)] transition-colors duration-[var(--dur-micro)] ease-out hover:text-[var(--color-accent)]"
            >
              看常见问题
            </Link>
          </div>
        </div>
      </main>

      <footer className="border-t border-[var(--color-rule)]">
        <div className="mx-auto flex w-full max-w-[72rem] flex-wrap items-center gap-x-4 gap-y-2 px-6 py-6">
          <Brand compact />
          <p className="text-[12.5px] text-[var(--color-muted)]">
            外部链接内容由各主办单位负责
          </p>
          <Link
            to="/chat"
            className="ml-auto text-[12.5px] text-[var(--color-accent)] transition-colors duration-[var(--dur-micro)] ease-out hover:underline"
          >
            开始提问
          </Link>
        </div>
      </footer>
    </div>
  );
}
