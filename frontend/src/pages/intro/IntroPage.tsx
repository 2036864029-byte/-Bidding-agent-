import type { ReactElement } from 'react';
import { Link } from 'react-router';
import { ArrowRight, FileText, MessageSquare, ScanSearch, Table2 } from 'lucide-react';
import { SiteHeader } from '@/components/layout/SiteHeader';
import { Brand } from '@/components/common/Brand';
import {
  ChatBranches,
  InquiryFlow,
  KanbanShapes,
  SupplierFacets,
  SystemMap,
} from '@/components/intro/IntroDiagrams';

/**
 * 功能介绍 —— 图文结合的功能说明页。
 *
 * Hallmark · pre-emit critique: P4 H4 E4 S5 R4 V4
 * Hallmark · genre: modern-minimal · macrostructure: Split Studio · theme: cobalt-tuned
 *          · enrichment: hand-built SVG (Tier B) · nav: N1 (既有 SiteHeader，共享外壳)
 *
 * 版式：**Split Studio**（交替双栏：一侧叙述、一侧机制示意图）。
 * 为什么不用三列等宽卡片：那是最常见的模板形态，"每个功能一张卡"读起来像目录，
 * 说明不了任何机制。交替双栏让每个模块都有一次"文字解释 + 图示机制"的对照。
 *
 * 全页遵守 Cobalt 主题：冷纸面 + 发丝线 + 单一钴蓝信号（仅用于「当前/选中」语义），
 * **仅一条暗带**（数据底座）。示意图全部手绘 SVG，不引外部插图库。
 * 数字只用真实值（来自代码与数据库统计），不编造任何指标。
 */

type ModuleItem = {
  to: string;
  name: string;
  icon: typeof MessageSquare;
  claim: string;
  points: string[];
  diagram: () => ReactElement;
  /** 图示放在文字左侧还是右侧（交替） */
  mediaFirst?: boolean;
};

const MODULES: ModuleItem[] = [
  {
    to: '/chat',
    name: '对话控制台',
    icon: MessageSquare,
    claim: '一个输入框，七种回答方式',
    points: [
      '问题先被判定类型，再走对应的回答链路 —— 法规问答带可点开的引用来源，数据查询回真实记录表，文档问答标明命中的段落。',
      '追问可以省略主语：「招标方式有哪些？」之后直接问「具体介绍一下？」即可接上。',
      '上传标书、合同、图片后提问，文件只进当前会话的私有索引，不污染公共知识库。',
    ],
    diagram: ChatBranches,
  },
  {
    to: '/inquiry',
    name: '智能询价',
    icon: ScanSearch,
    claim: '一段项目概况，换一份报价参考',
    points: [
      '粘贴项目概况，语义匹配历史中标公告，返回相似项目卡与金额区间（万元）。',
      '每条结果附原文链接，可直达政府采购网原公告核对。',
      '也可直接上传项目文件作为查询输入。',
    ],
    diagram: InquiryFlow,
    mediaFirst: true,
  },
  {
    to: '/supplier',
    name: '供应商全景',
    icon: Table2,
    claim: '按地区、行业、关键词筛出企业档案',
    points: [
      '覆盖 39,969 家企业，筛选在服务端完成。',
      '卡片给出工商信息与中标业绩 —— 中标次数与中标金额直接可核。',
      '一页 60 家，页头如实显示总量；缺工商字段的企业会标注说明，不做遮掩。',
    ],
    diagram: SupplierFacets,
  },
  {
    to: '/kanban',
    name: '商机看板',
    icon: FileText,
    claim: '数量口径的行业视图',
    points: [
      '行业走势（交易类型 × 月份，多序列）、地区热力、行业占比。',
      '没有金额维度是刻意的：聚合源本就没有金额指标，画出来只是一条没有意义的线。',
      '没有信号就不画图，而不是凑一张好看的图。',
    ],
    diagram: KanbanShapes,
    mediaFirst: true,
  },
];

/** 七种回答方式 —— 用清单而非卡片网格：它是一份枚举，不是七个卖点。 */
const BRANCHES = [
  ['法规知识问答', '问法规、程序、评标规则、投标流程、履约验收。正文附可点开的【来源N】引用卡片。'],
  ['价格与标讯查询', '问企业工商、处罚不良记录、中标记录、采购项目、项目编号。返回结果表，并可跳转到对应模块。'],
  ['文档问答', '上传文件后要求分析、解读、对比、提取。列出命中的来源段落，相关度不足时明确提示。'],
  ['深度综合', '上传了文件、且问题需要跨源比对时（如「这份标书这样约定合规吗」），展示规划了哪几步、每步查到什么。'],
  ['澄清反问', '问题信息不足时反问，并给出可一键选择的候选，替代直接拒答。'],
  ['通用对话', '问候、问能力、操作引导。'],
  ['兜底引导', '意图无法判定时给出能力引导，不猜测。'],
] as const;

