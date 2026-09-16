import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { ArrowRight, ChevronRight, Quote } from 'lucide-react';
import { SiteHeader } from '@/components/layout/SiteHeader';
import { Brand } from '@/components/common/Brand';
import { Markdown } from '@/components/chat/Markdown';
import { FAQ, type FaqItem } from './faqData';

/**
 * 常见问题 —— 三类高频问答。
 *
 * Hallmark · pre-emit critique: P4 H4 E4 S5 R4 V4
 * Hallmark · genre: modern-minimal · macrostructure: Conversational FAQ · theme: cobalt-tuned
 *          · enrichment: none (typography only) · nav: N1（既有 SiteHeader，共享外壳）
 *
 * **答案不是手写的** —— 逐字取自项目知识库的检索结果（`POST /chat` → kb_answer），
 * 每条后面列出命中的真实资料与章节。法条口径写错代价太大，所以让系统自己答。
 * 数据生成脚本：`frontend/.shots/faq_gen.py`；改答案请改知识库，别改数据文件。
 *
 * 版式：问答手风琴（点击展开），三类用分段控件切换 —— 每类只 5 道，一屏能扫完。
 */

/**
 * 清理资料名：去掉 PDF 解析产物与来源站后缀。
 *   `46b90fd2165bf92c_招标投标法` → `招标投标法`（哈希前缀是解析器加的，不是书名）
 *   末尾的 `(Z-Library)` / `(OCR)` 同理 —— 是文件从哪来的，不属于资料名。
 */
