# 招小智 · 前端（frontend/）

招投标智能助手前端 —— 独立包，前后端分离，仅经 HTTP/SSE 与后端通信。

## 技术栈

Vite 6 · React 19 · TypeScript 5 · Tailwind CSS v4 · Shadcn UI · Framer Motion · Recharts · react-router · Zustand · react-markdown · @microsoft/fetch-event-source

视觉：**Hallmark Cobalt**（modern-minimal 流派）—— 冷工程纸面 + 电钴蓝单一信号 + Space Grotesk/Inter/JetBrains Mono 三字体；容器大圆角（用户偏好）；token 见 `src/styles/index.css`。

## 命令

```bash
# 启动开发服务器（Vite 代理 /api → http://127.0.0.1:8000）
npm run dev

# 类型检查
npm run typecheck

# 测试（Vitest）
npm test

# 生产构建
npm run build
npm run preview
```

> 生产构建变量在 [`.env.production`](.env.production)（**已入库**）：`VITE_CHAT_MODE=api`。
> 不要依赖 `.env.local`（被 gitignore）——服务器上拿不到，构建出来对话栏会是演示数据。
> `VITE_API_BASE` 保持不设（走同域 `/api`）。细则见 `docs/部署手册_百度云轻量.md` §4。

> 本机 Node 在 `D:\Zhao327\`（不在 PATH），可用 `$env:Path="D:\Zhao327;"+$env:Path; npm ...` 调用。

## 目录

```
src/
├── App.tsx                  # 路由骨架（/chat /inquiry /kanban /supplier）
├── styles/index.css         # Hallmark Cobalt 视觉 Token（Tailwind v4 @theme）
├── components/
│   ├── ui/                  # Shadcn：Button, Badge（Phase 3 扩 Dialog/Drawer/Table…）
│   ├── common/              # Brand, EmptyState（Phase 3 扩 GlassCard/MetricCard…）
│   └── layout/              # LandingLayout(上/中/下), ModulePageLayout
├── pages/                   # chat/kanban/inquiry/supplier（Phase 3 前为占位）
├── types/ · mock/ · adapters/ · services/ · store/   # Phase 2~4 按方案落地
└── test/                    # Vitest setup + 冒烟
```

## 契约约定

- **页面组件只 import `services/api.ts` 与 `types/`**，禁止直接碰后端字段 —— 适配层是未来换数据的唯一接缝（见 `docs/plan1/前端架构与UI版面设计方案_20260914.md`）。
- 后端权威契约：`docs/前端接口清单.md`；开发期代理 `/api → 127.0.0.1:8000`（rewrite 去 `/api` 前缀）。
