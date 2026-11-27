import { useEffect, useId, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { fmtInt } from '@/lib/format';
import type { KanbanHeatmapCell } from '@/types/kanban';
import { CHINA_PROVINCES, CHINA_VIEW_BOX } from './chinaProvinces';

/**
 * ChinaMapHeatmap —— 省级热力（中国地图 · 立体台地）。
 *
 * 替代原先的方块网格 `RegionHeatmap`：同一份数据（`KanbanHeatmapCell[]`），
 * 换成真实省界 + 立体台地，读"哪里多"比数格子快得多。
 *
 * 视觉三件事：
 *   1. **立体**：整块中国轮廓向下平移 `DEPTH` 画成侧壁（统一浅灰蓝），再盖省面；
 *      侧壁只在国境南/东露出，形成一块被抬起的板。
 *   2. **细缝而非描边**：省界用 1.2px 白色描边，浅底上读作分块，不是勾线。
 *   3. **外圈一条蓝边**：先铺带钴蓝描边的整块底色，再用省面盖住内部，
 *      于是只剩最外圈一条细线（不用求并集）。
 *
 * 交互：
 *   - **点省份** → 右侧显示该省数量 / 排名 / 占全省域比例；再点一次取消。
 *     地图上同时用钴蓝描边把选中的省圈出来（颜色编码不变，只加轮廓）。
 *   - **显示全部省份情况** → 浮窗列出所有省份的占比（含「兵团」等非省级区域，
 *     它们只在列表里，不画在图上）。
 *   - 键盘可达：省份是 `role="button"` + Enter/Space 触发，浮窗支持 Esc 关闭。
 *
 * 数据诚实：颜色只编码"相对数量"（分档阈值固定，颜色含义不随数据漂移）；
 * 绝对值靠标注框、明细卡、TOP 列表与图表卡的表格孪生视图，不依赖颜色读。
 *
 * ⚠️ 色阶是**单色相、浅→深**（与 RegionHeatmap 的 --seq-1..4 同一语义），
 * 不用彩虹色：顺序量级必须单调可读。
 */

/** 5 档顺序色阶（浅→深 = 少→多）。阈值见 `HEAT_THRESHOLDS`，两者必须同步改。 */
export const HEAT_RAMP = ['#b3d2f7', '#74acef', '#3f8fe6', '#1a6fd8', '#0a4dae'] as const;

/** 分档阈值（降序）。`count >= 阈值` 落入对应档，最后兜底为最浅一档。 */
export const HEAT_THRESHOLDS = [3000, 2000, 1400, 900, 0] as const;

/** 本期无数据的省份（不出现在接口返回里）。 */
export const NO_DATA_FILL = '#e9eff7';

const DEPTH = 20;          // 挤出厚度（viewBox 单位）
const SEAM = 1.2;          // 白色细缝
const WALL_FILL = '#93b3d6';
const RIM_STROKE = '#2f7fd0';
const GLOW_MIN = 2000;     // 达到此值才加柔光，避免满屏发光

/** 前 5 名标注框的绝对位置（viewBox 坐标）；东海岸三省挨得近，必须手工错开。 */
const CALLOUT_POS: Record<string, readonly [number, number]> = {
  四川省: [6, 210],
  山东省: [782, 92],
  上海市: [782, 208],
  浙江省: [782, 324],
  广东省: [782, 440],
};
const BOX_W = 172;
const BOX_H = 60;
/** 不在 CALLOUT_POS 里的省份按名次落到右侧一列，避免叠在一起。 */
const CALLOUT_FALLBACK_X = 782;
const CALLOUT_STEP_Y = 116;

/** 选中省份后就地弹出的信息卡（在 SVG 内，所以任何窗口宽度都看得见）。 */
const POP_W = 158;
const POP_H = 64;

/** 把信息卡放在省中心旁边，并钳制在 viewBox 内（免得贴边被裁）。 */
function popoverRect(cx: number, cy: number) {
  const toRight = cx < 620;
  const x = Math.min(Math.max(toRight ? cx + 20 : cx - POP_W - 20, 8), 960 - POP_W - 8);
  const y = Math.min(Math.max(cy - POP_H / 2, 8), 560 - POP_H - 8);
  return {
    x,
    y,
    anchorX: toRight ? x : x + POP_W,
    anchorY: y + POP_H / 2,
  };
}

/** 数量 → 分档颜色。`null/undefined`（本期无数据）返回中性灰。 */
export function heatColor(count: number | null | undefined): string {
  if (count === null || count === undefined) return NO_DATA_FILL;
  for (let i = 0; i < HEAT_THRESHOLDS.length; i += 1) {
    if (count >= HEAT_THRESHOLDS[i]) return HEAT_RAMP[HEAT_RAMP.length - 1 - i];
  }
  return HEAT_RAMP[0];
}

export function ChinaMapHeatmap({ cells }: { cells: KanbanHeatmapCell[] }) {
  const uid = useId().replace(/:/g, '');
  const [selected, setSelected] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const countByName = useMemo(
    () => new Map(cells.map((c) => [c.region, c.count])),
    [cells]
  );
  const total = cells.reduce((sum, c) => sum + c.count, 0) || 1;
  const ranked = useMemo(() => [...cells].sort((a, b) => b.count - a.count), [cells]);
  const top5 = ranked.slice(0, 5);
  const geometry = useMemo(() => new Map(CHINA_PROVINCES.map((p) => [p.name, p])), []);

  // Esc 关闭浮窗（自绘弹窗没有 focus trap，至少把最常用的退出路径补上）
  useEffect(() => {
    if (!showAll) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowAll(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [showAll]);

  const allD = CHINA_PROVINCES.map((p) => p.d).join('');
  const shaded = CHINA_PROVINCES.filter((p) => (countByName.get(p.name) ?? 0) >= GLOW_MIN);

  const toggle = (name: string) => setSelected((cur) => (cur === name ? null : name));

  const selectedCell = selected ? ranked.find((c) => c.region === selected) : undefined;
  const selectedGeo = selected ? geometry.get(selected) : undefined;
  const selectedRank = selected ? ranked.findIndex((c) => c.region === selected) + 1 : 0;
  const pop =
    selectedGeo && selectedGeo.cx !== null && selectedGeo.cy !== null
      ? popoverRect(selectedGeo.cx, selectedGeo.cy)
      : null;

  const callouts = top5
    .map((cell, i) => {
      const geo = geometry.get(cell.region);
      if (!geo || geo.cx === null || geo.cy === null) return null;
      const [bx, by] = CALLOUT_POS[cell.region] ?? [
        CALLOUT_FALLBACK_X,
        92 + i * CALLOUT_STEP_Y,
      ];
      const anchorX = bx + (bx + BOX_W / 2 < geo.cx ? BOX_W : 0);
      const anchorY = by + BOX_H / 2;
      const dim = selected !== null && selected !== cell.region;
      return (
        <g key={cell.region} opacity={dim ? 0.45 : 1}>
          <line
            x1={geo.cx}
            y1={geo.cy}
            x2={anchorX}
            y2={anchorY}
            stroke="rgba(0,118,237,.42)"
            strokeWidth={0.9}
            strokeDasharray="2.5 3"
          />
          <circle cx={geo.cx} cy={geo.cy} r={3} fill="#0076ed" />
          <rect
            x={bx}
            y={by}
            width={BOX_W}
            height={BOX_H}
            rx={8}
            fill="rgba(255,255,255,.94)"
            stroke="rgba(0,118,237,.22)"
          />
          <text x={bx + 13} y={by + 21} style={{ fill: 'var(--color-ink)' }} fontSize={12}
            fontWeight={600}
          >
            {geo.short}
            <tspan dx={6} fill="#0076ed" fontSize={10} style={{ fontFamily: 'var(--font-mono)' }}>
              #{i + 1}
            </tspan>
          </text>
          <text
            x={bx + 13}
            y={by + 39}
            fill="#0a5cb8"
            fontSize={12}
            fontWeight={600}
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            {fmtInt(cell.count)}
            <tspan dx={4} style={{ fill: 'var(--color-neutral)' }} fontSize={9.5} fontWeight={400}>
              个项目
            </tspan>
          </text>
          <text x={bx + 13} y={by + 53} style={{ fill: 'var(--color-neutral)' }} fontSize={10}>
            占全省域 {((cell.count / total) * 100).toFixed(1)}%
          </text>
        </g>
      );
    })
    .filter(Boolean);

  const vmax = ranked[0]?.count ?? 1;
  const missingFromMap = ranked
    .slice(0, 5)
    .filter((c) => !geometry.has(c.region))
    .map((c) => c.region);

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_196px]">
      {/* 地图舞台：白底 + 极细网格 + 交点圆点（网格画进 SVG，随地图一起缩放） */}
      <div className="relative overflow-hidden rounded-[var(--radius-lg)] bg-[var(--color-paper)]">
        <svg
          viewBox={CHINA_VIEW_BOX}
          role="img"
          aria-label="中国省级商机分布（点击省份查看占比）"
          className="block w-full"
        >
          <defs>
            <pattern id={`${uid}-grid`} width={46} height={46} patternUnits="userSpaceOnUse">
              <path
                d="M46 0H0V46"
                fill="none"
                stroke="rgba(0,118,237,.055)"
                strokeWidth={1}
              />
              <circle cx={0.5} cy={0.5} r={1} fill="rgba(0,118,237,.16)" />
            </pattern>
            <radialGradient id={`${uid}-wash`} cx="50%" cy="46%" r="70%">
              <stop offset="0%" stopColor="rgba(0,118,237,.07)" />
              <stop offset="100%" stopColor="rgba(0,118,237,0)" />
            </radialGradient>
            <radialGradient id={`${uid}-veil`} cx="50%" cy="50%" r="72%">
              <stop offset="58%" style={{ stopColor: 'var(--color-paper)' }} stopOpacity={0} />
              <stop offset="96%" style={{ stopColor: 'var(--color-paper)' }} stopOpacity={0.75} />
            </radialGradient>
            <filter id={`${uid}-shadow`} x="-20%" y="-20%" width="140%" height="150%">
              <feGaussianBlur stdDeviation={7} />
            </filter>
            <filter id={`${uid}-glow`} x="-25%" y="-25%" width="150%" height="150%">
              <feGaussianBlur stdDeviation={9} />
            </filter>
          </defs>

          {/*
            ⚠️ 铺满画布的装饰层必须 pointer-events:none。
            SVG 默认 `visiblePainted`：渐变矩形虽然只是"压暗/网格"，但它是画上去的，
            就会进入命中检测 —— 曾经把整层省面的点击全吃掉（真实鼠标点河北毫无反应，
            而 jsdom 测试因为是直接派发事件，完全测不出来）。
          */}
          <rect width="960" height="560" fill={`url(#${uid}-wash)`} pointerEvents="none" />
          <rect width="960" height="560" fill={`url(#${uid}-grid)`} pointerEvents="none" />

          {/* 落影 → 外圈底色 → 侧壁 → 高值柔光 → 省面 → 四周淡出 → 选中描边 → 标注 */}
          <g transform={`translate(0,${DEPTH + 4})`} fill="#9db6d4" opacity={0.5}
            filter={`url(#${uid}-shadow)`}
          >
            <path d={allD} />
          </g>
          <g fill="#dbe8f6" stroke={RIM_STROKE} strokeWidth={1.6} strokeOpacity={0.75}
            strokeLinejoin="round"
          >
            <path d={allD} />
          </g>
          <g transform={`translate(0,${DEPTH})`} fill={WALL_FILL}>
            <path d={allD} />
          </g>
          <g opacity={0.75} filter={`url(#${uid}-glow)`}>
            {shaded.map((p) => (
              <path key={p.name} d={p.d} fill={heatColor(countByName.get(p.name))} />
            ))}
          </g>
          <g stroke="#ffffff" strokeWidth={SEAM} strokeLinejoin="round">
            {CHINA_PROVINCES.map((p) => {
              const count = countByName.get(p.name);
              const isSel = selected === p.name;
              return (
                <path
                  key={p.name}
                  d={p.d}
                  fill={heatColor(count)}
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSel}
                  aria-label={`${p.short}：${
                    count === undefined ? '本期无数据' : `${fmtInt(count)} 个项目`
                  }`}
                  onClick={() => toggle(p.name)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      toggle(p.name);
                    }
                  }}
                  className="cursor-pointer outline-none focus-visible:stroke-[var(--color-focus)]"
                  style={isSel ? { stroke: 'var(--color-accent)', strokeWidth: 2.2 } : undefined}
                >
                  <title>
                    {p.name}：{count === undefined ? '本期无数据' : `${fmtInt(count)} 个项目`}
                  </title>
                </path>
              );
            })}
          </g>
          <rect width="960" height="560" fill={`url(#${uid}-veil)`} pointerEvents="none" />
          {/* 选中：呼吸涟漪 + 静态描边 + 就地信息卡（不依赖右侧栏，窄屏也看得见） */}
          {selectedGeo && selectedCell && (
            <>
              <g
                className="cmh-ripple"
                style={{ transformOrigin: `${selectedGeo.cx ?? 0}px ${selectedGeo.cy ?? 0}px` }}
              >
                <path
                  d={selectedGeo.d}
                  fill="none"
                  stroke="var(--color-accent)"
                  strokeWidth={3}
                  strokeLinejoin="round"
                  pointerEvents="none"
                />
              </g>
              <path
                d={selectedGeo.d}
                fill="none"
                stroke="var(--color-accent)"
                strokeWidth={2.4}
                strokeLinejoin="round"
                pointerEvents="none"
              />
              {pop && (
                <g
                  className="cmh-pop"
                  style={{ transformOrigin: `${pop.anchorX}px ${pop.anchorY}px` }}
                  pointerEvents="none"
                >
                  <line
                    x1={selectedGeo.cx ?? 0}
                    y1={selectedGeo.cy ?? 0}
                    x2={pop.anchorX}
                    y2={pop.anchorY}
                    stroke="var(--color-accent)"
                    strokeWidth={1}
                  />
                  <rect
                    x={pop.x}
                    y={pop.y}
                    width={POP_W}
                    height={POP_H}
                    rx={8}
                    fill="#ffffff"
                    stroke="var(--color-accent)"
                    strokeWidth={1.2}
                  />
                  <text x={pop.x + 12} y={pop.y + 20} fontSize={12} fontWeight={600}
                    style={{ fill: 'var(--color-ink)' }}
                  >
                    {selectedGeo.short}
                    <tspan dx={6} fontSize={10} fill="var(--color-accent)"
                      style={{ fontFamily: 'var(--font-mono)' }}
                    >
                      第 {selectedRank} 名
                    </tspan>
                  </text>
                  <text x={pop.x + 12} y={pop.y + 41} fontSize={15} fontWeight={700} fill="#0a5cb8"
                    style={{ fontFamily: 'var(--font-mono)' }}
                  >
                    {fmtInt(selectedCell.count)}
                    <tspan dx={3} fontSize={9.5} fontWeight={400}
                      style={{ fill: 'var(--color-neutral)' }}
                    >
                      个项目
                    </tspan>
                  </text>
                  <text x={pop.x + 12} y={pop.y + 56} fontSize={10}
                    style={{ fill: 'var(--color-neutral)' }}
                  >
                    占全省域 {((selectedCell.count / total) * 100).toFixed(1)}%
                  </text>
                </g>
              )}
            </>
          )}
          {/* 标注框是说明性的，不让它挡住底下省份的点击 */}
          <g className="cmh-callouts" pointerEvents="none">
            {callouts}
          </g>
        </svg>
      </div>

      {/* 右侧：选中明细 / 竖向热力条 / TOP 5 */}
      <div className="grid content-start gap-2.5">
        <div
          className={`rounded-[var(--radius-md)] border p-3 transition-colors duration-[var(--dur-micro)] ${
            selected
              ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)]'
              : 'border-[var(--color-rule)] bg-white'
          }`}
        >
          {selected && selectedCell ? (
            <>
              <p className="text-[10.5px] tracking-[0.06em] text-[var(--color-muted)]">
                已选省份
              </p>
              <p className="mt-1 flex items-baseline gap-1.5 text-[13px] font-semibold text-[var(--color-ink)]">
                {selectedGeo?.short ?? selected}
                <span className="font-mono text-[10px] text-[var(--color-accent)]">
                  第 {selectedRank} 名
                </span>
              </p>
              <p className="mt-1.5 font-mono text-[18px] font-semibold leading-none text-[#0a5cb8]">
                {fmtInt(selectedCell.count)}
                <span className="ml-1 text-[10px] font-normal text-[var(--color-neutral)]">个</span>
              </p>
              <p className="mt-1.5 text-[11px] text-[var(--color-muted)]">
                占全省域 {((selectedCell.count / total) * 100).toFixed(1)}%
              </p>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="mt-2 text-[10.5px] text-[var(--color-neutral)] underline-offset-2 hover:text-[var(--color-accent)] hover:underline"
              >
                取消选中
              </button>
            </>
          ) : (
            <>
              <p className="text-[10.5px] tracking-[0.06em] text-[var(--color-muted)]">
                省份明细
              </p>
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-[var(--color-neutral)]">
                点击地图上的任一省份，查看它的项目数与占全省域比例。
              </p>
            </>
          )}
        </div>

        <div className="rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-white p-3">
          <p className="text-[10.5px] tracking-[0.06em] text-[var(--color-muted)]">商机热力值</p>
          <div className="mt-2.5 flex items-stretch gap-2.5">
            <div className="flex flex-col justify-between text-[9.5px] text-[var(--color-neutral)]">
              <span>高</span>
              <span>中</span>
              <span>低</span>
            </div>
            <div
              className="h-32 w-[9px] shrink-0 rounded-full"
              style={{
                background: `linear-gradient(180deg, ${[...HEAT_RAMP].reverse().join(',')})`,
              }}
            />
            <div className="flex flex-col justify-between font-mono text-[9.5px] text-[var(--color-neutral)]">
              <span className="tnum">{fmtInt(HEAT_THRESHOLDS[0])}+</span>
              <span className="tnum">{fmtInt(HEAT_THRESHOLDS[2])}</span>
              <span className="tnum">&lt;{fmtInt(HEAT_THRESHOLDS[3])}</span>
            </div>
          </div>
        </div>

        <div className="rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-white p-3">
          <p className="text-[10.5px] tracking-[0.06em] text-[var(--color-muted)]">
            TOP 5 地区
          </p>
          <ul className="mt-2 space-y-1.5">
            {top5.map((cell, i) => {
              const geo = geometry.get(cell.region);
              return (
                <li key={cell.region} className="flex items-center gap-1.5 text-[11px]">
                  <span className="w-3 text-right font-mono text-[9.5px] text-[var(--color-neutral)]">
                    {i + 1}
                  </span>
                  <span className="w-10 shrink-0 text-[var(--color-ink-2)]">
                    {geo?.short ?? cell.region}
                  </span>
                  <span className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--color-paper-2)]">
                    <span
                      className="block h-full rounded-full"
                      style={{
                        width: `${(cell.count / vmax) * 100}%`,
                        background: 'linear-gradient(90deg,#7bb6f4,#0076ed)',
                      }}
                    />
                  </span>
                  <span className="tnum w-10 text-right font-mono text-[10.5px] text-[#0a5cb8]">
                    {fmtInt(cell.count)}
                  </span>
                </li>
              );
            })}
          </ul>
          <button
            type="button"
            onClick={() => setShowAll(true)}
            className="mt-2.5 w-full rounded-[var(--radius-sm)] border border-[var(--color-rule-2)] py-1.5 text-[11px] font-medium text-[var(--color-ink)] transition-colors duration-[var(--dur-micro)] ease-out hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
          >
            显示全部省份情况
          </button>
          {missingFromMap.length > 0 && (
            <p className="mt-2 border-t border-[var(--color-rule)] pt-2 text-[10px] leading-relaxed text-[var(--color-neutral)]">
              {missingFromMap.join('、')} 不属于省级行政区，仅计入列表，不上图。
            </p>
          )}
        </div>
      </div>

      {showAll && (
        <AllProvincesDialog
          ranked={ranked}
          total={total}
          vmax={vmax}
          onClose={() => setShowAll(false)}
        />
      )}
    </div>
  );
}

