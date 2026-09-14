import { describe, expect, it, vi } from 'vitest';
import { BackendError, parseRetryAfter, withRetry } from '@/services/backend';

/* 边界打磨（接口清单 §6）：429 限流 / 503 过载的**退避重试**。
 * 只重试这两种 —— 其它错误重试没有意义，反而放大故障。 */

const fakeRes = (headers: Record<string, string>): Response =>
  ({ headers: { get: (k: string) => headers[k] ?? null } }) as unknown as Response;

describe('parseRetryAfter', () => {
  it('秒数 → 毫秒', () => {
    expect(parseRetryAfter(fakeRes({ 'Retry-After': '2' }))).toBe(2000);
  });

  it('没有该头返回 undefined', () => {
    expect(parseRetryAfter(fakeRes({}))).toBeUndefined();
  });

  it('非法值返回 undefined（不产生 NaN 等待）', () => {
    expect(parseRetryAfter(fakeRes({ 'Retry-After': 'soon' }))).toBeUndefined();
  });
});

describe('withRetry（429 / 503 退避重试）', () => {
  it('429 后重试并成功', async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new BackendError(429, '请求过于频繁', 0))
      .mockResolvedValueOnce('ok');
    await expect(withRetry(fn)).resolves.toBe('ok');
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('503 优先用服务端下发的 Retry-After', async () => {
    const onRetry = vi.fn();
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new BackendError(503, '过载', 7))
      .mockResolvedValueOnce('ok');
    await withRetry(fn, { onRetry });
    expect(onRetry).toHaveBeenCalledWith(1, 7);
  });

  it('没有 Retry-After 时指数退避', async () => {
    const onRetry = vi.fn();
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new BackendError(503, '过载'))
      .mockRejectedValueOnce(new BackendError(503, '过载'))
      .mockResolvedValueOnce('ok');
    await withRetry(fn, { onRetry });
    expect(onRetry.mock.calls.map((c) => c[1])).toEqual([400, 800]);
  });

  it('非 429/503 不重试', async () => {
    const fn = vi.fn().mockRejectedValue(new BackendError(400, '参数错误'));
    await expect(withRetry(fn)).rejects.toBeInstanceOf(BackendError);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('网络层失败（status=0）不重试', async () => {
    const fn = vi.fn().mockRejectedValue(new BackendError(0, '网络不可达'));
    await expect(withRetry(fn)).rejects.toThrow('网络不可达');
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('重试次数用尽后抛最后一次错误', async () => {
    const fn = vi.fn().mockRejectedValue(new BackendError(429, '请求过于频繁', 0));
    await expect(withRetry(fn, { attempts: 3 })).rejects.toMatchObject({ status: 429 });
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('BackendError 携带状态码与 Retry-After', () => {
    const err = new BackendError(503, '过载', 1500);
    expect(err.status).toBe(503);
    expect(err.retryAfterMs).toBe(1500);
    expect(err.name).toBe('BackendError');
  });
});
