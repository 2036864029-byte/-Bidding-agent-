import { create } from 'zustand';

/**
 * chat-window —— 落地页对话浮窗状态机（D6）。
 *
 * status:
 *   idle      无会话，dock 输入条常驻
 *   open      聚焦浮窗打开，输入在浮窗内
 *   minimized ×/ESC/遮罩收起，dock 输入条回归 + 「对话中」胶囊
 *
 * 会话纪律：× = 收起，非销毁。messages 随收起保留，胶囊点击恢复同一会话。
 * Phase 4 接真后端：submit() 的「占位回复」替换为 SSE 流式 + reply_type 渲染，
 * 状态机与消息列表形状不变（设计文档 §4.2 的接缝即在此）。
 */

export interface ChatMessage {
  id: string;
  role: 'user' | 'agent';
  content: string;
}

type PanelStatus = 'idle' | 'open' | 'minimized';

let seq = 0;
const uid = () => `m_${++seq}_${Date.now()}`;

/** Phase 1 占位回复：真实链路在 Phase 4（/chat + SSE）接入 */
const PLACEHOLDER_REPLY =
  '已收到。真实对话链路将在下一阶段接入（/chat + SSE 流式），届时此处展示完整回答与引用来源。';

interface ChatWindowState {
  status: PanelStatus;
  messages: ChatMessage[];
  input: string;
  setInput: (v: string) => void;
  /** 发送：追加用户消息 + 占位回复，浮窗打开，清空输入 */
  submit: () => void;
  open: () => void;
  minimize: () => void;
}

export const useChatWindowStore = create<ChatWindowState>((set, get) => ({
  status: 'idle',
  messages: [],
  input: '',
  setInput: (v) => set({ input: v }),
  submit: () => {
    const { input, messages } = get();
    const q = input.trim();
    if (!q) return;
    set({
      input: '',
      status: 'open',
      messages: [
        ...messages,
        { id: uid(), role: 'user', content: q },
        { id: uid(), role: 'agent', content: PLACEHOLDER_REPLY },
      ],
    });
  },
  open: () => set({ status: 'open' }),
  minimize: () => set({ status: 'minimized' }),
}));
