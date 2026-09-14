import { NavLink } from 'react-router';
import { User } from 'lucide-react';
import { Brand } from '@/components/common/Brand';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

/**
 * SiteHeader —— 吸顶导航（D1/D2）。
 * 毛玻璃 + 底部发丝线；品牌 compact；四个导航按钮占位页（当前路由变钴蓝）。
 * 移动端：品牌+徽章一行，导航换行一行。
 */
const NAV_ITEMS = [
  { to: '/intro', label: '功能介绍' },
  { to: '/faq', label: '常见问题' },
  { to: '/mobile', label: '移动端连接' },
  { to: '/links', label: '友情链接' },
];

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-[var(--color-rule)] bg-[var(--color-paper)]/80 backdrop-blur-md">
      <div className="mx-auto flex w-full max-w-[72rem] flex-col gap-1.5 px-6 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center justify-between">
          <Brand compact />
          <Badge variant="neutral" className="sm:hidden">
            <User className="h-3 w-3" aria-hidden />
            匿名会话
          </Badge>
        </div>
        <nav className="flex items-center gap-1" aria-label="站点导航">
          {NAV_ITEMS.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                cn(
                  'rounded-md px-2.5 py-1.5 text-[13px] transition-colors duration-[var(--dur-micro)] ease-out',
                  isActive
                    ? 'font-medium text-[var(--color-accent)]'
                    : 'text-[var(--color-muted)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]'
                )
              }
            >
              {n.label}
            </NavLink>
          ))}
          <Badge variant="neutral" className="ml-1 hidden sm:inline-flex">
            <User className="h-3 w-3" aria-hidden />
            匿名会话 · 数据隔离
          </Badge>
        </nav>
      </div>
    </header>
  );
}
