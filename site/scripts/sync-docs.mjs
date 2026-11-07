// docs/ → site/ 同步脚本
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
const OUT = SITE  // 直接落在站点根/子目录，路由就是 /vision、/blog/xxx
const PUBLIC_ASSETS = path.join(SITE, 'public', 'assets')

const GH = 'https://github.com/Peng-EA/embodied-autonomy-stack'
const BLOB = `${GH}/blob/main` // 目录用 blob URL 时 GitHub 会自动跳到 tree
// 仓库根上、但不属于站点页面的文件：站内链接必须指回 GitHub
const ROOT_REPO_FILES = ['AGENTS.md', 'CHANGELOG.md', 'README.en.md', 'LICENSE']

// docs 文件 → 站点路由（file 相对 docs/，route 相对站点根）
const PAGES = [
  { file: 'README.md', route: 'guide' },
  { file: 'vision.md', route: 'vision' },
  { file: 'architecture.md', route: 'architecture' },
  { file: 'interfaces.md', route: 'interfaces' },
  { file: 'evaluation.md', route: 'evaluation' },
  { file: 'safety.md', route: 'safety' },
  { file: 'roadmap.md', route: 'roadmap' },
  { file: 'glossary.md', route: 'glossary' },
  { file: 'blog/README.md', route: 'blog/index' },
  { file: 'blog/why-llm-should-not-be-the-real-time-center.md', route: 'blog/why-llm-should-not-be-the-real-time-center' },
]

const ROUTE_BY_FILE = Object.fromEntries(PAGES.map((p) => [p.file, p.route]))

// 仓库内、但站点不渲染的东西 → GitHub 绝对链接
const RAW_TARGETS = [
  ['../contracts/', `${BLOB}/contracts`],
  ['../.github/', `${BLOB}/.github`],
  ['../evals/', `${BLOB}/evals`],
  ['../site/', `${BLOB}/site`],
  ['../sim/', `${BLOB}/sim`],
  ['../testbed/', `${BLOB}/testbed`],
  ['../server/', `${BLOB}/server`],
  ['../evals/v0.2/', `${BLOB}/evals/v0.2`],
  ['../CONTRIBUTING.md', `${BLOB}/CONTRIBUTING.md`],
  ['../CODE_OF_CONDUCT.md', `${BLOB}/CODE_OF_CONDUCT.md`],
  ['../SECURITY.md', `${BLOB}/SECURITY.md`],
  ['../LICENSE', `${BLOB}/LICENSE`],
  ['../README.md', `${BLOB}/README.md`],
]

const posix = path.posix

function routeFor(relPath) {
  const clean = posix.normalize(relPath).replace(/^\.\//, '')
  return ROUTE_BY_FILE[clean]
}

function rewriteLinks(text, pageFile) {
  let out = text
  const pageDir = posix.dirname(pageFile) || '.'

  // 1) 图片：docs/assets/*.{png,svg,...} → /assets/*（构建时复制到 public/assets）
  out = out.replace(
    /\]\((?:\.\.\/)?assets\/([^)\s]+\.(?:png|jpe?g|svg|gif|webp))\)/gi,
    '](/assets/$1)'
  )
  // 1b) 其他 assets（如 .mmd 源文件）站点不提供 → GitHub
  out = out.replace(/\]\((?:\.\.\/)?assets\/([^)\s]+)\)/g, `](${BLOB}/docs/assets/$1)`)

  // 1c) 长图替换成站点自带的 inline SVG 组件：
  //     那张 PNG 是白底的（给 GitHub/README 用），直接放进深色站点会刺眼；
  //     而且它和页面上方的 SVG 图是同一张图，换成组件后永远同步。
  out = out.replace(/!\[[^\]]*\]\(\/assets\/architecture-long\.png\)/g, '<StackDiagram />')

  // 2) 仓库内非站点目标 → GitHub 绝对地址
  for (const [from, to] of RAW_TARGETS) {
    const esc = from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    out = out.replace(new RegExp(`\\]\\(${esc}([^)\\s]*)`, 'g'), (_m, rest) => `](${to}${rest ? '/' + rest.replace(/^\/+/, '') : ''}`)
  }

  // 2b) ADR 不在站点里渲染（避免又一套"文档站内文档"），统一指到 GitHub
  out = out.replace(/\]\((?:\.\.?\/)?adr\/([^)\s]*)\)/g, `](${BLOB}/docs/adr/$1)`)
  // 2c) blog/ 目录链接 → 站点路由（blog 是站内页）
  out = out.replace(/\]\((?:\.\/)?blog\/(?:README\.md)?\)/g, '](/blog/)')

  // 3) 同目录 xxx.md → /route（按页面所在目录解析）
  out = out.replace(/\]\((?:\.\/)?([A-Za-z0-9_/-]+)\.md(#[^)\s]*)?\)/g, (m, name, anchor) => {
    const route = routeFor(posix.join(pageDir, `${name}.md`))
    return route ? `](/${route}${anchor || ''})` : m
  })

  // 3b) 相对仓库根/其他目录：../xxx.md 或 ../blog/xxx.md
  //     根级文件（AGENTS.md / CHANGELOG.md / README.en.md）不是站点页面：
  //     站点只渲染 docs/ 下的正文，这些必须指回 GitHub，否则 VitePress 报 dead link
  //     （实测 ../AGENTS.md 会被解析成 ./../AGENTS 而 404）。
  out = out.replace(/\]\(\.\.\/([A-Za-z0-9_/-]+\.md)(#[^)\s]*)?\)/g, (m, rel, anchor) => {
    const route = routeFor(rel)
    if (route) return `](/${route}${anchor || ''})`
    if (ROOT_REPO_FILES.includes(posix.basename(rel))) return `](${BLOB}/${rel}${anchor || ''})`
    return m
  })

  // 3c) ../docs/xxx.md → /route
  out = out.replace(/\]\(\.\.\/docs\/([A-Za-z0-9_-]+)\.md(#[^)\s]*)?\)/g, (m, name, anchor) => {
    const route = routeFor(`${name}.md`)
    return route ? `](/${route}${anchor || ''})` : m
  })

  // 4) ```mermaid 代码块 → <Mermaid :code='"...json..."' />（站内真渲染，而不是贴源码）
  //    为什么不用模板字符串：把多行代码塞进 HTML 属性的 `` 里，Vue 模板解析器
  //    会把换行当成表达式未终止（实测报 Unterminated template）。
  //    这里转成**单行 JSON 字符串常量**，属性用单引号包，编译前就完成了转义。
  out = out.replace(/```mermaid\r?\n([\s\S]*?)```/g, (_m, code) => {
    const expr = JSON.stringify(code).replace(/'/g, '&#39;')
    return `<Mermaid :code='${expr}' />`
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
  const body = rewriteLinks(raw, page.file)
  const header = `<!-- 本文件由 site/scripts/sync-docs.mjs 从 docs/${page.file} 自动生成，请勿直接编辑 -->\n\n`
  return header + body
}

function main() {
  let written = 0
  for (const page of PAGES) {
    const content = buildOne(page)
    if (content === null) continue
    const dest = path.join(OUT, `${page.route}.md`)
    fs.mkdirSync(path.dirname(dest), { recursive: true })
    fs.writeFileSync(dest, content, 'utf8')
    written++
    console.log(`  ✓ docs/${page.file} → site/${page.route}.md`)
  }
  const assets = copyAssets()
  console.log(`docs 同步完成：${written} 个页面，${assets} 个图片资源`)
}

main()
