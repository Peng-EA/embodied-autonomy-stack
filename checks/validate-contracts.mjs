#!/usr/bin/env node
// 契约校验：JSON Schema 能编译、样例能通过、反例能被拒；基线 yaml 与 Issue 模板能解析。
//
// 依赖：ajv（2020-12）与 js-yaml，装在 checks/ 里（CI 单独 npm install）。
// 本地跑法：
//   cd checks && npm install && node validate-contracts.mjs

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import Ajv2020 from 'ajv/dist/2020.js'
import yaml from 'js-yaml'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const rel = (p) => path.relative(ROOT, p).replace(/\\/g, '/')

let failures = 0
const ok = (m) => console.log('  PASS  ' + m)
const bad = (m) => { failures++; console.log('  FAIL  ' + m) }

// ---------- 1) Schema 编译 + 样例
console.log('== schema 编译 ==')
const ajv = new Ajv2020({ allErrors: true, strict: false })
const schemaDir = path.join(ROOT, 'contracts')
const compiled = {}
for (const f of fs.readdirSync(schemaDir).filter((f) => f.endsWith('.schema.json'))) {
  try {
    compiled[f] = ajv.compile(JSON.parse(fs.readFileSync(path.join(schemaDir, f), 'utf8')))
    ok(f)
  } catch (e) {
    bad(`${f} 编译失败: ${e.message}`)
  }
}

console.log('== 样例校验 ==')
const pairs = [
  ['affect_vector.schema.json', 'examples/affect_vector.example.json'],
  ['homeostatic.schema.json', 'examples/homeostatic.example.json'],
  ['signals.schema.json', 'examples/outcome_event.example.json'],
  ['signals.schema.json', 'examples/goal_candidate.example.json'],
]
for (const [schemaFile, exampleFile] of pairs) {
  const validate = compiled[schemaFile]
  if (!validate) { bad(`${exampleFile} 无对应 schema`); continue }
  const data = JSON.parse(fs.readFileSync(path.join(schemaDir, exampleFile), 'utf8'))
  validate(data) ? ok(`${exampleFile} → ${schemaFile}`) : bad(`${exampleFile}: ${JSON.stringify(validate.errors)}`)
}

console.log('== 反例（应当被拒） ==')
const negCases = [
  ['homeostatic.schema.json', 'examples/homeostatic.example.json', (d) => delete d.severity, '缺 severity'],
  ['signals.schema.json', 'examples/goal_candidate.example.json', (d) => delete d.candidates[0].source_need, '缺 source_need'],
  ['affect_vector.schema.json', 'examples/affect_vector.example.json', (d) => { d.valence = 5 }, 'valence 越界'],
]
for (const [schemaFile, exampleFile, mutate, label] of negCases) {
  const validate = compiled[schemaFile]
  if (!validate) continue
  const data = JSON.parse(fs.readFileSync(path.join(schemaDir, exampleFile), 'utf8'))
  mutate(data)
  validate(data) ? bad(`${label} 未被拒（schema 太松）`) : ok(`${label} 被拒`)
}

// ---------- 2) 基线 yaml + Issue 模板
console.log('== yaml 解析 ==')
const yamlFiles = []
const walk = (dir) => {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) {
      if (['.git', 'node_modules', '.venv', 'dist', 'logs', '__pycache__'].includes(e.name)) continue
      walk(path.join(dir, e.name))
    } else if (/\.ya?ml$/i.test(e.name)) yamlFiles.push(path.join(dir, e.name))
  }
}
walk(ROOT)
let baselines = 0
for (const f of yamlFiles) {
  try {
    const doc = yaml.load(fs.readFileSync(f, 'utf8'))
    if (rel(f).startsWith('evals/baselines/')) {
      baselines++
      const need = ['id', 'description', 'layers', 'preemption', 'need_generation', 'metrics']
      const miss = need.filter((k) => !(k in doc))
      miss.length ? bad(`${rel(f)} 缺字段: ${miss.join(',')}`) : ok(`${rel(f)} → id=${doc.id} metrics=${doc.metrics.length}`)
    } else {
      ok(rel(f))
    }
  } catch (e) {
    bad(`${rel(f)} 解析失败: ${e.message}`)
  }
}
if (baselines !== 4) bad(`基线文件数应为 4，实际 ${baselines}`)

// ---------- 3) Issue 模板结构
console.log('== Issue 模板 ==')
const itDir = path.join(ROOT, '.github', 'ISSUE_TEMPLATE')
for (const f of fs.readdirSync(itDir)) {
  const doc = yaml.load(fs.readFileSync(path.join(itDir, f), 'utf8'))
  if (f === 'config.yml') {
    doc && Array.isArray(doc.contact_links) ? ok(f) : bad(`${f} 结构不对`)
  } else {
    const good = doc && doc.name && doc.description && Array.isArray(doc.body) &&
      doc.body.every((b) => b.type && b.attributes)
    good ? ok(`${f} → ${doc.name}（${doc.body.length} 字段）`) : bad(`${f} 结构不对`)
  }
}

console.log('\n' + (failures ? `失败 ${failures} 项` : '全部通过'))
process.exit(failures ? 1 : 0)