function cleanDocName(s: string): string {
  return s
    .replace(/^[0-9a-f]{16}_/, '')
    .replace(/\((?:Z-Library|z-library|OCR|ocr)\)/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/** 正文里的【来源N】是对话页的交互机制，静态页上留着是悬空标记 —— 剥掉。 */
function stripCitationMarkers(s: string): string {
  return s.replace(/【来源\d+】/g, '').replace(/[ \t]+\n/g, '\n').trim();
}

/** 把一条答案的 sources 汇总成「几部资料 · 命中几处」。 */
function summarizeSources(item: FaqItem) {
  const seen = new Set<string>();
  const docs: string[] = [];
  for (const s of item.sources) {
    const d = cleanDocName(s.doc);
    if (d && !seen.has(d)) {
      seen.add(d);
      docs.push(d);
    }
  }
  return { docs, hits: item.sources.length };
}

function Item({ item, index }: { item: FaqItem; index: number }) {
  const [open, setOpen] = useState(false);
  const { docs, hits } = useMemo(() => summarizeSources(item), [item]);
  const panelId = `faq-panel-${index}`;

  return (
    <div className="border-b border-[var(--color-rule)]">
      <h3>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={panelId}
          className="group flex w-full items-start gap-3 py-4 text-left"
        >
          <ChevronRight
            className={`mt-0.5 h-4 w-4 shrink-0 text-[var(--color-neutral)] transition-transform duration-[var(--dur-short)] ease-out ${
              open ? 'rotate-90 text-[var(--color-accent)]' : 'group-hover:text-[var(--color-ink)]'
            }`}
            aria-hidden
          />
          <span className="min-w-0 text-[14px] font-medium leading-relaxed text-[var(--color-ink)] group-hover:text-[var(--color-accent)]">
            {item.q}
          </span>
        </button>
      </h3>

      {/* 手风琴：grid-template-rows 0fr → 1fr（内容高度未知时唯一不用 JS 量高的做法）。
          `inert` 而非 `hidden` —— hidden 会 display:none，动画直接失效；
          inert 只把内容移出焦点与无障碍树，不动布局，收起动画才跑得起来。 */}
      <div
        id={panelId}
        inert={!open}
        className={`grid transition-[grid-template-rows] duration-[var(--dur-short)] ease-out motion-reduce:transition-none ${
          open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
        }`}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="pb-5 pl-7 pr-2">
            <div className="text-[13.5px] leading-relaxed text-[var(--color-ink-2)]">
              <Markdown>{stripCitationMarkers(item.answer)}</Markdown>
            </div>

            {docs.length > 0 && (
              <div className="mt-4 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2.5">
                <p className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.06em] text-[var(--color-neutral)]">
                  <Quote className="h-3 w-3" aria-hidden />
                  检索依据 · {docs.length} 部资料 · 命中 {hits} 处
                </p>
                <ul className="mt-1.5 space-y-0.5">
                  {docs.map((d) => (
                    <li key={d} className="truncate text-[12px] text-[var(--color-muted)]">
                      {d}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function FaqPage() {
  const [active, setActive] = useState(FAQ[0].id);
  const cat = FAQ.find((c) => c.id === active) ?? FAQ[0];

  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />

      <main className="flex-1">
        {/* 开篇：这些答案从哪来 —— 说清楚，比自称"权威"有用 */}
        <section className="border-b border-[var(--color-rule)]">
          <div className="mx-auto w-full max-w-[72rem] px-6 py-12">
            <h1 className="max-w-2xl text-[1.9rem] font-semibold leading-tight tracking-[-0.03em] text-[var(--color-ink)] sm:text-[2.15rem]">
              常见问题
            </h1>
            <p className="mt-3 max-w-2xl text-[14px] leading-relaxed text-[var(--color-muted)]">
              按提问者身份分三类，每类五道最常被问到的题。
              <span className="text-[var(--color-ink-2)]">
                答案由系统从法规知识库中检索生成
              </span>
              ，每题后面列出命中的资料与章节 —— 可以照着去核对原文，而不是只能相信这段文字。
            </p>
          </div>
        </section>

        <section className="mx-auto w-full max-w-[72rem] px-6 py-10">
          <div className="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
            {/* 分类：左侧分段控件（桌面）／顶部横向（移动） */}
            <nav aria-label="问题分类" className="lg:sticky lg:top-24 lg:self-start">
              <div className="flex gap-1.5 overflow-x-auto lg:flex-col lg:overflow-visible">
                {FAQ.map((c) => {
                  const on = c.id === active;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setActive(c.id)}
                      className={`shrink-0 rounded-[var(--radius-sm)] border px-3 py-2 text-left transition-colors duration-[var(--dur-micro)] ease-out ${
                        on
                          ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)]'
                          : 'border-[var(--color-rule)] hover:border-[var(--color-rule-2)] hover:bg-[var(--color-paper-2)]'
                      }`}
                    >
                      <span
                        className={`block whitespace-nowrap text-[13px] font-medium lg:whitespace-normal ${
                          on ? 'text-[var(--color-accent)]' : 'text-[var(--color-ink)]'
                        }`}
                      >
                        {c.name}
                      </span>
                      <span className="mt-0.5 hidden text-[12px] leading-relaxed text-[var(--color-muted)] lg:block">
                        {c.blurb}
                      </span>
                    </button>
                  );
                })}
              </div>
              <p className="mt-5 hidden text-[12px] leading-relaxed text-[var(--color-neutral)] lg:block">
                没找到想问的？直接去
                <Link to="/chat" className="mx-1 text-[var(--color-accent)] hover:underline">
                  对话
                </Link>
                提问，答案同样会带引用来源。
              </p>
            </nav>

            {/* 问题列表 */}
            <div className="min-w-0">
              <div className="flex items-baseline justify-between gap-4 border-b border-[var(--color-rule-2)] pb-2">
                <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                  {cat.name}
                </h2>
                <span className="font-mono text-[11px] tracking-[0.06em] text-[var(--color-neutral)]">
                  {cat.items.length} 问
                </span>
              </div>
              <div>
                {cat.items.map((it, i) => (
                  <Item key={it.q} item={it} index={i} />
                ))}
              </div>

              <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2">
                <Link
                  to="/chat"
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-rule-2)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--color-ink)] transition-colors duration-[var(--dur-micro)] ease-out hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
                >
                  去对话里追问
                  <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                </Link>
                <Link
                  to="/intro"
                  className="text-[12.5px] text-[var(--color-muted)] transition-colors duration-[var(--dur-micro)] ease-out hover:text-[var(--color-accent)]"
                >
                  了解系统能做什么
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-[var(--color-rule)]">
        <div className="mx-auto flex w-full max-w-[72rem] flex-wrap items-center gap-x-4 gap-y-2 px-6 py-6">
          <Brand compact />
          <p className="text-[12.5px] text-[var(--color-muted)]">
            答案来自法规知识库检索，不构成法律意见
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
