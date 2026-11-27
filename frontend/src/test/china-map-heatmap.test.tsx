import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  ChinaMapHeatmap,
  HEAT_RAMP,
  HEAT_THRESHOLDS,
  NO_DATA_FILL,
  heatColor,
} from '@/components/kanban/ChinaMapHeatmap';
import { CHINA_PROVINCES } from '@/components/kanban/chinaProvinces';

/* 省份热力地图（ChinaMapHeatmap）
 *
 * 三件事值得守住：
 * ① 色阶分档边界 —— 分档阈值与颜色一一绑定，改数据不该让颜色含义漂移；
 * ② 数据里出现"非省级行政区"（如「兵团」）时不能崩，也不能硬画到图上；
 * ③ TOP 排序必须是降序 —— 标注框的 #1..#5 直接读这个顺序。
 */

const CELLS = [
  { region: '上海市', count: 4698 },
  { region: '四川省', count: 4104 },
  { region: '浙江省', count: 2748 },
  { region: '广东省', count: 2353 },
  { region: '山东省', count: 2046 },
  { region: '兵团', count: 291 },
];

function renderMap(cells = CELLS) {
  return render(<ChinaMapHeatmap cells={cells} />);
}

describe('heatColor —— 分档边界', () => {
  it('阈值上沿落入更高一档（>= 判定）', () => {
    expect(heatColor(HEAT_THRESHOLDS[0])).toBe(HEAT_RAMP[4]); // 3000 → 最深
    expect(heatColor(HEAT_THRESHOLDS[1])).toBe(HEAT_RAMP[3]); // 2000
    expect(heatColor(HEAT_THRESHOLDS[2])).toBe(HEAT_RAMP[2]); // 1400
    expect(heatColor(HEAT_THRESHOLDS[3])).toBe(HEAT_RAMP[1]); // 900
    expect(heatColor(0)).toBe(HEAT_RAMP[0]);                  // 0 → 最浅
  });

  it('阈值下沿落在更低一档', () => {
    expect(heatColor(HEAT_THRESHOLDS[0] - 1)).toBe(HEAT_RAMP[3]);
    expect(heatColor(HEAT_THRESHOLDS[1] - 1)).toBe(HEAT_RAMP[2]);
    expect(heatColor(HEAT_THRESHOLDS[2] - 1)).toBe(HEAT_RAMP[1]);
    expect(heatColor(HEAT_THRESHOLDS[3] - 1)).toBe(HEAT_RAMP[0]);
  });

  it('本期无数据用中性灰，不参与色阶', () => {
    expect(heatColor(null)).toBe(NO_DATA_FILL);
    expect(heatColor(undefined)).toBe(NO_DATA_FILL);
  });

  it('色阶是单色相、浅→深（顺序量级不能彩虹）', () => {
    // 5 档逐个更暗：把 hex 拉平成亮度比较，必须严格递减
    const lum = HEAT_RAMP.map((c) => {
      const n = parseInt(c.slice(1), 16);
      return 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
    });
    for (let i = 1; i < lum.length; i += 1) {
      expect(lum[i]).toBeLessThan(lum[i - 1]);
    }
  });
});

describe('ChinaMapHeatmap —— 渲染', () => {
  it('34 个省级单元都有 path，并带数量提示', () => {
    const { container } = renderMap();
    const titles = Array.from(container.querySelectorAll('path > title')).map(
      (t) => t.textContent ?? ''
    );
    expect(titles).toHaveLength(CHINA_PROVINCES.length);
    expect(titles.some((t) => t.includes('上海市') && t.includes('4,698'))).toBe(true);
    // 本期无数据的省要写明，而不是留空
    expect(titles.some((t) => t.includes('本期无数据'))).toBe(true);
  });

  it('TOP 5 按数量降序，且第 6 名不进列表', () => {
    const { container } = renderMap();
    const listItems = Array.from(container.querySelectorAll('li'));
    expect(listItems).toHaveLength(5);
    const names = listItems.map((li) => li.textContent ?? '');
    expect(names[0]).toContain('上海');
    expect(names[4]).toContain('山东');
    expect(names.some((n) => n.includes('兵团'))).toBe(false);
  });

  it('标注框只给前 5 名，编号 #1..#5 各一个', () => {
    const { container } = renderMap();
    const text = container.textContent ?? '';
    for (const i of [1, 2, 3, 4, 5]) {
      expect(text).toContain(`#${i}`);
    }
    expect(text).not.toContain('#6');
  });

  it('「兵团」这类非省级区域只进列表，不硬画到图上', () => {
    // 把兵团顶到第 1，触发"有数据但无几何"的分支：不能崩，且要给出说明
    const cells = [{ region: '兵团', count: 9999 }, ...CELLS.slice(0, 5)];
    const { container } = renderMap(cells);
    expect(container.textContent).toContain('兵团');
    expect(container.textContent).toContain('不属于省级行政区');
    // 图上仍是 34 个省级单元，没有凭空多出一块
    expect(container.querySelectorAll('path > title')).toHaveLength(CHINA_PROVINCES.length);
  });

  it('空数据不崩（接口暂时没返回时）', () => {
    const { container } = renderMap([]);
    expect(container.querySelectorAll('path > title')).toHaveLength(CHINA_PROVINCES.length);
    expect(container.textContent).toContain('TOP 5 地区');
  });
});

