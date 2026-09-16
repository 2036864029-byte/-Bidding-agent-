import type { ChatRawResponse, ReplyType } from '@/types/chat';

/**
 * 对话主模块 —— 7 种 reply_type 的**原始响应 fixture**（raw 形态）。
 *
 * 用途：① 适配器单测（验证 chatAdapter 按分支归一）；② api.chat.send 的 mock 模式
 * （`opts.replyType` 指定展示哪一种，Phase 3/4 出视觉效果无需真后端）；
 * ③ Phase 4 渲染注册表的离线参照（形状严格对齐 docs/前端接口清单.md §3）。
 */

/** kb_answer：知识问答，citations 非空。 */
const kbAnswer: ChatRawResponse = {
  answer:
    '根据《中华人民共和国招标投标法》及其实施条例，招标方式主要分为两类：\n\n' +
    '1. **公开招标**——招标人以招标公告方式邀请不特定的法人或其他组织投标【来源1】；\n' +
    '2. **邀请招标**——招标人以投标邀请书方式邀请三个以上特定的法人或其他组织投标【来源2】。\n\n' +
    '此外，符合法定情形（如涉及国家安全、抢险救灾、采用不可替代的专利或专有技术等）的，' +
    '经项目审批部门批准后可以不进行招标。',
  reply_type: 'kb_answer',
  intent: 'knowledge_qa',
  citations: [
    {
      context_index: 1,
      chunk_id: 12345,
      chunk_uid: 'ck-0a1545f9d2e3',
      doc_name: '中华人民共和国招标投标法',
      chapter: '第二章 招标 > 第十条',
      chunk_index: 12,
      text: '第十条 招标分为公开招标和邀请招标。公开招标，是指招标人以招标公告的方式邀请不特定的法人或者其他组织投标。',
      score: 0.9775,
      metadata: { source_file: '中华人民共和国招标投标法.pdf', publish_date: '2017-12-27' },
    },
    {
      context_index: 2,
      chunk_id: 12346,
      chunk_uid: 'ck-0b2e6a10f4c1',
      doc_name: '招标投标法律解读与风险防范实务（白如银）',
      chapter: '第一章 总则 > 第二节 招标方式',
      chunk_index: 3,
      text: '邀请招标，是指招标人以投标邀请书的方式邀请特定的法人或者其他组织投标。邀请招标的受邀对象不得少于三个。',
      score: 0.9632,
      metadata: { source_file: '招标投标法律解读与风险防范实务 (白如银).pdf' },
    },
  ],
  data: {
    sources: [],
    citation_validation: { verdict: 'pass', total: 2, covered: 2, rules: ['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7'] },
  },
  latency_ms: 2840,
  session_id: '5ce3a9c6abcdef0123456789abcdef0',
  error_code: null,
};

