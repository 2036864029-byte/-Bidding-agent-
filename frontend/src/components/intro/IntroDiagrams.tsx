/**
 * IntroDiagrams —— 功能介绍页的手绘 SVG 示意图（Tier B 手工构建）。
 *
 * 为什么用 SVG 而不是截图或图库插画：
 *   ① 说明的是**机制**（一次提问如何分流、企业库如何被筛出来），
 *      截图只能展示"某个时刻的界面"，说明不了机制；
 *   ② 与 Cobalt 主题同源 —— 发丝线 + 单一钴蓝信号，颜色全部走 token，
 *      不引入第二种视觉语气（图标/插画混风格是 AI 生成页的典型痕迹）。
 *
 * 所有描边/填充只用 var(--color-*) token 或 currentColor，不写死颜色值。
 */

const RULE = 'stroke-[var(--color-rule-2)]';
const RULE_SOFT = 'stroke-[var(--color-rule)]';
const ACCENT = 'stroke-[var(--color-accent)]';
const FILL_ACCENT = 'fill-[var(--color-accent)]';
const INK = 'fill-[var(--color-ink)]';
const MUTED = 'fill-[var(--color-muted)]';
const NEUTRAL = 'fill-[var(--color-neutral)]';
const PAPER2 = 'fill-[var(--color-paper-2)]';

/** 统一的图内文字样式。 */
const T = 'font-mono text-[9px]';
const TS = 'font-mono text-[8px]';

function Label({
  x,
  y,
  children,
  cls = MUTED,
  size = TS,
}: {
  x: number;
  y: number;
  children: string;
  cls?: string;
  size?: string;
}) {
  return (
    <text x={x} y={y} className={`${size} ${cls}`} fontFamily="var(--font-mono)">
      {children}
    </text>
  );
}

/**
 * SystemMap —— 一次提问的完整链路：提问 → 路由判定 → 分派到能力 → 落到数据底座 → 出答案。
 * 画成竖向链路（而非横向分叉 + 曲线汇聚）：横向曲线在窄栏里会缠在一起，
 * 竖向堆叠在任何宽度下都读得清，也如实反映"逐级下沉"的真实结构。
 */
export function SystemMap() {
  const caps = ['法规问答', '数据查询', '文档问答', '深度综合'];
  const CHIP_W = 106;
  return (
    <svg viewBox="0 0 460 302" className="h-auto w-full" role="img"
         aria-label="系统链路示意：提问经路由判定分派到四类能力，下沉到数据底座，产出带来源的答案">
      {/* ① 提问 */}
      <rect x="10" y="10" width="80" height="28" rx="6" className={`${PAPER2} ${RULE}`} strokeWidth="1" />
      <Label x={26} y={28} cls={INK} size={T}>提问</Label>
      <path d="M50 38 V60" className={RULE} strokeWidth="1" />
      <path d="M46 56 L50 61 L54 56" fill="none" className={RULE} strokeWidth="1" />

      {/* ② 路由判定（唯一高亮：它是本轮的分水岭） */}
      <rect x="10" y="62" width="130" height="30" rx="6" className={ACCENT} strokeWidth="1.5" fill="none" />
      <circle cx="26" cy="77" r="3" className={FILL_ACCENT} />
      <Label x={36} y={81} cls={INK}>路由判定</Label>
      <path d="M75 92 V114" className={RULE} strokeWidth="1" />
      <path d="M71 110 L75 115 L79 110" fill="none" className={RULE} strokeWidth="1" />
      <Label x={84} y={107} cls={NEUTRAL}>每轮只走一条</Label>

      {/* ③ 四类能力（并列候选） */}
      {caps.map((c, i) => (
        <g key={c}>
          <rect x={9 + i * (CHIP_W + 6)} y="116" width={CHIP_W} height="30" rx="6"
                className={`${PAPER2} ${RULE_SOFT}`} strokeWidth="1" />
          <Label x={24 + i * (CHIP_W + 6)} y={135} cls={MUTED}>{c}</Label>
        </g>
      ))}
      {/* 分派：从判定框落到四张候选卡 */}
      <path d="M75 92 H232 V114" fill="none" className={RULE_SOFT} strokeWidth="1" />
      {caps.map((_, i) => (
        <path key={i} d={`M${9 + i * (CHIP_W + 6) + CHIP_W / 2} 146 V166`} className={RULE_SOFT} strokeWidth="1" />
      ))}
      <path d="M232 162 L232 167 L236 162" fill="none" className={RULE_SOFT} strokeWidth="1" />

      {/* ④ 数据底座 */}
      <rect x="9" y="170" width="442" height="48" rx="6" className={`${PAPER2} ${RULE}`} strokeWidth="1" />
      <Label x={22} y={189} cls={INK}>数据底座</Label>
      <Label x={22} y={204} cls={MUTED}>
        中标项目 18,497 · 企业工商 39,969 · 中标记录 12,209 · 公告 802
      </Label>
      <rect x="300" y="180" width="140" height="24" rx="4" className={RULE_SOFT} strokeWidth="1" fill="none" />
      <Label x={312} y={196} cls={NEUTRAL}>法规知识库 · 会话文档</Label>

      {/* ⑤ 落点 */}
      <path d="M230 218 V246" className={ACCENT} strokeWidth="1.5" />
      <path d="M225 242 L230 247 L235 242" fill="none" className={ACCENT} strokeWidth="1.5" />
      <rect x="150" y="250" width="160" height="30" rx="6" className={RULE} strokeWidth="1" fill="none" />
      <circle cx="167" cy="265" r="3" className={FILL_ACCENT} />
      <Label x={178} y={269} cls={INK}>带来源的答案</Label>
    </svg>
  );
}

