import { ArrowRight, Check, MapPin, MessageCircle, Sparkles } from 'lucide-react';
import { useNavigate } from 'react-router';
import { cn } from '@/lib/utils';

/**
 * HeroSection —— 首页首屏（2026-11-24 · 按参考图还原）。
 *
 * 右侧示例卡完全照参考图取色与配比（色值为原图实测像素，见 index.css 注释）：
 *   - 卡面饱和青柠 #F2FE94 + 右下绿晕，边 3px #070808，投影 5px 无模糊
 *   - 白气泡 #F5F5F0 / 冰蓝气泡 #DEFAFF，圆角矩形、无描边
 *   - 三枚漂浮标签压在卡片左右边缘**大部分在外**，蜜桃 / 薄荷 / 粉三段渐变
 *   - 右下墨色胶囊、`NEXT STEP` 浅色水印、#0E59FF 行动块
 *
 * 比例：整卡收窄到 26rem，字号按参考图与卡宽的比例折算（参考图卡宽 675px）。
 * 标签对位用「行内绝对定位」挂到所属行，行高变化不串位；问句右对齐留出左侧、
 * 答句左对齐留出右侧，标签只压卡面不压字。
 */

type Tone = 'peach' | 'mint' | 'pink';
type Chip = { title: string; sub: string; tone: Tone };

const CHIPS: Chip[] = [
  { title: '查法规', sub: '来源可核', tone: 'peach' },
  { title: '筛供应商', sub: '条件筛选', tone: 'mint' },
  { title: '看商机', sub: '走势一览', tone: 'pink' },
];

const CHIP_CLASS: Record<Tone, string> = {
  peach: 'chip-peach',
  mint: 'chip-mint',
  pink: 'chip-pink',
};

function FloatChip({ chip, className }: { chip: Chip; className?: string }) {
  return (
    <div
      className={cn(
        'absolute z-10 w-[7.4rem] rounded-[1.05rem] border-[3px] border-[var(--color-edge)] px-2 py-2.5 text-center',
        'shadow-[4px_4px_0_var(--color-edge)]',
        CHIP_CLASS[chip.tone],
        className
      )}
    >
      <p className="text-[19px] font-extrabold leading-[1.1] tracking-[-0.02em] text-[var(--color-edge)]">
        {chip.title}
      </p>
      <p className="mt-1 text-[11px] font-bold leading-tight text-[var(--color-edge)]/85">
        {chip.sub}
      </p>
    </div>
  );
}

