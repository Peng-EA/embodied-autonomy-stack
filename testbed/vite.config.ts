import { defineConfig } from 'vite';
import { resolve } from 'node:path';

// 三个入口：
//   index.html   —— 技术台（俯视物理 + 遥测）
//   console.html —— 演示台（角色 + 房间 + 对话 + 认知面板）
//   demo.html    —— G1 演示台（真 G1 + 可调控制面板；一条命令见 scripts/serve-demo.mjs）
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
        demo: resolve(import.meta.dirname, 'demo.html'),
      },
    },
  },
  worker: { format: 'es' },
});