/** ChatBranches —— 七种回答方式的分流示意。 */
export function ChatBranches() {
  const rows = [
    ['法规知识问答', true],
    ['价格与标讯查询', true],
    ['文档问答', true],
    ['深度综合', true],
    ['澄清反问', false],
    ['通用对话', false],
    ['兜底引导', false],
  ] as const;
  return (
    <svg viewBox="0 0 380 232" className="h-auto w-full" role="img"
         aria-label="一次提问被分流到七种回答方式">
      <rect x="6" y="96" width="58" height="28" rx="6" className={`${PAPER2} ${RULE}`} strokeWidth="1" />
      <Label x={20} y={114} cls={INK}>提问</Label>
      {rows.map(([name, accent], i) => {
        const y = 14 + i * 30;
        return (
          <g key={name}>
            <path d={`M64 110 C104 110 108 ${y + 12} 148 ${y + 12}`} fill="none"
                  className={accent ? RULE_SOFT : RULE_SOFT} strokeWidth="1" />
            <rect x="150" y={y} width="126" height="24" rx="6"
                  className={accent ? `${PAPER2} ${ACCENT}` : `${PAPER2} ${RULE_SOFT}`} strokeWidth="1" />
            <circle cx="163" cy={y + 12} r="2.5"
                    className={accent ? FILL_ACCENT : 'fill-[var(--color-neutral)]'} />
            <Label x={172} y={y + 16} cls={MUTED}>{name}</Label>
          </g>
        );
      })}
      <Label x={300} y={110} cls={NEUTRAL}>每轮只走一条</Label>
    </svg>
  );
}

