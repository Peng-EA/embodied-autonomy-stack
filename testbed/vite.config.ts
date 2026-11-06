import { defineConfig } from 'vite';

// 站点发布在 /testbed/ 下（docs-site.yml 把 dist 拷进 site/public/testbed）
export default defineConfig({
  base: './',
  build: { outDir: 'dist', target: 'es2022' },
  worker: { format: 'es' },
});
