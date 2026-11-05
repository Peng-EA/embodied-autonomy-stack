// 构建后处理：把平铺的 xxx.html 重排成 xxx/index.html（**递归**）
//
// 为什么需要：GitHub Pages 不支持 clean URL（/interfaces 不会映射到 interfaces.html），
// 但它认目录索引（/interfaces/ → /interfaces/index.html，且 /interfaces 会 301 到 /interfaces/）。
// 所以：VitePress 开 cleanUrls（链接写成 /interfaces，死链检查才能过），
// 构建后把文件摆成目录结构，两种能力凑一起 = 干净的 URL 且能在 Pages 上跑。
//
// 早期版本只处理顶层，导致 /blog/<slug>.html 在 Pages 上 404；这里改成递归。
//
// 不动：根 index.html（首页）、根 404.html（Pages 必须）、任何已有的 index.html。

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DIST = path.resolve(HERE, '..', '.vitepress', 'dist')
const KEEP_AT_ROOT = new Set(['index.html', '404.html'])

if (!fs.existsSync(DIST)) {
  console.error('dist 不存在，先跑 vitepress build')
  process.exit(1)
}

let moved = 0

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      walk(full)
      continue
    }
    if (!entry.name.endsWith('.html')) continue
    if (entry.name === 'index.html') continue                    // 已经是目录索引
    if (dir === DIST && KEEP_AT_ROOT.has(entry.name)) continue    // 首页 / 404

    const base = entry.name.replace(/\.html$/, '')
    const targetDir = path.join(dir, base)
    fs.mkdirSync(targetDir, { recursive: true })
    fs.renameSync(full, path.join(targetDir, 'index.html'))
    moved++
    console.log(`  ↻ ${path.relative(DIST, full)} → ${path.relative(DIST, path.join(targetDir, 'index.html'))}`)
  }
}

walk(DIST)
console.log(`页面重排完成：${moved} 个页面转成目录索引`)
