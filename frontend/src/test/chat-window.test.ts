import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearSessionId, loadSessionId } from '@/lib/chat';
import { useChatWindowStore } from '@/store/chat-window';

/* chat-window 状态机（Phase 4）：
 * 发送开浮窗 / 空白不发送 / 收起不销毁会话 / 流式完成后挂 replyType 与引用 /
 * 停止保留已收内容 / 会话 id 持久化。 */

/** 完整复位：busy/files/sessionId 也要清，否则上一条测试的在飞流会挡住下一次 submit */
function resetStore() {
  clearSessionId();
  useChatWindowStore.setState({
    status: 'idle',
    messages: [],
    input: '',
    files: [],
    busy: false,
    sessionId: null,
    notice: null,
  });
}

const agentMessage = () => useChatWindowStore.getState().messages[1];

describe('chat-window 状态机', () => {
  beforeEach(resetStore);

  it('submit 打开浮窗，追加用户消息 + 待生成的 agent 消息，清空输入', () => {
    const s = useChatWindowStore.getState();
    s.setInput('  招标方式有哪些？  ');
    s.submit();

    const after = useChatWindowStore.getState();
    expect(after.status).toBe('open');
    expect(after.messages).toHaveLength(2);
    expect(after.messages[0]).toMatchObject({ role: 'user', content: '招标方式有哪些？' });
    expect(after.messages[1].role).toBe('agent');
    expect(after.messages[1].pending).toBe(true);
    expect(after.input).toBe('');
    expect(after.busy).toBe(true);
  });

  it('空白输入不发送', () => {
    useChatWindowStore.getState().submit();
    const after = useChatWindowStore.getState();
    expect(after.status).toBe('idle');
    expect(after.messages).toHaveLength(0);
  });

  it('已生成中时重复 submit 被忽略（不并发两条流）', () => {
    const s = useChatWindowStore.getState();
    s.setInput('第一个问题');
    s.submit();
    useChatWindowStore.getState().setInput('第二个问题');
    useChatWindowStore.getState().submit();
    expect(useChatWindowStore.getState().messages).toHaveLength(2);
  });

  it('流式结束后挂上 replyType / 引用，pending 落回 false', async () => {
    const s = useChatWindowStore.getState();
    s.setInput('招标方式有哪些？'); // mock 关键词路由 → kb_answer
    s.submit();

    await vi.waitFor(() => {
      expect(agentMessage().pending).toBe(false);
    });

    const m = agentMessage();
    expect(m.replyType).toBe('kb_answer');
    expect(m.data?.kind).toBe('kb_answer');
    expect(m.citations?.length).toBeGreaterThan(0);
    expect(m.content).toContain('公开招标'); // 终态 answer 覆盖累积 token
    expect(useChatWindowStore.getState().busy).toBe(false);
  });

  it('会话 id 写入 localStorage（刷新后能恢复同一会话）', async () => {
    useChatWindowStore.getState().setInput('招标方式有哪些？');
    useChatWindowStore.getState().submit();

    await vi.waitFor(() => {
      expect(useChatWindowStore.getState().sessionId).toBeTruthy();
    });
    expect(loadSessionId()).toBe(useChatWindowStore.getState().sessionId);
  });

  it('stop 中断生成，保留已收到的部分并落回非生成态', async () => {
    useChatWindowStore.getState().setInput('招标方式有哪些？');
    useChatWindowStore.getState().submit();
    useChatWindowStore.getState().stop();

    await vi.waitFor(() => {
      expect(agentMessage().pending).toBe(false);
    });
    expect(useChatWindowStore.getState().busy).toBe(false);
    expect(agentMessage().content).toBeTruthy(); // 中止也要给个交代，不能是空白
  });

  it('minimize 收起保留消息，open 恢复同一会话', async () => {
    const s = useChatWindowStore.getState();
    s.setInput('问一句');
    s.submit();
    expect(useChatWindowStore.getState().status).toBe('open');

    useChatWindowStore.getState().minimize();
    const min = useChatWindowStore.getState();
    expect(min.status).toBe('minimized');
    expect(min.messages).toHaveLength(2);

    min.open();
    const restored = useChatWindowStore.getState();
    expect(restored.status).toBe('open');
    expect(restored.messages).toHaveLength(2);

    await vi.waitFor(() => expect(agentMessage().pending).toBe(false));
  });

  it('reset 清空消息、文件与会话 id', async () => {
    useChatWindowStore.getState().setInput('招标方式有哪些？');
    useChatWindowStore.getState().submit();
    await vi.waitFor(() => expect(agentMessage().pending).toBe(false));

    useChatWindowStore.getState().reset();
    const after = useChatWindowStore.getState();
    expect(after.messages).toHaveLength(0);
    expect(after.sessionId).toBeNull();
    expect(after.files).toHaveLength(0);
    expect(loadSessionId()).toBeNull();
  });
});
