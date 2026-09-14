import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ChartCard, DataTable } from '@/components/common/ChartCard';

/* 图表卡「图表 / 表格」双视图（dataviz 无障碍要求：每张图都要有表格孪生）。 */

describe('ChartCard 双视图', () => {
  const renderCard = () =>
    render(
      <ChartCard
        title="商机走势"
        subtitle="按月"
        table={<DataTable columns={['月份', '项目数']} rows={[['2026-08', 134]]} />}
      >
        <div data-testid="chart-body">chart</div>
      </ChartCard>
    );

  it('默认显示图表，不显示表格', () => {
    renderCard();
    expect(screen.getByTestId('chart-body')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('点击「表格」切换到表格孪生视图，再点「图表」切回', () => {
    renderCard();

    fireEvent.click(screen.getByRole('button', { name: /表格/ }));
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.queryByTestId('chart-body')).toBeNull();
    expect(screen.getByText('2026-08')).toBeInTheDocument();
    expect(screen.getByText('134')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /图表/ }));
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.getByTestId('chart-body')).toBeInTheDocument();
  });

  it('不传 table 时不渲染切换按钮', () => {
    render(
      <ChartCard title="无表格">
        <div>body</div>
      </ChartCard>
    );
    expect(screen.queryByRole('button')).toBeNull();
  });
});