export function HeroSection() {
  const navigate = useNavigate();

  return (
    <section className="hero-wash relative flex flex-1 flex-col overflow-hidden border-b-[3px] border-[var(--color-edge)]">
      <div className="relative mx-auto grid w-full max-w-[72rem] flex-1 grid-cols-1 items-center gap-x-14 gap-y-12 px-6 pb-8 pt-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.06fr)] lg:py-12">
        {/* ── 左：标题 + 副文 ── */}
        <div className="min-w-0">
          <span className="inline-flex items-center gap-1.5 rounded-full border-2 border-[var(--color-edge)] bg-white px-3 py-1 text-[11.5px] font-semibold text-[var(--color-edge)]">
            <Sparkles className="h-3 w-3 text-[var(--color-azure)]" aria-hidden />
            招投标智能助手 · 免登录
          </span>

          <h1 className="mt-5 text-[clamp(2.1rem,4.8vw,3.6rem)] font-extrabold leading-[1.06] tracking-[-0.01em] text-[var(--color-edge)] [overflow-wrap:anywhere]">
            招投标的问题
            <br />
            问一句就够
          </h1>

          <p className="mt-5 max-w-lg text-[15px] leading-[1.75] text-[var(--color-ink-2)]">
            法规条文、企业工商、中标记录、你上传的标书 —— 四类数据接在同一套检索上。
            答案带来源，查不到就直说。
          </p>

          {/* 深蓝底上 --color-muted 只有约 4.35:1，改用 ink-2（约 5.7:1） */}
          <ul className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12.5px] text-[var(--color-ink-2)]">
            {['匿名单会话', '答案可点开来源', '数据不出本机'].map((t) => (
              <li key={t} className="flex items-center gap-1.5">
                <Check className="h-3.5 w-3.5 text-[var(--color-azure-dp)]" aria-hidden />
                {t}
              </li>
            ))}
          </ul>
        </div>

        {/* ── 右：对话示例卡（比例收窄，标签大部分悬在卡外） ── */}
        <div className="relative mx-auto w-full max-w-[26rem]">
          {/* 窄屏：标签退化为一行 */}
          <div className="mb-3 flex flex-wrap gap-2 lg:hidden">
            {CHIPS.map((c) => (
              <span
                key={c.title}
                className={cn(
                  'rounded-full border-2 border-[var(--color-edge)] px-3 py-1.5 text-[11.5px] font-bold text-[var(--color-edge)]',
                  CHIP_CLASS[c.tone]
                )}
              >
                {c.title} · {c.sub}
              </span>
            ))}
          </div>

          <div className="card-grad relative rounded-[1.65rem] border-[3px] border-[var(--color-edge)] px-4 py-3.5 shadow-[5px_5px_0_var(--color-edge)]">
            {/* 卡头：粗体标题 + 气泡图标 ←→ 右侧状态词 */}
            <div className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5 text-[19px] font-extrabold tracking-[-0.02em] text-[var(--color-edge)]">
                对话中
                <MessageCircle className="h-[18px] w-[18px] fill-white" aria-hidden />
              </span>
              <span className="text-[13px] font-bold text-[var(--color-edge)]">可核验</span>
            </div>
            <div className="mt-2 h-[1.5px] bg-[var(--color-edge)]/90" aria-hidden />

            {/* 两轮问答 */}
            <div className="mt-3.5 space-y-2.5">
              <div className="relative flex justify-end">
                <p className="w-fit max-w-[76%] rounded-xl bg-[var(--color-bubble-warm)] px-3.5 py-2.5 text-[13.5px] font-bold leading-[1.55] text-[var(--color-edge)]">
                  招标方式有哪些？
                </p>
                <FloatChip
                  chip={CHIPS[0]}
                  className="-left-20 top-1/2 hidden -translate-y-1/2 lg:block xl:-left-24"
                />
              </div>

              <div className="relative flex justify-start">
                <p className="w-fit max-w-[82%] rounded-xl bg-[var(--color-bubble-sky)] px-3.5 py-2.5 text-[13.5px] font-bold leading-[1.55] text-[var(--color-edge)]">
                  招标方式分为公开招标和邀请招标两种……
                </p>
              </div>

              <div className="relative flex justify-end">
                <p className="w-fit max-w-[76%] rounded-xl bg-[var(--color-bubble-warm)] px-3.5 py-2.5 text-[13.5px] font-bold leading-[1.55] text-[var(--color-edge)]">
                  推荐几家建材类的供应商？
                </p>
                <FloatChip
                  chip={CHIPS[1]}
                  className="-left-20 top-1/2 hidden -translate-y-1/2 lg:block xl:-left-24"
                />
              </div>

              <div className="relative flex justify-start">
                <p className="w-fit max-w-[74%] rounded-xl bg-[var(--color-bubble-sky)] px-3.5 py-2.5 text-[13.5px] font-bold leading-[1.55] text-[var(--color-edge)]">
                  正在根据算法为您推荐中，请稍后
                </p>
                <FloatChip
                  chip={CHIPS[2]}
                  className="-right-16 top-1/2 hidden -translate-y-1/2 lg:block xl:-right-24"
                />
              </div>
            </div>

            {/* 墨色胶囊（对应参考图「确认发送」的位置与体量） */}
            <div className="mt-3 flex justify-end">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--color-edge)] px-4 py-2 text-[13px] font-bold text-white">
                <span className="relative flex h-1.5 w-1.5" aria-hidden>
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white opacity-70" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-white" />
                </span>
                生成中
              </span>
            </div>

            {/* NEXT STEP 水印 + 行动块 */}
            <p className="mt-1 text-right text-[22px] font-extrabold tracking-[0.16em] text-[var(--color-edge)]/20">
              NEXT STEP
            </p>
            <div className="mt-1 flex items-center gap-2.5 rounded-xl bg-[var(--color-step-blue)] px-3.5 py-2.5">
              <MapPin className="h-4 w-4 shrink-0 text-white" aria-hidden />
              <div className="min-w-0">
                <p className="text-[11px] font-bold leading-tight text-white/90">下一步</p>
                <p className="mt-0.5 text-[14px] font-extrabold leading-snug text-white">
                  查看 12,209 条中标记录
                </p>
              </div>
              <ArrowRight className="ml-auto h-4 w-4 shrink-0 text-white" aria-hidden />
            </div>
          </div>
        </div>
      </div>

      {/* ── 底部：居中放大的主按钮 ──
          只有一个按钮；亮蓝实底、无箭头（按评审意见改，之前那圈亮蓝硬投影
          看着像又叠了一个按钮，已去掉）。点击进入三模块页（/modules）。 */}
      <div className="mx-auto w-full max-w-[72rem] px-6 pb-12 text-center">
        <button
          type="button"
          onClick={() => navigate('/modules')}
          className="inline-flex h-16 items-center justify-center rounded-full bg-[var(--color-azure)] px-14 text-[17px] font-extrabold text-white transition-colors duration-[var(--dur-short)] ease-out hover:bg-[var(--color-azure-dp)]"
        >
          开始提问
        </button>
      </div>
    </section>
  );
}
