// docs/ → site/content/ 同步脚本（第二步）
// 原则：docs/ 是唯一事实源，站点页面只是"渲染副本"。改文档只改 docs/，
// 站点构建时自动重新生成，不做双份手写（双份必然分裂）。
//
// 用法：node scripts/sync-docs.mjs   （docs:dev / docs:build 会自动先跑）

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.resolve(HERE, '..')
const REPO = path.resolve(SITE, '..')
const DOCS = path.join(REPO, 'docs')
const OUT = SITE  // 直接落在站点根，路由就是 /vision
const PUBLIC_ASSETS = path.join(SITE, 'public', 'assets')

const GH = 'https://github.com/Peng-EA/embodied-autonomy-stack'
const BLOB = `${GH}/blob/main`   // 目录用 blob URL 时 GitHub 会自动跳到 tree

// docs 文件 → 站点路由
const PAGES = [
  { file: 'README.md', route: 'guide', title: '文档索引' },
  { file: 'vision.md', route: 'vision', title: '为什么做这个' },
  { file: 'architecture.md', route: 'architecture', title: '架构' },
  { file: 'interfaces.md', route: 'interfaces', title: '层间信号契约' },
  { file: 'evaluation.md', route: 'evaluation', title: '评价体系与基线' },
  { file: 'safety.md', route: 'safety', title: '安全与伦理' },
  { file: 'roadmap.md', route: 'roadmap', title: '路线图' },
  { file: 'glossary.md', route: 'glossary', title: '术语表' },
]

const ROUTE_BY_FILE = Object.fromEntries(PAGES.map((p) => [p.file, p.route]))

// 仓库内、但站点不渲染的东西 → GitHub 绝对链接
const RAW_TARGETS = [
  ['../contracts/', `${BLOB}/contracts`],
  ['../.github/', `${BLOB}/.github`],
  ['../evals/', `${BLOB}/evals`],
  ['../site/', `${BLOB}/site`],
  ['../CONTRIBUTING.md', `${BLOB}/CONTRIBUTING.md`],
  ['../CODE_OF_CONDUCT.md', `${BLOB}/CODE_OF_CONDUCT.md`],
  ['../SECURITY.md', `${BLOB}/SECURITY.md`],
  ['../LICENSE', `${BLOB}/LICENSE`],
  ['../README.md', `${BLOB}/README.md`],
]

function rewriteLinks(text) {
  let out = text

  // 1) 图片：docs/assets/*.{png,svg,...} → /assets/*（构建时复制到 public/assets）
  out = out.replace(
    /\]\((?:\.\.\/)?assets\/([^)\s]+\.(?:png|jpe?g|svg|gif|webp))\)/gi,
    '](/assets/$1)'
  )
  // 1b) 其他 assets（如 .mmd 源文件）站点不提供 → GitHub
  out = out.replace(
    /\]\((?:\.\.\/)?assets\/([^)\s]+)\)/g,
    `](${BLOB}/docs/assets/$1)`
  )

  // 1c) 长图替换成站点自带的 inline SVG 组件：
  //     那张 PNG 是白底的（给 GitHub/README 用），直接放进深色站点会刺眼；
  //     而且它和页面上方的 SVG 图是同一张图，换成组件后永远同步。
  out = out.replace(
    /!\[[^\]]*\]\(\/assets\/architecture-long\.png\)/g,
    '<StackDiagram />'
  )

  // 2) 仓库内非站点目标 → GitHub 绝对地址
  for (const [from, to] of RAW_TARGETS) {
    const esc = from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    out = out.replace(new RegExp(`\\]\\(${esc}([^)\\s]*)`, 'g'), (_m, rest) => `](${to}${rest ? '/' + rest.replace(/^\/+/, '') : ''}`)
  }

  // 3) docs/xxx.md → /route
  out = out.replace(/\]\((?:\.\/)?([A-Za-z0-9_-]+)\.md(#[^)\s]*)?\)/g, (m, name, anchor) => {
    const route = ROUTE_BY_FILE[`${name}.md`]
    return route ? `](/${route}${anchor || ''})` : m
  })

  // 4) ../docs/xxx.md → /route
  out = out.replace(/\]\(\.\.\/docs\/([A-Za-z0-9_-]+)\.md(#[^)\s]*)?\)/g, (m, name, anchor) => {
    const route = ROUTE_BY_FILE[`${name}.md`]
    return route ? `](/${route}${anchor || ''})` : m
  })

  return out
}

function copyAssets() {
  const src = path.join(DOCS, 'assets')
  if (!fs.existsSync(src)) return 0
  fs.mkdirSync(PUBLIC_ASSETS, { recursive: true })
  let n = 0
  for (const f of fs.readdirSync(src)) {
    if (!/\.(png|jpe?g|svg|gif|webp)$/i.test(f)) continue
    fs.copyFileSync(path.join(src, f), path.join(PUBLIC_ASSETS, f))
    n++
  }
  return n
}

function buildOne(page) {
  const srcPath = path.join(DOCS, page.file)
  if (!fs.existsSync(srcPath)) {
    console.warn(`  ! 缺少 docs/${page.file}，跳过`)
    return null
  }
  const raw = fs.readFileSync(srcPath, 'utf8')
  const body = rewriteLinks(raw)
  const header = [
    '<!-- 本文件由 site/scripts/sync-docs.mjs 从 docs/' + page.file + ' 自动生成，请勿直接编辑 -->',
    '',
  ].join('\n')
  return header + body
}

function main() {
  fs.mkdirSync(OUT, { recursive: true })
  let written = 0
  for (const page of PAGES) {
    const content = buildOne(page)
    if (content === null) continue
    const dest = path.join(OUT, `${page.route}.md`)
    fs.writeFileSync(dest, content, 'utf8')
    written++
    console.log(`  ✓ docs/${page.file} → site/${page.route}.md`)
  }
  const assets = copyAssets()
  console.log(`docs 同步完成：${written} 个页面，${assets} 个图片资源`)
}

main()
