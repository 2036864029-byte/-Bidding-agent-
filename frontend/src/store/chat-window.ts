import { create } from 'zustand';
import { clearSessionId, loadSessionId, saveSessionId, validateUpload } from '@/lib/chat';
import { api, type UploadedFileState } from '@/services/api';
import type { ChatMessage } from '@/types/chat';

export type { ChatMessage } from '@/types/chat';
export type { UploadedFileState } from '@/services/api';

/**
 * chat-window —— 对话控制台状态机（Phase 4 接真后端）。
 *
 * status:
 *   idle      无会话，dock 输入条常驻
 *   open      聚焦浮窗打开，输入在浮窗内
 *   minimized ×/ESC/遮罩收起，dock 输入条回归 + 「对话中」胶囊
 *
 * 会话纪律：× = 收起，非销毁。messages 随收起保留，胶囊点击恢复同一会话。
 *
 * Phase 4 落地：`submit()` 走 `api.chat.stream`（api 态 = 真 SSE；mock 态 = 模拟流式），
 * 逐 token 追加、以 `result` 终态**覆盖**（权威）；会话 id 存 localStorage 供刷新恢复；
 * 上传走异步端点 + 轮询，文件 chips 随状态更新。**停止** = abort 流，保留已收到的部分。
 */

type PanelStatus = 'idle' | 'open' | 'minimized';

let seq = 0;
const uid = () => `m_${++seq}_${Date.now()}`;

/** 当前流的 abort 句柄（模块级：不需要触发渲染）。 */
let currentAbort: AbortController | null = null;

const patch = (
  messages: ChatMessage[],
  id: string,
  fn: (m: ChatMessage) => ChatMessage
): ChatMessage[] => messages.map((m) => (m.id === id ? fn(m) : m));

interface ChatWindowState {
  status: PanelStatus;
  messages: ChatMessage[];
  input: string;
  /** 服务端会话 id（刷新后从 localStorage 恢复，保证多轮上下文不断） */
  sessionId: string | null;
  /** 已上传文件（chips） */
  files: UploadedFileState[];
  /** 正在生成回答 */
  busy: boolean;
  /** 即时提示（上传校验失败等） */
  notice: string | null;

  setInput: (v: string) => void;
  /** 发送：追加用户消息 + 占位 agent 消息，开流式 */
  submit: () => void;
  /** 停止生成：中断流，保留已收到的内容 */
  stop: () => void;
  open: () => void;
  minimize: () => void;
  uploadFiles: (files: File[]) => void;
  removeFile: (id: string) => void;
  /** 清空会话（消息 + 会话 id + 文件） */
  reset: () => void;
}

export const useChatWindowStore = create<ChatWindowState>((set, get) => ({
  status: 'idle',
  messages: [],
  input: '',
  sessionId: loadSessionId(),
  files: [],
  busy: false,
  notice: null,

  setInput: (v) => set({ input: v }),

  submit: () => {
    const { input, messages, files, busy } = get();
    const q = input.trim();
    if (!q || busy) return;

    // 只把「解析完成」的文件带给后端；解析中的带了也没用
    const fileIds = files.filter((f) => f.status === 'done' && f.fileId).map((f) => f.fileId as string);
    const agentId = uid();

    set({
      input: '',
      status: 'open',
      busy: true,
      notice: null,
      messages: [
        ...messages,
        { id: uid(), role: 'user', content: q },
        { id: agentId, role: 'agent', content: '', pending: true },
      ],
    });

    const ctrl = new AbortController();
    currentAbort = ctrl;

    api.chat
      .stream(
        q,
        { sessionId: get().sessionId ?? undefined, fileIds, signal: ctrl.signal },
        {
          onSession: (sid) => {
            if (!sid) return;
            saveSessionId(sid);
            set({ sessionId: sid });
          },
          onToken: (delta) =>
            set((s) => ({
              messages: patch(s.messages, agentId, (m) => ({ ...m, content: m.content + delta })),
            })),
          // result 是终态权威结果：正文覆盖累积的 token，并挂上类型/引用/data
          onResult: (res) =>
            set((s) => ({
              messages: patch(s.messages, agentId, (m) => ({
                ...m,
                content: res.answer || m.content,
                pending: false,
                replyType: res.replyType,
                intent: res.intent,
                citations: res.citations,
                data: res.data,
                errorCode: res.errorCode,
              })),
            })),
        }
      )
      .catch((err: unknown) => {
        const aborted = (err as Error)?.name === 'AbortError';
        set((s) => ({
          messages: patch(s.messages, agentId, (m) => ({
            ...m,
            pending: false,
            content: m.content
              ? m.content // 已收到部分内容：保留，不覆盖成报错
              : aborted
                ? '（已停止）'
                : `抱歉，这次没能取到回答：${(err as Error).message}`,
          })),
        }));
      })
      .finally(() => {
        currentAbort = null;
        set({ busy: false });
      });
  },

  stop: () => {
    currentAbort?.abort();
    currentAbort = null;
    set({ busy: false });
  },

  open: () => set({ status: 'open' }),
  minimize: () => set({ status: 'minimized' }),

  uploadFiles: (files) => {
    const accepted = Array.from(files).filter((f) => {
      const err = validateUpload(f);
      if (err) {
        set({ notice: err });
        return false;
      }
      return true;
    });
    if (!accepted.length) return;

    // 串行上传：同一客户端快速连传会撞限流（429），串行最省事也最稳
    void (async () => {
      for (const file of accepted) {
        const key = `f_${file.name}_${file.size}_${Date.now()}`;
        set((s) => ({
          notice: null,
          status: 'open',
          files: [...s.files, { id: key, name: file.name, status: 'pending' }],
        }));
        try {
          await api.chat.upload(file, key, get().sessionId, (u) => {
            // 采纳上传回带的会话：先传文件后提问时，是这次上传开了会话；
            // 不采纳的话，后续提问会落到另一个会话，文件归属对不上。
            if (u.sessionId && !get().sessionId) {
              saveSessionId(u.sessionId);
              set({ sessionId: u.sessionId });
            }
            set((s) => ({ files: s.files.map((x) => (x.id === u.id ? u : x)) }));
          });
        } catch (err) {
          set((s) => ({
            files: s.files.map((x) =>
              x.id === key ? { ...x, status: 'error' as const, error: (err as Error).message } : x
            ),
          }));
        }
      }
    })();
  },

  removeFile: (id) => set((s) => ({ files: s.files.filter((f) => f.id !== id) })),

  reset: () => {
    currentAbort?.abort();
    currentAbort = null;
    clearSessionId();
    set({
      messages: [],
      sessionId: null,
      files: [],
      busy: false,
      input: '',
      notice: null,
      status: 'idle',
    });
  },
}));