/** 全部省份占比浮窗。 */
function AllProvincesDialog({
  ranked,
  total,
  vmax,
  onClose,
}: {
  ranked: KanbanHeatmapCell[];
  total: number;
  vmax: number;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[85] flex items-center justify-center px-4 py-6">
      <div
        className="absolute inset-0 bg-[oklch(24%_0.02_258/0.22)] backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="全部省份占比"
        className="relative z-10 flex max-h-[80vh] w-full max-w-[30rem] flex-col rounded-[var(--radius-xl)] border border-[var(--color-rule)] bg-[var(--color-paper)] shadow-[var(--shadow-float)]"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--color-rule)] px-5 py-4">
          <div>
            <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
              全部省份占比
            </h2>
            <p className="mt-0.5 text-[11.5px] text-[var(--color-neutral)]">
              共 {ranked.length} 个地区 · 合计{' '}
              <span className="tnum font-mono">{fmtInt(total)}</span> 个项目
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            autoFocus
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--color-neutral)] transition-colors duration-[var(--dur-micro)] ease-out hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
          <table className="w-full border-collapse text-[12px]">
            <thead className="sticky top-0 bg-[var(--color-paper)]">
              <tr className="border-b border-[var(--color-rule)]">
                <th scope="col" className="w-8 py-1.5 text-right font-medium text-[var(--color-neutral)]">
                  #
                </th>
                <th scope="col" className="py-1.5 pl-2 text-left font-medium text-[var(--color-neutral)]">
                  地区
                </th>
                <th scope="col" className="py-1.5 text-right font-medium text-[var(--color-neutral)]">
                  项目数
                </th>
                <th scope="col" className="py-1.5 pl-3 text-left font-medium text-[var(--color-neutral)]">
                  占比
                </th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((c, i) => {
                const pct = (c.count / total) * 100;
                return (
                  <tr key={c.region} className="border-b border-[var(--color-rule)] last:border-0">
                    <td className="tnum py-1.5 text-right font-mono text-[10.5px] text-[var(--color-neutral)]">
                      {i + 1}
                    </td>
                    <td className="py-1.5 pl-2 text-[var(--color-ink-2)]">{c.region}</td>
                    <td className="tnum py-1.5 text-right font-mono text-[var(--color-ink)]">
                      {fmtInt(c.count)}
                    </td>
                    <td className="py-1.5 pl-3">
                      <span className="flex items-center gap-2">
                        <span className="h-1.5 w-20 overflow-hidden rounded-full bg-[var(--color-paper-2)]">
                          <span
                            className="block h-full rounded-full"
                            style={{
                              width: `${(c.count / vmax) * 100}%`,
                              background: 'linear-gradient(90deg,#7bb6f4,#0076ed)',
                            }}
                          />
                        </span>
                        <span className="tnum font-mono text-[10.5px] text-[var(--color-neutral)]">
                          {pct.toFixed(1)}%
                        </span>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
