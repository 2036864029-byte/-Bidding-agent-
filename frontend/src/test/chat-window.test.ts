import { beforeEach, describe, expect, it } from 'vitest';
import { useChatWindowStore } from '@/store/chat-window';

/* chat-window 状态机（D6）：发送开浮窗 / 空白不发送 / 收起不销毁会话 */

describe('chat-window 状态机', () => {
  beforeEach(() => {
    useChatWindowStore.setState({ status: 'idle', messages: [], input: '' });
  });

  it('submit 打开浮窗，追加用户消息 + 占位回复，清空输入', () => {
    const s = useChatWindowStore.getState();
    s.setInput('  招标方式有哪些？  ');
    s.submit();
    const after = useChatWindowStore.getState();
    expect(after.status).toBe('open');
    expect(after.messages).toHaveLength(2);
    expect(after.messages[0]).toMatchObject({ role: 'user', content: '招标方式有哪些？' });
    expect(after.messages[1].role).toBe('agent');
    expect(after.input).toBe('');
  });

  it('空白输入不发送', () => {
    useChatWindowStore.getState().submit();
    const after = useChatWindowStore.getState();
    expect(after.status).toBe('idle');
    expect(after.messages).toHaveLength(0);
  });

  it('minimize 收起保留消息，open 恢复同一会话', () => {
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
  });
});
