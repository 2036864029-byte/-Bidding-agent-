import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';
import { SupplierPage } from '@/pages/supplier/SupplierPage';
import { setDataSource } from '@/services/api';

/* 供应商全景页（2026-09-15 重塑后）：
 * ① 卡片展示工商基本信息 + 中标概况；
 * ② 按地区/行业筛选走服务端（mock 侧同语义过滤）；
 * ③ 详情浮窗给「工商信息 + 近期中标项目」；
 * ④ CCGP 来源（无工商数据）要**明说没有**，而不是留空。
 *
 * 数据源固定在 mock：本文件测页面渲染，真实端点由 api-switch.test.ts 覆盖。 */

beforeEach(() => setDataSource('supplier', 'mock'));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/supplier']}>
      <SupplierPage />
    </MemoryRouter>
  );
}

const LEGACY = '安徽海纳信息科技有限公司';
const CCGP = '湖北楚天智能交通股份有限公司';

describe('供应商全景页', () => {
  it('渲染供应商卡片（含中标次数与总额）', async () => {
    renderPage();
    expect(await screen.findByText(LEGACY)).toBeInTheDocument();
    expect(screen.getByText(CCGP)).toBeInTheDocument();
    // 中标次数 12 次
    expect(screen.getByText('12')).toBeInTheDocument();
    // 新的筛选行（按地区 / 行业）在位
    expect(screen.getByLabelText('按地区筛选')).toBeInTheDocument();
    expect(screen.getByLabelText('按行业筛选')).toBeInTheDocument();
  });

  it('按地区筛选（服务端语义）：只剩该省企业', async () => {
    renderPage();
    await screen.findByText(LEGACY);

    fireEvent.change(screen.getByLabelText('按地区筛选'), { target: { value: '安徽省' } });

    await waitFor(() => {
      expect(screen.queryByText(CCGP)).toBeNull();
    });
    expect(screen.getByText(LEGACY)).toBeInTheDocument();
  });

  it('点「查看详情」→ 工商信息 + 近期中标项目', async () => {
    renderPage();
    const buttons = await screen.findAllByRole('button', { name: /查看详情/ });
    fireEvent.click(buttons[0]);

    const dialog = await screen.findByRole('dialog', { name: /供应商详情/ });
    expect(within(dialog).getByText('工商信息')).toBeInTheDocument();
    expect(within(dialog).getByText('软件和信息技术服务业')).toBeInTheDocument();
    expect(within(dialog).getByText('郭振')).toBeInTheDocument();
    expect(within(dialog).getByText('近期中标项目')).toBeInTheDocument();
    expect(
      within(dialog).getByText('宿州市政务服务中心安防监控系统升级项目')
    ).toBeInTheDocument();
  });

  it('⚠️ CCGP 来源（无工商数据）详情里明说「暂无工商信息」，不装作有', async () => {
    renderPage();
    await screen.findByText(CCGP);
    const buttons = await screen.findAllByRole('button', { name: /查看详情/ });
    // mock 里 CCGP 那家排在最后
    fireEvent.click(buttons[buttons.length - 1]);

    const dialog = await screen.findByRole('dialog', { name: /供应商详情/ });
    expect(within(dialog).getByText(/暂无工商信息/)).toBeInTheDocument();
  });
});
