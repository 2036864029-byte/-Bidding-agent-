import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { InquiryPage } from '@/pages/inquiry/InquiryPage';

/* 智能询价页集成：
 * ① 对比表渲染 + 超阈值行高亮；
 * ② 点「调价」开抽屉，且**真的**算出需下调金额（不是假交互）；
 * ③ 对话区问价格类问题 → 经 mock 路由拿回 price_records → 渲染历史中标记录表。 */

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/inquiry']}>
      <InquiryPage />
    </MemoryRouter>
  );
}

describe('智能询价页', () => {
  it('渲染询价单与对比表，预算按「元」口径展示（不误当万元）', async () => {
    renderPage();

    expect(await screen.findByText('智慧园区安防与信息化设备采购')).toBeInTheDocument();
    // 2,860,000 元 = 286.00 万元；若错用万元口径会显示成 286.00 亿元
    expect(screen.getByText('286.00')).toBeInTheDocument();
    expect(screen.getByText('万元')).toBeInTheDocument();
    expect(screen.queryByText('亿元')).toBeNull();

    // 超阈值项在表头给出计数
    expect(screen.getByText('1 项报价超阈值')).toBeInTheDocument();
  });

  it('点「调价」开抽屉，并算出回到阈值内所需下调金额', async () => {
    renderPage();

    // 摄像机三项报价升序为 820 / 875 / 1040 —— 第 3 条超阈值（阈值 1000）
    const adjustButtons = await screen.findAllByRole('button', { name: /调价/ });
    fireEvent.click(adjustButtons[2]);

    const drawer = await screen.findByRole('dialog', { name: /调价/ });
    expect(within(drawer).getByText(/深圳市捷顺/)).toBeInTheDocument();
    // 1040 → 阈值 1000，需下调 40
    expect(within(drawer).getByText(/需较当前下调 ¥40/)).toBeInTheDocument();
  });

  it('对话区：价格类问题经 mock 路由拿回中标记录并渲染成表', async () => {
    renderPage();

    const input = await screen.findByPlaceholderText(/例如：采购/);
    fireEvent.change(input, { target: { value: '摄像机的历史中标均价大概多少？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    // price_records fixture 的采购人只出现在表里
    expect(await screen.findByText('广州开发区智慧园区管理委员会')).toBeInTheDocument();
  });
});
