import { useEffect, useRef, useState } from 'react';

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

/**
 * useAsyncData —— 极简异步取数。
 *
 * 默认挂载时跑一次；传 `key` 则在 key 变化时**重新取数**（服务端筛选/分页用，
 * 例如 `useAsyncData(() => api.supplier.getOverview(f), JSON.stringify(f))`）。
 *
 * 页面只经 `services/api.ts` 取数；本 hook 负责 loading / error 三态，
 * 卸载或 key 变化后旧请求的结果不再写入（避免竞态）。
 */
export function useAsyncData<T>(loader: () => Promise<T>, key?: unknown): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: true });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    let alive = true;
    setState({ data: null, error: null, loading: true });
    loaderRef.current().then(
      (data) => {
        if (alive) setState({ data, error: null, loading: false });
      },
      (err: unknown) => {
        if (alive) setState({ data: null, error: err as Error, loading: false });
      }
    );
    return () => {
      alive = false;
    };
    // key 是调用方给的**取数标识**（通常是筛选条件的序列化）；变了就重取
  }, [key]);

  return state;
}