/** InquiryFlow —— 项目概况 → 语义检索 → 相似项目与金额区间。 */
export function InquiryFlow() {
  return (
    <svg viewBox="0 0 420 210" className="h-auto w-full" role="img"
         aria-label="询价链路：项目概况经语义检索匹配历史中标公告，给出相似项目与金额区间">
      <rect x="8" y="20" width="150" height="76" rx="6" className={`${PAPER2} ${RULE}`} strokeWidth="1" />
      <Label x={20} y={38} cls={INK}>项目概况</Label>
      {[0, 1, 2, 3, 4].map((i) => (
        <rect key={i} x={20} y={48 + i * 9} width={i === 4 ? 62 : 124} height="3" rx="1.5"
              className="fill-[var(--color-rule-2)]" />
      ))}

      <path d="M158 58 H196" className={RULE} strokeWidth="1" />
      <path d="M192 54 L197 58 L192 62" fill="none" className={RULE} strokeWidth="1" />
      <rect x="198" y="40" width="76" height="36" rx="6" className={RULE} strokeWidth="1" fill="none" />
      <Label x={210} y={55} cls={MUTED}>语义检索</Label>
      <Label x={210} y={67} cls={NEUTRAL}>802 条公告</Label>

      <path d="M274 58 H312" className={RULE} strokeWidth="1" />
      <path d="M308 54 L313 58 L308 62" fill="none" className={RULE} strokeWidth="1" />

      {[0, 1, 2].map((i) => (
        <g key={i}>
          <rect x="316" y={12 + i * 34} width="96" height="30" rx="6"
                className={i === 0 ? `${PAPER2} ${ACCENT}` : `${PAPER2} ${RULE_SOFT}`} strokeWidth="1" />
          <Label x={326} y={26 + i * 34} cls={MUTED}>{`相似项目 ${i + 1}`}</Label>
          <Label x={326} y={37 + i * 34} cls={NEUTRAL}>中标金额</Label>
        </g>
      ))}

      {/* 金额区间标尺 —— 询价模块真正的产出 */}
      <path d="M8 148 H412" className={RULE_SOFT} strokeWidth="1" />
      {[0, 1, 2, 3, 4].map((i) => (
        <path key={i} d={`M${40 + i * 86} 144 V152`} className={RULE_SOFT} strokeWidth="1" />
      ))}
      <rect x="126" y="132" width="150" height="8" rx="4" className="fill-[var(--color-accent-soft)]" />
      <path d="M126 136 H276" className={ACCENT} strokeWidth="1.5" />
      <Label x={126} y={126} cls={INK}>金额区间（万元）</Label>
      {['0', '低', '中位', '高', ''].map((t, i) =>
        t ? (
          <Label key={i} x={36 + i * 86} y={166} cls={NEUTRAL}>{t}</Label>
        ) : null
      )}

      <rect x="8" y="180" width="404" height="24" rx="6" className={RULE_SOFT} strokeWidth="1" fill="none" />
      <Label x={20} y={196} cls={MUTED}>每条结果附「查看原文」外链，可直达政府采购网原公告</Label>
    </svg>
  );
}

/** SupplierFacets —— 三个筛选维度 → 企业卡（工商 + 中标业绩）。 */
export function SupplierFacets() {
  const facets = ['地区', '行业', '关键词'];
  return (
    <svg viewBox="0 0 420 214" className="h-auto w-full" role="img"
         aria-label="供应商筛选：地区、行业、关键词三个维度筛出企业卡，卡内含工商信息与中标业绩">
      {facets.map((f, i) => (
        <g key={f}>
          <rect x="8" y={16 + i * 42} width="88" height="30" rx="6"
                className={`${PAPER2} ${RULE}`} strokeWidth="1" />
          <Label x={24} y={35 + i * 42} cls={INK}>{f}</Label>
          <path d={`M96 ${31 + i * 42} H128`} className={RULE_SOFT} strokeWidth="1" />
          <path d={`M124 ${27 + i * 42} L129 ${31 + i * 42} L124 ${35 + i * 42}`} fill="none"
                className={RULE_SOFT} strokeWidth="1" />
        </g>
      ))}

      {/* 汇入筛选器 */}
      <path d="M128 31 C154 31 156 90 178 90" fill="none" className={RULE_SOFT} strokeWidth="1" />
      <path d="M128 73 C154 73 156 92 178 92" fill="none" className={RULE_SOFT} strokeWidth="1" />
      <path d="M128 115 C154 115 156 96 178 96" fill="none" className={RULE_SOFT} strokeWidth="1" />
      <rect x="180" y="72" width="66" height="44" rx="6" className={`${PAPER2} ${ACCENT}`} strokeWidth="1" />
      <Label x={190} y={90} cls={INK}>筛选器</Label>
      <Label x={190} y={103} cls={NEUTRAL}>服务端</Label>

      <path d="M246 94 H286" className={RULE} strokeWidth="1" />
      <path d="M282 90 L287 94 L282 98" fill="none" className={RULE} strokeWidth="1" />

      {/* 企业卡：工商字段 + 中标业绩 */}
      <rect x="290" y="14" width="122" height="96" rx="6" className={`${PAPER2} ${RULE}`} strokeWidth="1" />
      <Label x={300} y={32} cls={INK}>企业名称</Label>
      <Label x={300} y={45} cls={NEUTRAL}>统一社会信用代码</Label>
      <path d="M300 52 H402" className={RULE_SOFT} strokeWidth="1" />
      <Label x={300} y={67} cls={MUTED}>行业 · 地区 · 经营状态</Label>
      <Label x={300} y={80} cls={MUTED}>企业规模 · 注册资本</Label>
      <path d="M300 87 H402" className={RULE_SOFT} strokeWidth="1" />
      <circle cx="305" cy="99" r="2.5" className={FILL_ACCENT} />
      <Label x={313} y={102} cls={INK}>中标次数 · 中标金额</Label>

      <rect x="290" y="122" width="122" height="80" rx="6" className={RULE} strokeWidth="1" fill="none" />
      <Label x={300} y={140} cls={MUTED}>下一页的同一形态卡</Label>
      <Label x={300} y={153} cls={NEUTRAL}>一页 60 家</Label>
      <Label x={300} y={166} cls={NEUTRAL}>页头显示「共 N 家」</Label>
      <path d="M300 174 H402" className={RULE_SOFT} strokeWidth="1" />
      <Label x={300} y={188} cls={NEUTRAL}>无工商字段者标注说明</Label>
    </svg>
  );
}

