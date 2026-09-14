import { useEffect, useRef, useState } from 'react';

export interface AsyncState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

/**
 * useAsyncData —— 极简异步取数（挂载时跑一次）。
 *
 * 页面只经 `services/api.ts` 取数；本 hook 负责 loading / error 三态，
 * 卸载后不再 setState（避免竞态写入）。
 */
export function useAsyncData<T>(loader: () => Promise<T>): AsyncState<T> {
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
  }, []);

  return state;
}