/** price_records：中标记录表（bid_project 口径）。 */
const priceRecords: ChatRawResponse = {
  answer:
    '根据系统收录的招投标数据，杭州海康威视数字技术股份有限公司近三年共中标 3 个相关项目，' +
    '中标金额合计约 1,286 万元。最近的中标记录如下：\n\n' +
    '① 智慧园区安防系统集成项目，中标金额 486.00 万元（2026-06-18）；\n' +
    '② 某市公安视频专网改造项目，中标金额 512.00 万元（2025-11-04）；\n' +
    '③ 省级政务云视频监控扩容项目，中标金额 288.00 万元（2024-09-22）。',
  reply_type: 'price_records',
  intent: 'price_inquiry',
  citations: [],
  data: {
    records: [
      {
        项目名称: '智慧园区安防系统集成项目',
        项目编号: 'ZTB-2026-GD-004517',
        采购人: '广州开发区智慧园区管理委员会',
        中标供应商: '杭州海康威视数字技术股份有限公司',
        中标金额: '4,860,000.00',
        中标日期: '2026-06-18',
        标的物: '视频监控系统、门禁系统、综合安防平台',
        代理机构: '广东省机电设备招标中心有限公司',
        项目阶段: '中标结果公告',
        项目类别: '信息化设备',
        省份: '广东省',
        城市: '广州市',
        发布日期: '2026-06-20',
      },
      {
        项目名称: '某市公安视频专网改造项目',
        项目编号: 'ZTB-2025-ZJ-009842',
        采购人: '杭州市公安局',
        中标供应商: '杭州海康威视数字技术股份有限公司',
        中标金额: '5,120,000.00',
        中标日期: '2025-11-04',
        标的物: '视频专网设备、存储扩容、运维服务',
        代理机构: '浙江省国际技术设备招标有限公司',
        项目阶段: '中标结果公告',
        项目类别: '系统集成',
        省份: '浙江省',
        城市: '杭州市',
        发布日期: '2025-11-06',
      },
      {
        项目名称: '省级政务云视频监控扩容项目',
        项目编号: 'ZTB-2024-GD-003176',
        采购人: '广东省政务服务数据管理局',
        中标供应商: '杭州海康威视数字技术股份有限公司',
        中标金额: '2,880,000.00',
        中标日期: '2024-09-22',
        标的物: '云存储节点、视频接入网关',
        代理机构: '广东省政府采购中心',
        项目阶段: '合同履行',
        项目类别: '信息化设备',
        省份: '广东省',
        城市: '广州市',
        发布日期: '2024-09-24',
      },
    ],
    // 服务端下发的列定义（机器名 + 中文标签，顺序同 OutputTemplate.display_order）
    columns: [
      { key: 'project_name', label: '项目名称' },
      { key: 'project_number', label: '项目编号' },
      { key: 'purchaser', label: '采购人' },
      { key: 'successful_bidder', label: '中标供应商' },
      { key: 'winning_amount', label: '中标金额' },
      { key: 'winning_date', label: '中标日期' },
      { key: 'subject_matter', label: '标的物' },
      { key: 'agent', label: '代理机构' },
      { key: 'project_stage', label: '项目阶段' },
      { key: 'project_category', label: '项目类别' },
      { key: 'province', label: '省份' },
      { key: 'city', label: '城市' },
      { key: 'publish_date', label: '发布日期' },
    ],
    tables: ['ztb_clean.bid_project'],
    total_found: 3,
    intent: {
      sub_route: 'bidding_query',
      query_type: 'bidding_query',
      semantic_keywords: ['海康威视', '中标'],
      exact_tokens: ['杭州海康威视数字技术股份有限公司'],
    },
    meta: {
      total_hits: 3,
      displayed_hits: 3,
      sql_count: 2,
      total_sql_time: 0.084,
      node_elapsed: 0.412,
    },
  },
  latency_ms: 3120,
  session_id: '5ce3a9c6abcdef0123456789abcdef0',
  error_code: null,
};

/** doc_qa：长文档检索模式，sources 有值 + retrieval.notes 显式提示。 */
const docQa: ChatRawResponse = {
  answer:
    '本项目采用**综合评分法**评标。评标委员会由七名成员组成，其中技术、经济等方面专家不少于成员总数的三分之二【依据：第四章 评标办法】。\n\n' +
    '投标保证金金额为人民币 **80 万元**，应在投标截止时间前以电汇或银行保函形式提交【依据：第三章 投标保证金】。',
  reply_type: 'doc_qa',
  intent: 'doc_qa',
  citations: [],
  data: {
    status: 'success',
    mode: 'retrieval',
    sources: [
      {
        doc: '智慧园区智慧化改造项目招标文件.pdf.md',
        chapter: '第三章 投标人须知 > 投标保证金',
        chunk_index: 4,
        content_snippet: '本项目投标保证金金额为人民币捌拾万元整（¥800,000.00），投标人应在投标截止时间前……',
        score: 0.647,
      },
      {
        doc: '智慧园区智慧化改造项目招标文件.pdf.md',
        chapter: '第四章 评标办法 > 评标委员会',
        chunk_index: 9,
        content_snippet: '评标委员会由七名成员组成，其中技术、经济等方面的专家不少于成员总数的三分之二……',
        score: 0.612,
      },
    ],
    attached_docs: ['智慧园区智慧化改造项目招标文件.pdf'],
    retrieval: {
      mode: 'retrieval',
      blocks: 8,
      truncated: false,
      notes: [],
    },
  },
  latency_ms: 2100,
  session_id: '5ce3a9c6abcdef0123456789abcdef0',
  error_code: null,
};

