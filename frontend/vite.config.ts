import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';

// 开发代理：/api → 后端 127.0.0.1:8000（后端端点无 /api 前缀，故 rewrite 去掉）
// 生产：同域反代，由部署侧配置（见 docs/前端接口清单.md §0 Base URL）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    // 分包交给 Rollup 自动处理：路由级懒加载（见 App.tsx）已能让图表库按需拉取。
    // 曾用 manualChunks 对象式硬分「charts / markdown / motion」，结果 Rollup 把
    // 入口共用的符号也抬进 charts chunk，反而使**入口静态 import 图表库**
    // （modulepreload 提前拉 438KB）—— 与懒加载的初衷相反。
    // 显式把 chunk 体积上限抬高，警告只对真正异常的膨胀发声。
    chunkSizeWarningLimit: 600,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
