import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import { SupplierPage } from '@/pages/supplier/SupplierPage';

/* 供应商全景页集成：卡片网格 / 筛选 / 对比席位上限 / 详情弹窗。 */

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/supplier']}>
      <SupplierPage />
    </MemoryRouter>
  );
}

const HEIKANG = '杭州海康威视数字技术股份有限公司';

describe('供应商全景页', () => {
  it('渲染供应商卡片网格', async () => {
    renderPage();
    expect(await screen.findByText(HEIKANG)).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox')).toHaveLength(7);
  });

  it('筛选标签按维度过滤结果', async () => {
    renderPage();
    await screen.findByText(HEIKANG);

    fireEvent.click(screen.getByRole('button', { name: '辽宁省' }));
    expect(screen.getByText('东软集团股份有限公司')).toBeInTheDocument();
    expect(screen.queryByText(HEIKANG)).toBeNull();

    // 重置后恢复
    fireEvent.click(screen.getByRole('button', { name: '重置筛选' }));
    expect(screen.getByText(HEIKANG)).toBeInTheDocument();
  });

  it('勾选 3 家后出现对比面板，第 4 家被禁用（规范上限）', async () => {
    renderPage();
    const boxes = await screen.findAllByRole('checkbox');

    expect(screen.queryByText('多方对比')).toBeNull();
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);
    fireEvent.click(boxes[2]);

    expect(screen.getByText('多方对比')).toBeInTheDocument();
    expect(boxes[0]).toBeChecked(); // 已选中的仍可操作（点它是移除）
    expect(boxes[0]).not.toBeDisabled();
    expect(boxes[3]).toBeDisabled(); // 已达上限：未选中的不能再加
  });

  it('点「查看详情」打开详情弹窗，含价格分布', async () => {
    renderPage();
    const buttons = await screen.findAllByRole('button', { name: /查看详情/ });
    fireEvent.click(buttons[0]);

    const dialog = await screen.findByRole('dialog', { name: /供应商详情/ });
    expect(within(dialog).getByText('中标历史价格分布')).toBeInTheDocument();
    expect(within(dialog).getByText('履约能力雷达')).toBeInTheDocument();
  });
});
