import { defineConfig } from 'vitepress'

const GH = 'https://github.com/Peng-EA/embodied-autonomy-stack'
const DESC =
  '给 LLM 划边界，给具身智能补上稳态、反射、情感调制、需求生成与执行修正。v0.1 设计草案。'

// 说明：站点目前只有首页。arch/contracts/evals 等站内页面在第二步接入（由 docs/ 自动同步生成），
// 在那之前导航指向 GitHub 原文，避免出现 404。
export default defineConfig({
  lang: 'zh-CN',
  title: 'Embodied Autonomy Stack',
  titleTemplate: ':title · EA Stack',
  description: DESC,
  appearance: 'dark',
  cleanUrls: true,
  lastUpdated: false,
  // site/README.md 是仓库内的开发说明，不发布成页面
  srcExclude: ['README.md'],
  head: [
    ['meta', { name: 'theme-color', content: '#080B0C' }],
    ['meta', { name: 'color-scheme', content: 'dark light' }],
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:title', content: 'Embodied Autonomy Stack' }],
    ['meta', { property: 'og:description', content: DESC }],
    [
      'meta',
      { property: 'og:image', content: `${GH}/raw/main/docs/assets/architecture-long.png` },
    ],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['link', { rel: 'preconnect', href: 'https://fonts.googleapis.com' }],
    ['link', { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' }],
    [
      'link',
      {
        rel: 'stylesheet',
        href: 'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap',
      },
    ],
  ],
  markdown: {
    theme: { dark: 'github-dark-dimmed', light: 'github-light' },
    lineNumbers: false,
  },
  vite: {
    build: {
      // mermaid 单独切块：首屏不背这 1MB，只有需要渲染图时才拉
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes('node_modules/mermaid') || id.includes('node_modules/@mermaid-js')) {
              return 'mermaid'
            }
            if (id.includes('node_modules/cytoscape') || id.includes('node_modules/d3-')) {
              return 'mermaid-viz'
            }
          },
        },
      },
      chunkSizeWarningLimit: 3000,
    },
  },
  themeConfig: {
    siteTitle: 'EA // STACK',
    logo: false,
    nav: [
      { text: 'ARCHITECTURE', link: `${GH}/blob/main/docs/architecture.md` },
      { text: 'CONTRACTS', link: `${GH}/tree/main/contracts` },
      { text: 'BASELINES', link: `${GH}/tree/main/evals` },
      { text: 'DISCUSSIONS', link: `${GH}/discussions` },
    ],
    socialLinks: [{ icon: 'github', link: GH }],
    footer: {
      message: 'Apache-2.0 · v0.1 Design RFC · 四层神经系统是工程隐喻，不是生物等价声明',
      copyright: 'Embodied Autonomy Stack',
    },
    outline: { level: [2, 3], label: '本页' },
    docFooter: { prev: '上一页', next: '下一页' },
    darkModeSwitchLabel: '主题',
    sidebarMenuLabel: '目录',
    returnToTopLabel: '回到顶部',
    notFound: {
      title: '404',
      quote: '这条路径不在契约里。',
      linkLabel: '回到首页',
      linkText: 'return home',
    },
  },
  sitemap: { hostname: 'https://ea-stack.vercel.app' },
})
