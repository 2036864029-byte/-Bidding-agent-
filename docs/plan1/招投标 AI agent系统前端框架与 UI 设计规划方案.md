# 招投标 AI agent系统前端框架与 UI 设计规划方案

**（Hallmark 视觉规范 + Mock-First 适配器架构）**

## 1. 架构理念与设计规范

### 1.1 核心设计理念：UI 与底层数据彻底解耦

针对智能询价、供应商管理、商机看板三个模块数据表尚未入库、字段未来可能调整的现状，架构上采用 **“Domain Model（领域模型） + Adapter（适配层）”** 模式：

- **UI 视图层**：完全依赖前端定义的干净领域接口（Domain Interface），不直接绑定后端原始表字段。
- **数据适配层**：统一收口在 `src/adapters/` 目录。当前负责转换 Mock 数据，未来后端 API 上线后，只需更换适配层转换逻辑，**UI 组件层实现 少量或0 行代码修改**。

### 1.2 视觉与微交互规范（`nutlope/hallmark` 技能标准）

系统全面遵循Claude code中的/ `hallmark` 技能审美指导原则，摒弃传统政企软件的沉闷感：

- **色彩体系**：
  - **底色**：自行根据整个项目确定，不过过于沉闷
  - **容器**：自行确定
  - **强调色**：自行确定
- **圆角与间距**：大圆角（`rounded-2xl` / `rounded-3xl`），开阔的 padding 留白，强化流线型视觉。
- **微交互**：基于 Framer Motion 实现卡片悬浮升起、 Drawer 抽屉平滑滑出、图表实时动量过渡。

## 2. 系统前端目录架构

Plaintext

```
src/
├── assets/                    # 静态资源 (Logo, SVG, 图标)
├── components/                # 通用 UI 组件 (基于 Shadcn UI 封装)
│   ├── ui/                    # Button, Dialog, Drawer, Badge, Table 等
│   ├── common/                # GlassCard, MetricCard, EmptyState, Header
│   └── layout/                # MainLayout, Sidebar, Navbar
├── pages/                     # 核心页面/大视图
│   ├── chat/                  # AI 对话交互中心 (SSE 流式输出)
│   ├── kanban/                # 招标商机看板 (Recharts 图表 + 状态卡片)
│   ├── inquiry/               # 智能询价与比价 (多方横向对比 + 调价抽屉)
│   └── supplier/              # 供应商全景管理 (标签筛选 + 能力仪表盘)
├── types/                     # 前端领域数据契约 (Domain Interfaces)
│   ├── chat.ts                # 对话与 Agent 状态类型
│   ├── kanban.ts              # 商机与管道数据类型
│   ├── inquiry.ts             # 询价单与对比明细类型
│   └── supplier.ts            # 供应商画像与评分类型
├── mock/                      # 业务场景 Mock 数据源
│   ├── mockKanban.ts
│   ├── mockInquiry.ts
│   └── mockSupplier.ts
├── adapters/                  # 数据适配转换层 (核心解耦层)
│   ├── kanbanAdapter.ts       # 原始数据 -> Kanban Domain Model
│   ├── inquiryAdapter.ts      # 原始数据 -> Inquiry Domain Model
│   └── supplierAdapter.ts     # 原始数据 -> Supplier Domain Model
├── services/                  # 数据请求统一接口层 (Mock <-> API 切换控制)
│   └── api.ts
└── lib/                       # 工具函数 (Tailwind clsx/twMerge 整合)
```

## 3. 核心业务模块 UI/UX 设计

整个系统采用 **全屏大屏工作台 + 响应式双栏布局**：左侧为轻量可折叠导航，中央为多维业务看板与对话控制台。

### 模块 A：AI 智能对话控制台（RAG + Agent 协同中心）

- **视觉呈现**：悬浮式双气泡对话框、打字机流式打字效果、思维链（Chain-of-Thought）下拉折叠面板。
- **协同联动**：当 Agent 回复包含结构化分析结果（如：“已提取出 3 家符合条件的供应商”）时，下方动态渲染**交互式操作卡片**，一键联动开启 Drawer 抽屉或跳转到业务模块。

### 模块 B：商机看板 (Opportunity Kanban)

- **版面布局**：
  - **顶部漏斗指标栏**：可以筛选地区、行业、产品
  - **中央可视化区域**：1.固定的各地区热 力图 2.各行业招投标项目的总体占比图                   3.自行筛选的某个行业/产品的按时间线的走势统计图
  - **要求**：点击主页面的商机看板模块，是否应该自动跳转一个新的页面，不然统计图比较多展示不开
- **容错机制**：无数据或空状态下自动展示带有 Hallmark 风格光晕的渐变空态插图，支持一键注入演示 Mock 数据。

### 模块 C：智能询价 (Smart Pricing & Inquiry)