describe('ChinaMapHeatmap —— 交互', () => {
  const provinceButton = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) });

  it('点击省份 → 右侧明细显示数量、排名与占比', () => {
    renderMap();
    fireEvent.click(provinceButton('四川'));

    const card = screen.getByText('已选省份').parentElement as HTMLElement;
    expect(within(card).getByText('第 2 名')).toBeInTheDocument();
    expect(within(card).getByText('4,104')).toBeInTheDocument();
    // fixture 合计 16,240：4104 / 16240 = 25.3%
    expect(within(card).getByText(/占全省域 25\.3%/)).toBeInTheDocument();
  });

  it('再点一次同一个省 → 取消选中，回到提示态', () => {
    renderMap();
    const btn = provinceButton('四川');
    fireEvent.click(btn);
    expect(screen.queryByText('已选省份')).not.toBeNull();

    fireEvent.click(btn);
    expect(screen.queryByText('已选省份')).toBeNull();
    expect(screen.getByText(/点击地图上的任一省份/)).toBeInTheDocument();
  });

  it('键盘可达：Enter 也能选中省份', () => {
    renderMap();
    const btn = provinceButton('广东');
    fireEvent.keyDown(btn, { key: 'Enter' });
    const card = screen.getByText('已选省份').parentElement as HTMLElement;
    expect(within(card).getByText('第 4 名')).toBeInTheDocument();
  });

  it('点「显示全部省份情况」打开浮窗，列出全部地区与占比', () => {
    renderMap();
    fireEvent.click(screen.getByRole('button', { name: '显示全部省份情况' }));

    const dialog = screen.getByRole('dialog', { name: '全部省份占比' });
    expect(within(dialog).getByText(/共 6 个地区/)).toBeInTheDocument();
    // 6 个数据行（表头 1 行之外）
    expect(within(dialog).getAllByRole('row')).toHaveLength(7);
    expect(within(dialog).getByText('兵团')).toBeInTheDocument();
    // fixture 合计 16,240：4698 / 16240 = 28.9%
    expect(within(dialog).getByText('28.9%')).toBeInTheDocument();
  });

  it('点击省份 → 地图上就地弹出信息卡（不依赖右侧栏，窄屏也可见）', () => {
    const { container } = renderMap();
    fireEvent.click(provinceButton('四川'));

    // 精确定位到浮出的那张卡：地图里同时还有前 5 名的标注框，文本会重复
    const pop = container.querySelector('.cmh-pop');
    expect(pop).not.toBeNull();
    // SVG 里的 <g> 不是 HTMLElement，用 textContent 断言（tspan 会被拼进来）
    const text = pop?.textContent ?? '';
    expect(text).toContain('第 2 名');
    expect(text).toContain('4,104');
    expect(text).toContain('占全省域 25.3%');
  });

  it('选中态带呼吸动效（cmh-ripple）与淡入（cmh-pop）', () => {
    const { container } = renderMap();
    expect(container.querySelector('.cmh-ripple')).toBeNull();

    fireEvent.click(provinceButton('四川'));
    expect(container.querySelector('.cmh-ripple')).not.toBeNull();
    expect(container.querySelector('.cmh-pop')).not.toBeNull();
  });

  it('铺满画布的装饰层必须 pointer-events:none（否则吃掉省份点击）', () => {
    const { container } = renderMap();
    // jsdom 没有命中检测，唯一的守卫是这条结构性断言：
    // 凡是铺满 viewBox 的装饰 rect，都不能参与命中测试。
    const canvasRects = Array.from(container.querySelectorAll('svg > rect'));
    expect(canvasRects.length).toBeGreaterThanOrEqual(3);
    for (const rect of canvasRects) {
      expect(rect.getAttribute('width')).toBe('960');
      expect(rect.getAttribute('pointer-events')).toBe('none');
    }

    // 标注框同理：说明性元素不该挡住底下的省份
    const callouts = container.querySelector('.cmh-callouts');
    expect(callouts).not.toBeNull();
    expect(callouts?.getAttribute('pointer-events')).toBe('none');
  });

  it('Esc 关闭浮窗', () => {
    renderMap();
    fireEvent.click(screen.getByRole('button', { name: '显示全部省份情况' }));
    expect(screen.queryByRole('dialog')).not.toBeNull();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
