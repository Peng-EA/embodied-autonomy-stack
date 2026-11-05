import { defineConfig } from 'vitepress'

const GH = 'https://github.com/Peng-EA/embodied-autonomy-stack'
const DESC =
  '给 LLM 划边界，给具身智能补上稳态、反射、情感调制、需求生成与执行修正。v0.1 设计草案。'

// 部署形态有两种，靠 EA_BASE 区分：
//   - 自定义域名 pjtbot.xyz（目标）：根路径，base = '/'
//   - 回退到 <user>.github.io/<repo>/：CI 注入 EA_BASE=/embodied-autonomy-stack/
const BASE = process.env.EA_BASE || '/'

// docs/*.md 由 scripts/sync-docs.mjs 生成为站点根下的同名页面
// （site/vision.md → /vision），站点不手写正文，避免双份维护分裂。

export default defineConfig({
  lang: 'zh-CN',
  base: BASE,
  title: 'Embodied Autonomy Stack',
  titleTemplate: ':title · EA Stack',
  description: DESC,
  // 强制暗色：不渲染切换按钮，也没有亮色主题可切（避免半吊子黑白）
  appearance: 'force-dark',
  // 链接写成 /interfaces（无 .html）；实际文件由 scripts/postbuild-pages.mjs
  // 重排成 interfaces/index.html —— GitHub Pages 认目录索引，这样两边都满足。
  cleanUrls: true,
  lastUpdated: false,
  // site/README.md 与生成脚本不发布成页面
  srcExclude: ['README.md', 'scripts/**'],
  head: [
    ['meta', { name: 'theme-color', content: '#080B0C' }],
    ['meta', { name: 'color-scheme', content: 'dark' }],
    ['link', { rel: 'icon', type: 'image/svg+xml', href: `${BASE}favicon.svg` }],
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
            // mermaid 与它的可视化依赖放同一个块，避免 chunk 循环依赖告警
            if (
              id.includes('node_modules/mermaid') ||
              id.includes('node_modules/@mermaid-js') ||
              id.includes('node_modules/cytoscape') ||
              id.includes('node_modules/d3-')
            ) {
              return 'mermaid'
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
      { text: 'ARCHITECTURE', link: '/architecture' },
      { text: 'CONTRACTS', link: '/interfaces' },
      { text: 'BASELINES', link: '/evaluation' },
      { text: 'ROADMAP', link: '/roadmap' },
      { text: 'DISCUSSIONS', link: `${GH}/discussions` },
    ],
    sidebar: [
      {
        text: '起点',
        items: [
          { text: '文档索引', link: '/guide' },
          { text: '为什么做这个', link: '/vision' },
        ],
      },
      {
        text: '架构',
        items: [
          { text: '分层架构', link: '/architecture' },
          { text: '层间信号契约', link: '/interfaces' },
          { text: '术语表', link: '/glossary' },
        ],
      },
      {
        text: '评价与安全',
        items: [
          { text: '评价体系与基线', link: '/evaluation' },
          { text: '安全与伦理', link: '/safety' },
        ],
      },
      {
        text: '推进',
        items: [
          { text: '路线图', link: '/roadmap' },
          { text: '架构决策记录 (ADR)', link: `${GH}/tree/main/docs/adr` },
        ],
      },
      {
        text: '博客',
        items: [
          { text: '全部文章', link: '/blog/' },
          { text: '为什么 LLM 不该做实时中心', link: '/blog/why-llm-should-not-be-the-real-time-center' },
        ],
      },
    ],
    search: { provider: 'local' },
    socialLinks: [{ icon: 'github', link: GH }],
    footer: {
      message: 'Apache-2.0 · v0.1 Design RFC · 四层神经系统是工程隐喻，不是生物等价声明',
      copyright: 'Embodied Autonomy Stack',
    },
    outline: { level: [2, 3], label: '本页' },
    docFooter: { prev: '上一页', next: '下一页' },
    sidebarMenuLabel: '目录',
    returnToTopLabel: '回到顶部',
    notFound: {
      title: '404',
      quote: '这条路径不在契约里。',
      linkLabel: '回到首页',
      linkText: 'return home',
    },
  },
})
