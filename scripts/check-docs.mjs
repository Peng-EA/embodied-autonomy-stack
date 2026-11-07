#!/usr/bin/env node
// 文档结构检查：链接可解析、表格有分隔行、无行尾空白/Tab、文件以换行结尾。
// 不引第三方依赖，CI 里直接 `node scripts/check-docs.mjs` 就能跑。
//
// 检查范围：仓库内所有 .md（跳过 node_modules / .venv / dist / logs / .git / .tmp）。
// .tmp/ 是本地落地目录（权重、下载的仓库），里面的 README 不是本仓库的文档，不该算死链。

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const SKIP_DIRS = new Set(['.git', 'node_modules', '.venv', 'dist', 'logs', '__pycache__', '.vitepress', '.tmp'])

let problems = 0

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue
      walk(path.join(dir, entry.name), out)
    } else if (entry.name.endsWith('.md')) {
      const full = path.join(dir, entry.name)
      const r = path.relative(ROOT, full).split(path.sep).join('/')
      // site/ 下的正文是 docs/ 同步生成的（链接是站点路由，不是仓库路径），只检查手写的两份
      if (/^site\//.test(r) && !/^site\/(index|README)\.md$/.test(r)) continue
      out.push(full)
    }
  }
  return out
}

const files = walk(ROOT)
const rel = (p) => path.relative(ROOT, p).replace(/\\/g, '/')

// ---------- 1) 本地链接
let checkedLinks = 0
for (const f of files) {
  const lines = fs.readFileSync(f, 'utf8').split('\n')
  let inFence = false
  lines.forEach((line, i) => {
    if (/^\s*```/.test(line)) { inFence = !inFence; return }
    if (inFence) return
    const re = /!?\[[^\]]*\]\(([^)\s]+)\)/g
    let m
    while ((m = re.exec(line)) !== null) {
      let target = m[1].trim()
      if (/^(https?:|mailto:|#)/i.test(target)) continue
      target = target.split('#')[0]
      if (!target) continue
      checkedLinks++
      const abs = target.startsWith('/')
        ? path.join(ROOT, target.slice(1))
        : path.resolve(path.dirname(f), decodeURIComponent(target))
      if (!fs.existsSync(abs)) {
        problems++
        console.log(`  MISSING  ${rel(f)}:${i + 1}  →  ${m[1]}`)
      }
    }
  })
}
console.log(`链接：检查 ${checkedLinks} 个，问题 ${problems} 个`)

// ---------- 2) 表格结构
const before = problems
let tables = 0
for (const f of files) {
  const lines = fs.readFileSync(f, 'utf8').split('\n')
  let inFence = false
  const blocks = []
  let cur = null
  const isRow = (s) => s.trim().startsWith('|') && s.trim().endsWith('|')
  const isSep = (s) => /^\|[\s:|-]+\|$/.test(s.trim()) && s.includes('-')
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*```/.test(lines[i])) { inFence = !inFence; cur = null; continue }
    if (inFence) continue
    if (isRow(lines[i])) {
      if (!cur) { cur = { start: i, rows: [] }; blocks.push(cur) }
      cur.rows.push({ text: lines[i].trim(), line: i + 1 })
    } else cur = null
  }
  for (const b of blocks) {
    tables++
    if (b.rows.length < 2) { problems++; console.log(`  单行表格 ${rel(f)}:${b.rows[0].line}`); continue }
    if (!isSep(b.rows[1].text)) { problems++; console.log(`  缺分隔行 ${rel(f)}:${b.rows[0].line}`); continue }
    const count = (s) => (s.match(/\|/g) || []).length
    const expect = count(b.rows[0].text)
    b.rows.forEach((r, idx) => {
      if (idx === 1) return
      if (count(r.text) !== expect) {
        problems++
        console.log(`  列数不符 ${rel(f)}:${r.line}（表头 ${expect - 1} 列，该行 ${count(r.text) - 1} 列）`)
      }
    })
  }
}
console.log(`表格：${tables} 张，新增问题 ${problems - before} 个`)

// ---------- 3) 排版
const before2 = problems
for (const f of files) {
  const lines = fs.readFileSync(f, 'utf8').split('\n')
  lines.forEach((l, i) => {
    if (/[ \t]+$/.test(l)) { problems++; console.log(`  行尾空白 ${rel(f)}:${i + 1}`) }
    if (l.includes('\t')) { problems++; console.log(`  Tab 字符 ${rel(f)}:${i + 1}`) }
  })
  if (!fs.readFileSync(f, 'utf8').endsWith('\n')) { problems++; console.log(`  末尾缺换行 ${rel(f)}`) }
}
console.log(`排版：新增问题 ${problems - before2} 个`)

console.log(problems ? `\n总计 ${problems} 个问题` : '\n全部通过')
process.exit(problems ? 1 : 0)
