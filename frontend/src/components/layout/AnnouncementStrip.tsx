/**
 * AnnouncementStrip —— 顶部公告条（V2 · 政务蓝）。
 *
 * 对应参考站最上方那条通栏：只做一条事实说明，不做营销文案。
 * 深蓝底 + 白字（#0031C2 对白 ≈ 8.6:1），是页面里唯一的整条重色带，
 * 与首屏产品卡的主渐变同族，不再使用跳脱的高饱和色。
 */
export function AnnouncementStrip() {
  return (
    <div className="border-b-2 border-[var(--color-edge)] bg-[var(--color-azure-dp)]">
      <p className="mx-auto w-full max-w-[72rem] px-6 py-2 text-center text-[12.5px] font-semibold leading-relaxed text-white">
        数据当前为演示形态；对话模块已接真实后端，其余模块将在数据就绪后接入。
      </p>
    </div>
  );
}