- **版面布局**：

  - **主对话框**：用户点金这个模块之后，出现一个对话框，用户基于语言/文件/图片去描述当前项目的需求概况，之后触动agent去掉用双库查询类似项目的历史中标金额情况，给出价格参考

  - **偏离度高亮**：对超出预算阈值的单项做荧光红/橙色 Badge 警戒标识。

    

### 模块 D：供应商全景管理 (Supplier Intelligence)

- **版面布局**：
  - **多维多选筛选器**：顶部支持按资质、历史合作评分、主营类别、地区做微型 Tag 组无刷新筛选。
  - **网格化供应商卡片 (Grid Card)**：展现供应商核心履约能力、雷达图评分、风险预警标签。
  - **详情弹窗 (Modal)**：查看供应商过往参与投标的成功率、中标历史价格分布。

## 4. 前端领域数据契约与适配层设计 (代码级规范)

这里在 `src/types/` 中定义领域模型（以智能询价为例），并在 `src/adapters/` 中通过适配器函数隔离未来真实的 API：

### 4.1 领域契约定义 (`src/types/inquiry.ts`)

TypeScript

```
// 前端 UI 独立消费的数据契约
export interface InquiryItem {
  itemId: string;
  itemName: string;
  specifications: string; // 规格型号
  unit: string;
  targetQuantity: number;
  benchmarkUnitPrice: number; // 内部测算基准单价
  quotes: {
    supplierId: string;
    supplierName: string;
    unitPrice: number;
    totalPrice: number;
    isRecommended: boolean;
    deviationRate: number; // 偏离度 %
  }[];
}

export interface InquirySummary {
  inquiryId: string;
  title: string;
  status: 'DRAFT' | 'IN_PROGRESS' | 'COMPLETED';
  totalBudget: number;
  createdAt: string;
  items: InquiryItem[];
}
```

### 4.2 数据适配器实现 (`src/adapters/inquiryAdapter.ts`)

TypeScript

```
import { InquirySummary } from '@/types/inquiry';
import { mockRawInquiryData } from '@/mock/mockInquiry';

/**
 * 适配器函数：将原始数据（当前为 Mock，未来为后端未定的 API Payload）
 * 转化为前端 UI 统一消费的 Domain Model
 */
export function getFormattedInquiryData(rawResponse?: any): InquirySummary {
  const data = rawResponse || mockRawInquiryData;

  // 即使未来后端字段叫 base_price / quote_list，也只需在此处做逻辑映射
  return {
    inquiryId: data.id || data.inquiryId,
    title: data.title || '智能询价单',
    status: data.status || 'IN_PROGRESS',
    totalBudget: Number(data.budget || data.totalBudget || 0),
    createdAt: data.created_at || data.createdAt,
    items: (data.items || []).map((item: any) => ({
      itemId: item.item_id || item.itemId,
      itemName: item.name || item.itemName,
      specifications: item.spec || item.specifications || '-',
      unit: item.unit || '件',
      targetQuantity: Number(item.qty || item.targetQuantity || 0),
      benchmarkUnitPrice: Number(item.base_price || item.benchmarkUnitPrice || 0),
      quotes: (item.supplier_quotes || item.quotes || []).map((q: any) => ({
        supplierId: q.supplier_id || q.supplierId,
        supplierName: q.supplier_name || q.supplierName,
        unitPrice: Number(q.price || q.unitPrice || 0),
        totalPrice: Number(q.price || q.unitPrice || 0) * Number(item.qty || item.targetQuantity || 1),
        isRecommended: Boolean(q.is_best || q.isRecommended),
        deviationRate: Number(q.deviation || q.deviationRate || 0),
      })),
    })),
  };
}
```

## 5. 分阶段实施路线图 (Phase 1 ~ Phase 4)

| **阶段**    | **核心任务目标**                       | **关键产出物**                                               | **预估工时** |
| ----------- | -------------------------------------- | ------------------------------------------------------------ | ------------ |
| **Phase 1** | **基座搭建与 Hallmark 视觉Token注入**  | 初始化 Vite + Tailwind，安装 Shadcn UI，引入 `hallmark` 配置到 Tailwind 主题文件。完成全局 Layout。 | 1 天         |
| **Phase 2** | **Mock 数据与 Adapter 契约构造**       | 编写 `types/` 契约文件，在 `mock/` 中注入高保真的招投标商业数据，编写 `adapters/` 转换层。 | 1 天         |
| **Phase 3** | **三大业务模块高质感 UI 制作**         | 构建商机看板（Recharts 图表）、智能询价（对比表格+Drawer 抽屉）、供应商管理（网格卡片）。 | 2 天         |
| **Phase 4** | **Agent 对话控制台与业务模块协同联动** | 实现流式对话框，完成点击 Agent 分析建议直接弹出右侧业务 Drawer 抽屉的联动微交互。 | 1 天         |