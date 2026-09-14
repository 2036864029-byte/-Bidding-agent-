import '@testing-library/jest-dom/vitest';

/* jsdom 未实现 matchMedia —— framer-motion（MotionConfig reducedMotion="user"）需要。
 * node 环境（如直连真后端的联调探针）没有 window，跳过即可。 */
if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
