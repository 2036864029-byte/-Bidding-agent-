import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { KanbanPage } from '@/pages/kanban/KanbanPage';
import { setDataSource } from '@/services/api';

/* 商机看板：数量口径 + 行业走势。
 * 两条回归护栏：
 * ① 金额维度确实不在了（用户拍板去掉）；
 * ② 产品榜确实撤掉了（频率分布无信号，最高 3 次/94% 只出现 1 次）。
 *
 * 数据源固定在 mock：本文件测的是**页面渲染**，数据来源是另一回事
 * （真实端点由 api-switch.test.ts 覆盖）。默认已翻到 'api'，不固定就会去连后端。 */

beforeEach(() => setDataSource('kanban', 'mock'));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/kanban']}>
      <KanbanPage />
    </MemoryRouter>
  );
}

describe('商机看板页', () => {
  it('汇总卡是四个数量口径指标', async () => {
    renderPage();

    expect(await screen.findByText('收录项目')).toBeInTheDocument();
    expect(screen.getByText('近一月项目')).toBeInTheDocument();
    expect(screen.getByText('覆盖地区')).toBeInTheDocument();
    expect(screen.getByText('覆盖行业')).toBeInTheDocument();
  });

  it('行业走势：三条交易类型序列（用表格孪生验，jsdom 不渲染 SVG 图例）', async () => {
    renderPage();

    const card = (await screen.findByText('行业走势')).closest('section') as HTMLElement;
    // 切到表格视图：列头应恰好是三条序列名
    fireEvent.click(within(card).getByRole('button', { name: /表格/ }));

    expect(within(card).getByRole('columnheader', { name: '政府采购' })).toBeInTheDocument();
    expect(within(card).getByRole('columnheader', { name: '其他' })).toBeInTheDocument();
    expect(within(card).getByRole('columnheader', { name: '工程建设' })).toBeInTheDocument();
    // 月份行也在（10 个月）
    expect(within(card).getByText('2025-11')).toBeInTheDocument();
  });

  it('⚠️ 页面不含任何金额维度（用户已拍板去掉）', async () => {
    renderPage();
    await screen.findByText('行业走势');

    expect(screen.queryByText(/金额/)).toBeNull();
    expect(screen.queryByText(/万元/)).toBeNull();
    expect(document.body.textContent).not.toContain('金额');
  });

  it('⚠️ 产品榜已撤（分布无信号，别再加回来）', async () => {
    renderPage();
    await screen.findByText('行业走势');

    expect(screen.queryByText(/产品 Top 榜/)).toBeNull();
    expect(screen.queryByText(/产品出现次数/)).toBeNull();
  });

  it('地区分布 / 行业占比副图仍在（只换了维度，没砍功能）', async () => {
    renderPage();

    expect(await screen.findByText('地区分布')).toBeInTheDocument();
    expect(screen.getByText('行业占比')).toBeInTheDocument();
  });
});