/** deep_agent：带文件 + 跨源问题，含多步检索轨迹（唯一有真 trace 的分支）。 */
const deepAgent: ChatRawResponse = {
  answer:
    '经核对，该招标文件第三章约定的投标保证金**高于**《招标投标法实施条例》规定的上限。\n\n' +
    '《招标投标法实施条例》第二十六条规定，投标保证金一般不得超过招标项目估算价的 **2%**。本项目估算价 3,200 万元，' +
    '对应上限为 64 万元，而招标文件要求 80 万元，超出上限 16 万元，建议要求招标人修正后再投标。',
  reply_type: 'deep_agent',
  intent: 'deep_agent',
  citations: [],
  data: {
    trace: {
      mode: 'plan_and_solve_reflect',
      rounds: 1,
      plan: [
        { id: 1, objective: '读取招标文件中的保证金条款', tool: 'read_file', query: '投标保证金 金额', depends_on: [] },
        { id: 2, objective: '查法规对投标保证金的上限规定', tool: 'kb_search_tool', query: '投标保证金 不得超过 估算价 比例', depends_on: [] },
      ],
      steps: [
        {
          step_id: 1,
          tool: 'read_file',
          query: '投标保证金 金额',
          status: 'ok',
          observation_head: '本项目投标保证金金额为人民币捌拾万元整（¥800,000.00），投标人应在投标截止时间前以电汇或银行保函形式提交……',
          records_count: 3,
          latency_ms: 657,
          sources: ['智慧园区智慧化改造项目招标文件.pdf｜第三章 投标人须知 > 投标保证金'],
        },
        {
          step_id: 2,
          tool: 'kb_search_tool',
          query: '投标保证金 不得超过 估算价 比例',
          status: 'ok',
          observation_head: '招标投标法实施条例 第二十六条 招标人在招标文件中要求投标人提交投标保证金的，投标保证金不得超过招标项目估算价的 2%……',
          records_count: 5,
          latency_ms: 812,
          sources: ['中华人民共和国招标投标法实施条例｜第二章 招标 > 第二十六条'],
        },
      ],
      reflection: {
        verdict: 'adequate',
        notes: '两步检索均已获得可核验依据（文件条款 + 法规条文），结论可直接引用。',
        revised_answer: null,
      },
    },
    draft_answer: '该项目投标保证金为 80 万元。',
  },
  latency_ms: 9400,
  session_id: '5ce3a9c6abcdef0123456789abcdef0',
  error_code: null,
};

/** chat：一般闲聊，无 data。 */
const chat: ChatRawResponse = {
  answer: '你好！我是招小智，招投标领域的智能助手。你可以问我法律法规、企业信息、中标记录，也可以上传招标文件让我帮你解读。',
  reply_type: 'chat',
  intent: 'general_chat',
  citations: [],
  data: null,
  latency_ms: 860,
  session_id: '5ce3a9c6abcdef0123456789abcdef0',
  error_code: null,
};

/** safety：安全边界拒答/引导。 */
const safety: ChatRawResponse = {
  answer:
    '抱歉，我无法协助编造投标材料或规避招标程序。如果你在真实项目中遇到合规疑问，' +
    '我可以帮你查阅相关法律法规，或分析招标文件中的条款风险。',
  reply_type: 'safety',
  intent: 'safety',
  citations: [],
  data: { status: { category: 'boundary', reason: 'compliance' } },
  latency_ms: 720,
  session_id: '5ce3a9c6abcdef0123456789abcdef0',
  error_code: null,
};

/** fallback：路由/链路失败时的降级文案。 */
const fallback: ChatRawResponse = {
  answer: '抱歉，这次没能准确理解你的问题。你可以换个说法，或补充更具体的信息（如公司名称、项目编号、文件名），我再试一次。',
  reply_type: 'fallback',
  intent: 'fallback',
  citations: [],
  data: null,
  latency_ms: 540,
  session_id: '5ce3a9c6abcdef0123456789abcdef0',
  error_code: null,
};

/** 按 reply_type 索引的 fixture 表。 */
export const mockChatFixtures: Record<ReplyType, ChatRawResponse> = {
  kb_answer: kbAnswer,
  price_records: priceRecords,
  doc_qa: docQa,
  deep_agent: deepAgent,
  chat,
  safety,
  fallback,
};

/** 与 kb_answer 相关的示例问题（浮窗引导语用）。 */
export const mockChatSuggestions: string[] = [
  '招标方式有哪些？',
  '杭州海康威视最近中标了哪些项目？',
  '投标保证金的上限是多少？',
];
