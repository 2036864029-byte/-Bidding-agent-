import { Link } from 'react-router';
import { ArrowRight, BellRing, MessageSquare, Smartphone } from 'lucide-react';
import { Brand } from '@/components/common/Brand';
import { SiteHeader } from '@/components/layout/SiteHeader';

/**
 * 移动端连接 —— 用飞书扫码把「招小智」送进手机。
 *
 * 二维码放在 `public/feishu-qr.png`（464×464 裁切自飞书应用卡片原图，含 8px 白边
 * 共 480×480），用**绝对路径**引用而不是 `import`：它只有 2.5 KB，走 import 会被
 * Vite 内联成 base64 塞进 JS（实测构建后 dist/assets 里根本没有这个文件）。二维码
 * 是要让人扫、可能被保存或打印的图，留成独立文件更合适，替换时也只换图不动代码。
 *
 * 不用运行时生成二维码：入口链接是飞书侧的，前端拿不到，硬编码反而更难维护。
 */

const STEPS = [
  { t: '打开飞书', d: '手机端飞书 App，任意界面右上角进入扫一扫。' },
  { t: '扫码', d: '对准左侧二维码，识别后会自动打开「招小智」。' },
  { t: '直接提问', d: '同一个问题，和网页端走的是同一套知识库与数据。' },
] as const;

export function MobilePage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />

      <main className="flex-1">
        {/* 开篇 */}
        <section className="border-b border-[var(--color-rule)]">
          <div className="mx-auto w-full max-w-[72rem] px-6 py-12">
            <p className="mono-label mb-2">移动端 · 飞书</p>
            <h1 className="max-w-2xl text-[1.9rem] font-semibold leading-tight tracking-[-0.03em] text-[var(--color-ink)] sm:text-[2.15rem]">
              移动端连接
            </h1>
            <p className="mt-3 max-w-2xl text-[14px] leading-relaxed text-[var(--color-muted)]">
              招小智可以直接在飞书里用。
              <span className="text-[var(--color-ink-2)]">扫一次码，手机端就能随时提问</span>
              ，长任务跑完也会把进度推到飞书里，不必守着网页。
            </p>
          </div>
        </section>

        {/* 二维码 + 说明 */}
        <section className="mx-auto w-full max-w-[72rem] px-6 py-12">
          <div className="grid gap-10 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] lg:gap-14">
            {/* 左：二维码 */}
            <div className="lg:sticky lg:top-24 lg:self-start">
              <div className="rounded-[var(--radius-xl)] border border-[var(--color-rule-2)] bg-[var(--color-paper)] p-5 shadow-[var(--shadow-float)]">
                <img
                  src="/feishu-qr.png"
                  alt="飞书扫码进入招小智"
                  width={480}
                  height={480}
                  className="mx-auto block w-full max-w-[17rem] rounded-[var(--radius-sm)]"
                />
                <p className="mono-label mt-4 text-center">请通过飞书扫码访问</p>
              </div>
              <p className="mt-3 text-center text-[12.5px] leading-relaxed text-[var(--color-neutral)]">
                手机相机扫不出来时，用飞书内置的扫一扫
              </p>
            </div>

            {/* 右：说明 */}
            <div className="min-w-0">
              {/* 三步 */}
              <div className="border-b border-[var(--color-rule-2)] pb-2">
                <h2 className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                  <Smartphone className="h-4 w-4 text-[var(--color-accent)]" aria-hidden />
                  怎么连
                </h2>
              </div>
              <ol className="mt-5 space-y-5">
                {STEPS.map((s, i) => (
                  <li key={s.t} className="flex gap-4">
                    <span
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--color-rule-2)] font-mono text-[12px] text-[var(--color-neutral)]"
                      aria-hidden
                    >
                      {i + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="text-[14px] font-medium text-[var(--color-ink)]">{s.t}</p>
                      <p className="mt-1 text-[13px] leading-relaxed text-[var(--color-muted)]">
                        {s.d}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>

              {/* 一个能力的两个面 */}
              <div className="mt-10 grid gap-4 sm:grid-cols-2">
                <div className="rounded-[var(--radius-lg)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] p-5">
                  <MessageSquare
                    className="h-4 w-4 text-[var(--color-accent)]"
                    aria-hidden
                  />
                  <p className="mt-3 text-[14px] font-medium text-[var(--color-ink)]">
                    同样的答案
                  </p>
                  <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--color-muted)]">
                    法规问答、企业查询、价格参考，与网页端是同一套检索与数据，不是另做一份。
                  </p>
                </div>
                <div className="rounded-[var(--radius-lg)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] p-5">
                  <BellRing className="h-4 w-4 text-[var(--color-accent)]" aria-hidden />
                  <p className="mt-3 text-[14px] font-medium text-[var(--color-ink)]">
                    进度推送到飞书
                  </p>
                  <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--color-muted)]">
                    深度分析这类耗时任务，在网页端发起后可以离开，完成时飞书会收到通知。
                  </p>
                </div>
              </div>

              {/* 说明 + 去向 */}
              <div className="mt-8 rounded-[var(--radius-lg)] border border-[var(--color-rule)] px-5 py-4">
                <p className="text-[13px] leading-relaxed text-[var(--color-muted)]">
                  手机屏幕小，适合随时追问和看结论；
                  <span className="text-[var(--color-ink-2)]">
                    涉及表格、图表、多字段对比
                  </span>
                  的场合，回到网页端看会更清楚。两边数据是同一份，换设备不会丢会话上下文以外的东西。
                </p>
              </div>

              <div className="mt-8 flex flex-wrap items-center gap-x-4 gap-y-2">
                <Link
                  to="/chat"
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-rule-2)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--color-ink)] transition-colors duration-[var(--dur-micro)] ease-out hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
                >
                  先在网页上试试
                  <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                </Link>
                <Link
                  to="/faq"
                  className="text-[12.5px] text-[var(--color-muted)] transition-colors duration-[var(--dur-micro)] ease-out hover:text-[var(--color-accent)]"
                >
                  看看别人常问什么
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
            二维码来自飞书应用入口，仅在飞书内有效
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
