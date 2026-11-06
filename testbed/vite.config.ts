import { defineConfig } from 'vite';
import { resolve } from 'node:path';

// 两个入口：
//   index.html   —— 技术台（俯视物理 + 遥测）
//   console.html —— 演示台（角色 + 房间 + 对话 + 认知面板）
// 站点发布在 /testbed/ 下（docs-site.yml 把 dist 拷进 site/public/testbed）
export default defineConfig({
  base: './',
  build: {
    outDir: 'dist',
    target: 'es2022',
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        console: resolve(import.meta.dirname, 'console.html'),
      },
    },
  },
  worker: { format: 'es' },
});
