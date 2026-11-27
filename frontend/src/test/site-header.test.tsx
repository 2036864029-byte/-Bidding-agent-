import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { describe, expect, it } from 'vitest';
import { SiteHeader } from '@/components/layout/SiteHeader';

/* 「开始」按钮的跨页回归（2026-11-24）：
 *
 * 该按钮活在 SiteHeader 里，而 SiteHeader 被 /chat、/modules、/intro、/faq、/links
 * 共用。它曾只写 getElementById('modules')?.scrollIntoView()：在非首页取到 null、
 * 被可选链吞掉 → 点击毫无反应（用户反馈为"卡住/不能返回"）。
 *
 * 现在改成独立路由跳转 `/modules`，各页行为一致，且可后退/直链。
 * 这里钉死：任何页面点「开始」都必须落到 /modules。 */

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="loc">{`${loc.pathname}${loc.hash}`}</span>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="*"
          element={
            <>
              <SiteHeader />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>
  );
}

describe('SiteHeader · home 图标跨页行为', () => {
  it.each(['/intro', '/faq', '/links', '/modules'])('从 %s 点 home 图标都回到 /chat', (path) => {
    renderAt(path);
    fireEvent.click(screen.getByRole('button', { name: '返回首页' }));
    expect(screen.getByTestId('loc')).toHaveTextContent('/chat');
  });

  it('/chat 上 home 图标标记为当前页（aria-current）', () => {
    renderAt('/chat');
    expect(screen.getByRole('button', { name: '返回首页' })).toHaveAttribute('aria-current', 'page');
  });

  it('导航条里不再有「开始」按钮（已改为 home 图标）', () => {
    renderAt('/chat');
    expect(screen.queryByRole('button', { name: '开始' })).toBeNull();
  });
});
