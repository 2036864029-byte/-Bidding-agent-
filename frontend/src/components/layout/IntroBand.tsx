/**
 * @deprecated 2026-11-24 起不再挂载。
 *
 * 首屏已由 `HeroSection`（淡青柠洗底 + 超重中文标题 + 贴纸语言产品卡）取代；
 * 本组件的满幅蓝径向渐变与「强调色 <5% 视口」的约束冲突，故从 `LandingLayout` 移除。
 * 文件暂时保留以免误删历史实现，确认无需回滚后可整体删除。
 *
 * IntroBand —— 页面介绍部分（D3 v3）。
 * 文字与色彩一体化：介绍文字坐在**晕染渐变背景**上（自上而下 深→浅→无），
 * 渐变以径向柔化 + blur 实现晕染，边缘透明融入页面背景，无边框、无分割线。
 */
export function IntroBand() {
  return (
    <section className="relative overflow-hidden">
      {/* 晕染渐变：径向自上而下 深→浅→无，柔化边缘融入页面 */}
      <div
        aria-hidden
        className="absolute -inset-2"
        style={{
          background:
            'radial-gradient(130% 130% at 50% -15%, oklch(46% 0.22 258) 0%, oklch(58% 0.21 256) 24%, oklch(77% 0.13 256) 50%, oklch(93% 0.03 256) 76%, transparent 95%)',
          filter: 'blur(6px)',
        }}
      />
      <div className="relative mx-auto max-w-[72rem] px-6 py-7">
        <h1 className="max-w-2xl text-[1.5rem] font-semibold tracking-[-0.02em] text-white sm:text-[1.7rem]">
          从询价、供应商到商机，一站式智能分析
        </h1>
        <p className="mt-1.5 max-w-lg text-[13px] leading-relaxed text-[oklch(95%/0.92)]">
          数据当前为演示形态；对话模块已接真实后端，其余模块将在数据就绪后接入。
        </p>
      </div>
    </section>
  );
}
