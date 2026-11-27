import { ModuleGrid } from '@/components/layout/ModuleGrid';

/**
 * ModulesBoard —— 三模块区的内容块（标题 + 三张入口卡）。
 *
 * 只负责内容，不含页面外壳（导航/对话框由 `ModulesPage` 提供）。
 * 抽出来是为了让「三模块」既能作为独立路由 /modules 的主体，
 * 又不会把页面壳复制一遍。
 */
export function ModulesBoard() {
  return (
    <div className="mx-auto flex w-full max-w-[72rem] flex-1 flex-col justify-center px-6 py-12">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div className="min-w-0">
          <p className="mono-label">THREE MODULES</p>
          <h1 className="mt-2 text-[clamp(1.5rem,2.6vw,2rem)] font-extrabold tracking-[-0.01em] text-[var(--color-edge)]">
            从询价、供应商到商机，一站式智能分析
          </h1>
          <p className="mt-2 max-w-xl text-[13.5px] leading-relaxed text-[var(--color-muted)]">
            对话控制台是主入口；另外三个模块把同一批数据的某一种用法展开成独立界面。
            每个模块页底部都保留同一条对话框。
          </p>
        </div>
        <p className="shrink-0 text-[12px] font-medium text-[var(--color-muted)]">
          点卡片进入模块 · 底部随时可提问
        </p>
      </div>

      <div className="mt-10">
        <ModuleGrid />
      </div>
    </div>
  );
}
