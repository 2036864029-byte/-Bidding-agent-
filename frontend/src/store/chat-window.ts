import { create } from 'zustand';
import {
  clearSessionId,
  feishuPrompted,
  loadSessionId,
  markFeishuPrompted,
  saveSessionId,
  validateUpload,
} from '@/lib/chat';
import { api, type UploadedFileState } from '@/services/api';
import type { ChatMessage } from '@/types/chat';

export type { ChatMessage } from '@/types/chat';
export type { UploadedFileState } from '@/services/api';

/**
 * chat-window —— 对话控制台状态机（Phase 4 接真后端 / Phase 5 边界打磨）。
 *
 * status:
 *   idle      无会话，dock 输入条常驻
 *   open      聚焦浮窗打开，输入在浮窗内
 *   minimized ×/ESC/遮罩收起，dock 输入条回归 + 「对话中」胶囊
 *
 * 会话纪律：× = 收起，非销毁。messages 随收起保留，胶囊点击恢复同一会话。
 *
 * Phase 4：`submit()` 走 `api.chat.stream`，逐 token 追加、以 `result` 终态**覆盖**；
 *   会话 id 存 localStorage；上传串行 + 轮询。
 * Phase 5：429/503 自动退避（`onRetry` 回吐提示）；失败的消息**可重试**；停止不算失败。
 */

type PanelStatus = 'idle' | 'open' | 'minimized';

let seq = 0;
const uid = () => `m_${++seq}_${Date.now()}`;

/** 当前流的 abort 句柄（模块级：不需要触发渲染）。 */
let currentAbort: AbortController | null = null;

/**
 * 顶层导航。真实浏览器 = 正常跳转；jsdom / 受限环境（location.assign 只读或
 * not-implemented）会抛 —— 这里吞掉，避免授权跳转失败变成 unhandled rejection。
 */
export const assignHref = (url: string): void => {
  try {
    window.location.assign(url);
  } catch {
    /* 非浏览器环境：跳转本就不该发生 */
  }
};

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
  /** 即时提示（上传校验失败 / 正在退避重试等） */
  notice: string | null;
  /** 飞书连接提醒：null=不弹；'unbound'=情况1（引导连接）；'bound'=情况2（已连接） */
  feishuPrompt: 'unbound' | 'bound' | null;

  setInput: (v: string) => void;
  /** 发送：追加用户消息 + 占位 agent 消息，开流式 */
  submit: () => void;
  /** 直接以指定文本发起一轮提问（澄清候选 chips 点击用；不经输入框） */
  submitText: (question: string) => void;
  /** 重试某条失败的 agent 消息（重发它前面那条用户消息） */
  retry: (messageId: string) => void;
  /** 停止生成：中断流，保留已收到的内容 */
  stop: () => void;
  open: () => void;
  minimize: () => void;
  uploadFiles: (files: File[]) => void;
  removeFile: (id: string) => void;
  /** 上传后检查飞书绑定：未弹过 → 查询 → 决定弹哪种提醒（异步，不阻塞上传） */
  checkFeishuPrompt: () => void;
  /** 点「连接飞书」：拿授权链 → 顶层跳转（授权后 302 回当前页 + ?feishu=bound） */
  connectFeishu: () => void;
  /** 关闭飞书提醒（并记下本会话已弹，避免反复打扰） */
  dismissFeishuPrompt: () => void;
  /** 设一条即时提示（授权回调落地页「绑定成功」用） */
  notify: (message: string) => void;
  /** 清空会话（消息 + 会话 id + 文件） */
  reset: () => void;
}