const DATA_SOURCES = [
  ['结构化业务库', '中标项目 18,497 条 · 企业 39,969 家 · 中标记录 12,209 条'],
  ['法规知识库', '权威法规 PDF 分块向量，标题感知分块、保留层级结构'],
  ['公告语义索引', '802 条中标公告，正文 + 产品清单参与语义匹配'],
  ['会话私有文档', '上传文件按会话隔离，只进当前会话索引'],
] as const;

const LIMITS = [
  ['检索不到就直说', '法规问答设相关度下限，不足时明确提示「未检索到」，不生成看似合理实则无据的条文。'],
  ['数据有时空边界', '收录范围取决于采集，回答中标注数据来源；未收录不等于现实中不存在。'],
  ['工商字段有缺口', '部分新入库企业暂无工商信息，卡片会标注，按地区或行业筛选时不会出现。'],
  ['金额口径不混用', '询价模块统一万元；对话表格中的中标金额为元，列头写明单位。'],
  ['匿名会话', '无需登录，会话之间数据隔离。'],
] as const;

export function IntroPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />

      <main className="flex-1">
        {/* ── 开篇：文字 + 系统链路图（Split Studio 的开场对位） ── */}
        <section className="border-b border-[var(--color-rule)]">
          <div className="mx-auto grid w-full max-w-[72rem] gap-x-14 gap-y-10 px-6 py-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:items-center">
            <div>
              <h1 className="text-[2rem] font-semibold leading-[1.15] tracking-[-0.03em] text-[var(--color-ink)] sm:text-[2.4rem]">
                招投标领域的可核验问答
              </h1>
              <p className="mt-4 max-w-lg text-[14.5px] leading-relaxed text-[var(--color-ink-2)]">
                法规条文、企业工商、中标记录、你上传的标书 —— 四类数据接在同一套检索上。
                答案带来源，数据回真值，检索不到就直说。
              </p>
              <dl className="mt-7 grid gap-x-8 gap-y-4 sm:grid-cols-2">
                {[
                  ['四类数据源', '法规 · 工商 · 标讯 · 上传文档'],
                  ['六种处理能力', '按问题类型自动分派'],
                  ['答案可核验', '引用来源可点开对照'],
                  ['免登录', '匿名会话，数据互相隔离'],
                ].map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-[13px] font-medium text-[var(--color-ink)]">{k}</dt>
                    <dd className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--color-muted)]">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <figure className="min-w-0">
              <SystemMap />
              <figcaption className="mt-3 text-[12px] leading-relaxed text-[var(--color-neutral)]">
                一次提问的完整链路：判定类型 → 分派到对应能力 → 落到数据底座 → 产出带来源的答案。
              </figcaption>
            </figure>
          </div>
        </section>

        {/* ── 四个模块：交替双栏 ── */}
        <section className="mx-auto w-full max-w-[72rem] px-6 py-14">
          <h2 className="text-[1.35rem] font-semibold tracking-[-0.02em] text-[var(--color-ink)]">
            四个功能模块
          </h2>
          <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-[var(--color-muted)]">
            对话控制台是主入口，另外三个模块把同一批数据的某一种用法展开成独立界面。
          </p>

          <div className="mt-12 space-y-16">
            {MODULES.map((m) => {
              const Icon = m.icon;
              const Diagram = m.diagram;
              return (
                <article
                  key={m.to}
                  className="grid gap-x-14 gap-y-8 border-t border-[var(--color-rule)] pt-12 first:border-t-0 first:pt-0 lg:grid-cols-2 lg:items-center"
                >
                  <div className={m.mediaFirst ? 'lg:order-2' : ''}>
                    <div className="flex items-center gap-2">
                      <Icon className="h-4 w-4 shrink-0 text-[var(--color-accent)]" aria-hidden />
                      <h3 className="text-[1.05rem] font-semibold tracking-[-0.01em] text-[var(--color-ink)]">
                        {m.name}
                      </h3>
                    </div>
                    <p className="mt-2 text-[13.5px] font-medium text-[var(--color-ink-2)]">{m.claim}</p>
                    <ul className="mt-4 space-y-2.5">
                      {m.points.map((p) => (
                        <li
                          key={p}
                          className="border-l border-[var(--color-rule-2)] pl-3 text-[13px] leading-relaxed text-[var(--color-muted)]"
                        >
                          {p}
                        </li>
                      ))}
                    </ul>
                    <Link
                      to={m.to}
                      className="mt-5 inline-flex items-center gap-1.5 rounded-md border border-[var(--color-rule-2)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--color-ink)] transition-colors duration-[var(--dur-micro)] ease-out hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
                    >
                      打开{m.name}
                      <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                    </Link>
                  </div>
                  <figure className={`min-w-0 ${m.mediaFirst ? 'lg:order-1' : ''}`}>
                    <div className="rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] p-4">
                      <Diagram />
                    </div>
                  </figure>
                </article>
              );
            })}
          </div>
        </section>

        {/* ── 七种回答方式：清单（枚举，不是卖点网格） ── */}
        <section className="border-y border-[var(--color-rule)] bg-[var(--color-paper-2)]">
          <div className="mx-auto grid w-full max-w-[72rem] gap-x-14 gap-y-10 px-6 py-14 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
            <div className="lg:sticky lg:top-24 lg:self-start">
              <h2 className="text-[1.35rem] font-semibold tracking-[-0.02em] text-[var(--color-ink)]">
                对话控制台的七种回答方式
              </h2>
              <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--color-muted)]">
                提问不需要挑选模式。系统先判定类型，再决定用哪种方式回答 ——
                每一轮只走其中一条。
              </p>
              <figure className="mt-6 hidden lg:block">
                <ChatBranches />
              </figure>
            </div>
            <ol className="min-w-0 divide-y divide-[var(--color-rule)] border-t border-[var(--color-rule)]">
              {BRANCHES.map(([name, desc], i) => (
                <li key={name} className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-3 py-4">
                  <span className="tnum font-mono text-[12px] leading-6 text-[var(--color-neutral)]">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <div className="min-w-0">
                    <p className="text-[13.5px] font-medium text-[var(--color-ink)]">{name}</p>
                    <p className="mt-1 text-[13px] leading-relaxed text-[var(--color-muted)]">{desc}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* ── 数据底座：全页唯一的暗带 ── */}
        <section className="bg-[var(--color-graphite)] text-[var(--color-graphite-ink)]">
          <div className="mx-auto w-full max-w-[72rem] px-6 py-14">
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-[color-mix(in_oklab,var(--color-graphite-ink)_18%,transparent)] pb-4">
              <h2 className="text-[1.35rem] font-semibold tracking-[-0.02em] text-[var(--color-graphite-ink)]">
                数据底座
              </h2>
              <p className="font-mono text-[11px] tracking-[0.06em] text-[color-mix(in_oklab,var(--color-graphite-ink)_62%,transparent)]">
                4 SOURCES · 2 STORES
              </p>
            </div>
            <dl className="mt-6 grid gap-x-12 gap-y-5 sm:grid-cols-2">
              {DATA_SOURCES.map(([k, v]) => (
                <div key={k} className="min-w-0 border-l border-[color-mix(in_oklab,var(--color-accent)_55%,transparent)] pl-4">
                  <dt className="text-[13px] font-medium text-[var(--color-graphite-ink)]">{k}</dt>
                  <dd className="mt-1 text-[12.5px] leading-relaxed text-[color-mix(in_oklab,var(--color-graphite-ink)_72%,transparent)]">
                    {v}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        {/* ── 边界与口径 ── */}
        <section className="mx-auto w-full max-w-[72rem] px-6 py-14">
          <h2 className="text-[1.35rem] font-semibold tracking-[-0.02em] text-[var(--color-ink)]">
            边界与口径
          </h2>
          <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-[var(--color-muted)]">
            这些是该系统做不到或做得不完整的地方。写在这里，比藏在回答里好。
          </p>
          <dl className="mt-8 grid gap-x-12 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
            {LIMITS.map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-[13px] font-medium text-[var(--color-ink)]">{k}</dt>
                <dd className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--color-muted)]">{v}</dd>
              </div>
            ))}
          </dl>
        </section>
      </main>

      <footer className="border-t border-[var(--color-rule)]">
        <div className="mx-auto flex w-full max-w-[72rem] flex-wrap items-center gap-x-4 gap-y-2 px-6 py-6">
          <Brand compact />
          <p className="text-[12.5px] text-[var(--color-muted)]">
            政府采购与招投标领域的智能检索助手
          </p>
          <Link
            to="/chat"
            className="ml-auto text-[12.5px] text-[var(--color-accent)] transition-colors duration-[var(--dur-micro)] ease-out hover:underline"
          >
            开始提问
          </Link>
        </div>
      </footer>
    </div>
  );
}
