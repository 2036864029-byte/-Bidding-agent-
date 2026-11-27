import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { MobilePage } from '@/pages/mobile/MobilePage';

/* 移动端连接页（/mobile）：
 * 原先是一个 PlaceholderPage 占位，本轮填入飞书二维码。
 * 这里守住三件事：占位态确实没了、二维码真的渲染出来、扫码步骤仍在。 */

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/mobile']}>
      <MobilePage />
    </MemoryRouter>
  );
}

describe('移动端连接页', () => {
  it('渲染标题，且不再是占位页', () => {
    renderPage();
    expect(screen.getByRole('heading', { level: 1, name: '移动端连接' })).toBeInTheDocument();
    expect(screen.queryByText(/内容建设中/)).toBeNull();
  });

  it('渲染飞书二维码图片（可访问名称说明用途）', () => {
    renderPage();
    const qr = screen.getByAltText('飞书扫码进入招小智');
    expect(qr.tagName).toBe('IMG');
    // Vite 会把资源导入改写为带 hash 的 URL —— 断言指向的是那张二维码
    expect(qr.getAttribute('src')).toContain('feishu-qr');
  });

  it('给出扫码三步', () => {
    renderPage();
    for (const step of ['打开飞书', '扫码', '直接提问']) {
      expect(screen.getByText(step)).toBeInTheDocument();
    }
  });

  it('保留返回对话的入口', () => {
    renderPage();
    expect(screen.getByRole('link', { name: /先在网页上试试/ })).toHaveAttribute(
      'href',
      '/chat'
    );
  });
});