export const useChatWindowStore = create<ChatWindowState>((set, get) => {
  /** 发起一次流式问答（submit 与 retry 共用）。 */
  const startStream = (question: string) => {
    const { messages, files } = get();
    // 只把「解析完成」的文件带给后端；解析中的带了也没用
    const fileIds = files
      .filter((f) => f.status === 'done' && f.fileId)
      .map((f) => f.fileId as string);
    const agentId = uid();

    set({
      status: 'open',
      busy: true,
      notice: null,
      messages: [
        ...messages,
        { id: uid(), role: 'user', content: question },
        { id: agentId, role: 'agent', content: '', pending: true, sourceQuestion: question },
      ],
    });

    const ctrl = new AbortController();
    currentAbort = ctrl;

    api.chat
      .stream(
        question,
        {
          sessionId: get().sessionId ?? undefined,
          fileIds,
          signal: ctrl.signal,
          // 429/503 退避时给用户一句交代，别让界面无声地卡着
          onRetry: (attempt) =>
            set({ notice: `当前请求较多，正在重试（第 ${attempt} 次）…` }),
        },
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
              notice: null,
              messages: patch(s.messages, agentId, (m) => ({
                ...m,
                content: res.answer || m.content,
                pending: false,
                failed: false,
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
          notice: null,
          messages: patch(s.messages, agentId, (m) => {
            // 已收到部分内容：保留，不覆盖成报错（也不标失败 —— 用户主动停的就是这种）
            if (m.content) return { ...m, pending: false };
            return {
              ...m,
              pending: false,
              failed: !aborted,
              content: aborted
                ? '（已停止）'
                : `抱歉，这次没能取到回答：${(err as Error).message}`,
            };
          }),
        }));
      })
      .finally(() => {
        currentAbort = null;
        set({ busy: false });
      });
  };

  return {
    status: 'idle',
    messages: [],
    input: '',
    sessionId: loadSessionId(),
    files: [],
    busy: false,
    notice: null,
    feishuPrompt: null,

    setInput: (v) => set({ input: v }),

    submit: () => {
      const { input, busy } = get();
      const q = input.trim();
      if (!q || busy) return;
      set({ input: '' });
      startStream(q);
    },

    submitText: (question) => {
      const q = question.trim();
      if (!q || get().busy) return;
      startStream(q);
    },

    retry: (messageId) => {
      const { messages, busy } = get();
      if (busy) return;
      const idx = messages.findIndex((m) => m.id === messageId);
      const userMsg = idx > 0 ? messages[idx - 1] : undefined;
      if (!userMsg || userMsg.role !== 'user') return;
      // 丢掉失败的那条，连同它前面的用户消息一起重发（否则会看到重复的提问）
      set({ messages: messages.slice(0, idx - 1).concat(messages.slice(idx + 1)) });
      startStream(userMsg.content);
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
      // 并行线 A：大文件进异步解析，弹「连接飞书」提醒（情况1/情况2，同会话只弹一次）
      get().checkFeishuPrompt();

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

    // ── 飞书连接提醒（并行线 A） ─────────────────────────────
    checkFeishuPrompt: () => {
      const { sessionId, feishuPrompt } = get();
      if (feishuPrompt || !sessionId) return;
      if (feishuPrompted(sessionId)) return; // 本会话已弹过
      void api.feishu
        .binding(sessionId)
        .then((bound) => {
          if (!get().sessionId) return; // 期间会话被清空 → 不弹
          set({ feishuPrompt: bound ? 'bound' : 'unbound' });
        })
        .catch(() => {
          /* 后端未配飞书 / 不可用：静默，不打扰用户 */
        });
    },

    connectFeishu: () => {
      const { sessionId } = get();
      if (!sessionId) return;
      markFeishuPrompted(sessionId); // 跳转前记下，避免授权页往返后重复弹
      set({ feishuPrompt: null });
      void api.feishu
        .authUrl(sessionId, window.location.href)
        .then((url) => {
          assignHref(url); // 顶层导航；授权后 302 回当前页 + ?feishu=bound
        })
        .catch(() => {
          set({ notice: '连接飞书暂时不可用，请稍后再试。' });
        });
    },

    dismissFeishuPrompt: () => {
      const { sessionId } = get();
      if (sessionId) markFeishuPrompted(sessionId);
      set({ feishuPrompt: null });
    },

    notify: (message) => set({ notice: message }),

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
        feishuPrompt: null,
        status: 'idle',
      });
    },
  };
});
