import { NavLink, useLocation, useNavigate } from 'react-router';
import { Home, Sparkles } from 'lucide-react';
import mascot from '@/assets/mascot-trim.webp';
import { cn } from '@/lib/utils';

/**
 * SiteHeader —— 吸顶导航（2026-11-24 · 对照参考图的比例与层次）。
 *
 * 结构（自上而下）：
 *   ① 品牌行：[宠物] + 「招小智 Ai」
 *   ② 主行动：「开始」紧贴词标下方，`w-full` ⇒ 宽度与「招小智 Ai」一致
 *   ③ 导航条：**整行满宽**的玻璃圆角条，左端被宠物压住，导航项从宠物右侧排起
 *
 * 宠物用绝对定位贴住整块底部（`bottom` 与容器的 `pb` 对齐），因此它的脚底
 * 正好落在导航条的**底边**上 —— 就是参考图那种"角色站在条上"的效果。
 */
const NAV_ITEMS = [
  { to: '/intro', label: '功能介绍' },
  { to: '/faq', label: '常见问题' },
  { to: '/mobile', label: '移动端连接' },
  { to: '/links', label: '友情链接' },
] as const;

const ITEM_BASE =
  'rounded-full px-4 py-2 text-[17px] font-semibold transition-colors duration-[var(--dur-micro)] ease-out';

export function SiteHeader() {
  const navigate = useNavigate();
  const { pathname } = useLocation();

  return (
    <header className="sticky top-0 z-50 bg-[var(--color-paper)]/92 backdrop-blur-md">
      {/* 容器给足高度（min-h）+ 内容靠底排：
          宠物 144px 绝对定位贴在容器底部，若容器不够高，它的头顶会被顶出视口
          （之前就是这样被裁掉的）。min-h 保证宠物完整落在框内。 */}
      <div className="relative mx-auto flex min-h-[10rem] w-full max-w-[78rem] flex-col justify-end px-6 pb-2 pt-2">
        {/* 宠物：底边贴住导航条底边，完整不裁切 */}
        <img
          src={mascot}
          alt=""
          aria-hidden
          className="pointer-events-none absolute bottom-2 left-6 z-10 h-24 w-24 object-contain drop-shadow-[0_3px_5px_rgba(8,8,8,0.18)] sm:h-36 sm:w-36"
        />

        {/* ① 词标：整体上移，下方留一点空白（不再贴死导航条）。
            行内 items-baseline 让中文与「Ai」共用基线（字号差一倍也齐）。 */}
        <div className="mb-4 pl-28 sm:pl-40">
          <span className="flex items-baseline gap-1.5 whitespace-nowrap">
            <span className="text-[1.25rem] font-extrabold leading-none tracking-[-0.04em] text-[var(--color-edge)] sm:text-[2.25rem]">
              招小智
            </span>
            {/* 艺术字 Ai：蓝渐变 + 斜体 + 星芒。外层保持普通行内元素，
                这样父级的 items-baseline 才能吃到 Ai 的基线（用 inline-flex 会取错）。 */}
            <span className="relative">
              <span className="bg-gradient-to-br from-[#0031c2] via-[#0e59ff] to-[#5fd0ff] bg-clip-text text-[2.3rem] font-extrabold italic leading-none tracking-[-0.08em] text-transparent sm:text-[4.3rem]">
                Ai
              </span>
              <Sparkles className="absolute -top-0.5 left-full ml-1 h-4 w-4 text-[#5fd0ff] sm:h-6 sm:w-6" aria-hidden />
            </span>
          </span>
        </div>

        {/* ② 导航条：整行满宽 + 满圆弧 + 玻璃拟态；左侧给宠物让位
            条内第一项是**返回首页的 home 图标**（无底色、无文字），其后是四个功能链接 */}
        <nav
          aria-label="站点导航"
          className={cn(
            // home 图标钉最左；四个功能项在 `图标 → 右侧留白` 之间均匀分布。
            // 右侧留白 ≈ 四个中文字宽（17px × 4 ≈ 68px），使「友情链接」不顶到最右侧。
            'flex w-full flex-wrap items-center justify-between gap-1 rounded-full p-1.5 pl-24 sm:flex-nowrap sm:pl-40 sm:pr-[4.25rem]',
            'border border-white/20 bg-[rgba(10,13,22,0.80)] backdrop-blur-xl',
            'shadow-[inset_0_1px_0_rgba(255,255,255,0.18),0_10px_30px_-12px_rgba(8,8,8,0.45)]'
          )}
        >
          <button
            type="button"
            onClick={() => navigate('/chat')}
            aria-label="返回首页"
            aria-current={pathname === '/chat' ? 'page' : undefined}
            title="返回首页"
            className={cn(
              'flex h-11 w-11 shrink-0 translate-x-3 items-center justify-center rounded-full text-white/85',
              'transition-colors duration-[var(--dur-micro)] ease-out hover:bg-white/10 hover:text-white',
              pathname === '/chat' && 'text-white'
            )}
          >
            <Home className="h-7 w-7 stroke-[2.8]" aria-hidden />
          </button>

          {NAV_ITEMS.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                cn(
                  ITEM_BASE,
                  isActive
                    ? 'bg-white text-[var(--color-edge)]'
                    : 'text-white/85 hover:bg-white/10 hover:text-white'
                )
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}
