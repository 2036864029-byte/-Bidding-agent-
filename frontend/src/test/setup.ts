import '@testing-library/jest-dom/vitest';

/* jsdom 未实现 matchMedia —— framer-motion（MotionConfig reducedMotion="user"）需要 */
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
