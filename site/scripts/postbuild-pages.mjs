// 构建后处理：把平铺的 xxx.html 重排成 xxx/index.html
//
// 为什么需要：GitHub Pages 不支持 clean URL（/interfaces 不会映射到 interfaces.html），
// 但它认目录索引（/interfaces/ → /interfaces/index.html，且 /interfaces 会 301 到 /interfaces/）。
// 所以：VitePress 开 cleanUrls（链接写成 /interfaces，死链检查才能过），
// 构建后把文件摆成目录结构，两种能力凑一起 = 干净的 URL 且能在 Pages 上跑。
//
// 不动 index.html（首页）与 404.html（Pages 必须在根）。

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
for (const entry of fs.readdirSync(DIST, { withFileTypes: true })) {
  if (!entry.isFile() || !entry.name.endsWith('.html')) continue
  if (KEEP_AT_ROOT.has(entry.name)) continue

  const name = entry.name.replace(/\.html$/, '')
  const dir = path.join(DIST, name)
  fs.mkdirSync(dir, { recursive: true })
  fs.renameSync(path.join(DIST, entry.name), path.join(dir, 'index.html'))
  moved++
  console.log(`  ↻ ${entry.name} → ${name}/index.html`)
}

console.log(`页面重排完成：${moved} 个页面转成目录索引`)