/** KanbanShapes —— 看板的三种视图（数量口径）。 */
export function KanbanShapes() {
  const bars = [26, 44, 18, 52, 34, 62, 30, 48];
  return (
    <svg viewBox="0 0 420 200" className="h-auto w-full" role="img"
         aria-label="商机看板三种视图：行业走势多序列、地区热力、行业占比">
      {/* 走势 */}
      <rect x="8" y="12" width="200" height="104" rx="6" className={`${PAPER2} ${RULE}`} strokeWidth="1" />
      <Label x={20} y={30} cls={INK}>行业走势</Label>
      <path d="M24 100 H192" className={RULE_SOFT} strokeWidth="1" />
      {bars.map((h, i) => (
        <rect key={i} x={30 + i * 20} y={100 - h} width="10" height={h} rx="2"
              className={i === 5 ? FILL_ACCENT : 'fill-[var(--color-rule-2)]'} />
      ))}
      <Label x={20} y={112} cls={NEUTRAL}>交易类型 × 月份 · 多序列</Label>

      {/* 热力 */}
      <rect x="216" y="12" width="196" height="104" rx="6" className={`${PAPER2} ${RULE}`} strokeWidth="1" />
      <Label x={228} y={30} cls={INK}>地区热力</Label>
      {Array.from({ length: 24 }).map((_, i) => {
        const r = Math.floor(i / 8);
        const c = i % 8;
        const op = [0.14, 0.3, 0.55, 1][(i * 7) % 4];
        return (
          <rect key={i} x={232 + c * 20} y={42 + r * 20} width="14" height="14" rx="3"
                className="fill-[var(--color-accent)]" opacity={op} />
        );
      })}
      <Label x={228} y={110} cls={NEUTRAL}>深浅 = 数量相对值</Label>

      {/* 占比 */}
      <rect x="8" y="124" width="404" height="64" rx="6" className={RULE} strokeWidth="1" fill="none" />
      <Label x={20} y={142} cls={INK}>行业占比</Label>
      <path d="M20 164 H392" className={RULE_SOFT} strokeWidth="1" />
      <rect x="20" y={158} width="132" height="12" rx="3" className="fill-[var(--color-accent)]" />
      <rect x="152" y={158} width="74" height="12" rx="3" className="fill-[var(--color-accent)]" opacity="0.55" />
      <rect x="226" y={158} width="96" height="12" rx="3" className="fill-[var(--color-rule-2)]" />
      <rect x="322" y={158} width="70" height="12" rx="3" className="fill-[var(--color-rule-2)]" />
      <Label x={20} y={182} cls={NEUTRAL}>数量口径 · 看板不含金额维度（聚合源无金额指标）</Label>
    </svg>
  );
}
