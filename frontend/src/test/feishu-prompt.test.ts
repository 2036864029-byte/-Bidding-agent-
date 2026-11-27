import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { feishuPrompted, markFeishuPrompted } from '@/lib/chat';
import { api } from '@/services/api';
import { useChatWindowStore } from '@/store/chat-window';

/* 并行线 A「连接飞书」提醒：lib 标记 + store 触发/关闭/跳转。 */

function resetStore() {
  localStorage.clear();
  useChatWindowStore.setState({
    status: 'idle',
    messages: [],
    input: '',
    files: [],
    busy: false,
    sessionId: 's1',
    notice: null,
    feishuPrompt: null,
  });
}

describe('lib：飞书提醒标记（同会话只弹一次）', () => {
  beforeEach(() => localStorage.clear());

  it('mark 后本会话 feishuPrompted=true，其它会话不受影响', () => {
    expect(feishuPrompted('s1')).toBe(false);
    markFeishuPrompted('s1');
    expect(feishuPrompted('s1')).toBe(true);
    expect(feishuPrompted('s2')).toBe(false);
  });
});

describe('store：checkFeishuPrompt 触发弹窗', () => {
  beforeEach(resetStore);
  afterEach(() => vi.restoreAllMocks());

  it('未绑定 → 情况1（unbound）', async () => {
    vi.spyOn(api.feishu, 'binding').mockResolvedValue(false);
    useChatWindowStore.getState().checkFeishuPrompt();
    await vi.waitFor(() =>
      expect(useChatWindowStore.getState().feishuPrompt).toBe('unbound')
    );
  });

  it('已绑定 → 情况2（bound）', async () => {
    vi.spyOn(api.feishu, 'binding').mockResolvedValue(true);
    useChatWindowStore.getState().checkFeishuPrompt();
    await vi.waitFor(() =>
      expect(useChatWindowStore.getState().feishuPrompt).toBe('bound')
    );
  });

  it('本会话已弹过 → 不再查询也不弹', () => {
    markFeishuPrompted('s1');
    const spy = vi.spyOn(api.feishu, 'binding');
    useChatWindowStore.getState().checkFeishuPrompt();
    expect(spy).not.toHaveBeenCalled();
    expect(useChatWindowStore.getState().feishuPrompt).toBeNull();
  });

  it('无 sessionId → 不查询不弹', () => {
    useChatWindowStore.setState({ sessionId: null });
    const spy = vi.spyOn(api.feishu, 'binding');
    useChatWindowStore.getState().checkFeishuPrompt();
    expect(spy).not.toHaveBeenCalled();
    expect(useChatWindowStore.getState().feishuPrompt).toBeNull();
  });

  it('查询失败（后端未配飞书）→ 静默不弹', async () => {
    const spy = vi.spyOn(api.feishu, 'binding').mockRejectedValue(new Error('503'));
    useChatWindowStore.getState().checkFeishuPrompt();
    await vi.waitFor(() => expect(spy).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10)); // 等 .catch 跑完
    expect(useChatWindowStore.getState().feishuPrompt).toBeNull();
  });

  it('已弹 unbound 后不再重复弹（prompt 非空即短路）', async () => {
    vi.spyOn(api.feishu, 'binding').mockResolvedValue(false);
    const s = useChatWindowStore.getState();
    s.checkFeishuPrompt();
    await vi.waitFor(() => expect(useChatWindowStore.getState().feishuPrompt).toBe('unbound'));
    useChatWindowStore.getState().checkFeishuPrompt(); // 再触发一次
    expect(useChatWindowStore.getState().feishuPrompt).toBe('unbound');
  });
});

describe('store：connectFeishu / dismissFeishuPrompt', () => {
  beforeEach(resetStore);
  afterEach(() => vi.restoreAllMocks());

  it('connectFeishu → 拿授权链 + 关弹窗 + 记已弹', async () => {
    // jsdom 的 window.location.assign 只读不可 mock，跳转本身无法断言——
    // 断言可观察副作用：授权链已取、弹窗关闭、已记「本会话弹过」。
    const auth = vi.spyOn(api.feishu, 'authUrl').mockResolvedValue('https://open.feishu.cn/authorize?x');
    useChatWindowStore.getState().connectFeishu();
    await vi.waitFor(() => expect(auth).toHaveBeenCalledWith('s1', window.location.href));
    await new Promise((r) => setTimeout(r, 5)); // 让 .then(assignHref) 跑完
    expect(useChatWindowStore.getState().feishuPrompt).toBeNull();
    expect(feishuPrompted('s1')).toBe(true); // 跳转前就记，避免往返重复弹
  });

  it('connectFeishu 拿授权链失败 → 提示且不跳', async () => {
    vi.spyOn(api.feishu, 'authUrl').mockRejectedValue(new Error('503'));
    useChatWindowStore.getState().connectFeishu();
    await new Promise((r) => setTimeout(r, 10));
    expect(useChatWindowStore.getState().notice).toContain('连接飞书暂时不可用');
    expect(useChatWindowStore.getState().feishuPrompt).toBeNull();
  });

  it('dismissFeishuPrompt → 关弹窗 + 记已弹', () => {
    useChatWindowStore.setState({ feishuPrompt: 'unbound' });
    useChatWindowStore.getState().dismissFeishuPrompt();
    expect(useChatWindowStore.getState().feishuPrompt).toBeNull();
    expect(feishuPrompted('s1')).toBe(true);
  });
});
