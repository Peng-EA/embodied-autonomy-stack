#!/usr/bin/env node
// 仲裁优先级的只读断言（P0）：sim/config.py 的 PRIORITY / PRIORITY_ORDER 自洽，
// 且等于**固定的权威顺序**。不启动仿真、不改任何行为。
//
// 跑法：  cd checks && node check-priority.mjs
// 反向验红（已做）：把 config 里 l2b_reflex 与 l4_plan 的权重对调 → 本脚本必须 FAIL。
//
// 为什么权威顺序在这里硬编码一份：若把"键集合与 PRIORITY_ORDER 一致"建立在
// config 自己的 PRIORITY_ORDER 上，改 config 就自己证自己、断言恒真。
// 硬编码的 EXPECTED_ORDER 是外部事实源，config 两处都得对上它才绿。

import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

// 仓库根：fileURLToPath(new URL('..'))，不用 import.meta.dirname（老 Node 没有）
const ROOT = fileURLToPath(new URL('..', import.meta.url))
const PY = path.join(ROOT, 'sim', '.venv', 'Scripts', 'python.exe')

// 权威顺序（固定，不许改）
const EXPECTED_ORDER = [
  'hardware_estop',
  'l1_survival',
  'l2b_reflex',
  'l3_user',
  'l4_plan',
  'l2a_skill',
]

let failures = 0
const ok = (m) => console.log('  PASS  ' + m)
const bad = (m) => { failures += 1; console.log('  FAIL  ' + m) }
const show = (a) => JSON.stringify(a)

function runPy(code, label) {
  const r = spawnSync(PY, ['-c', code], { cwd: ROOT, encoding: 'utf8' })
  if (r.error) {
    bad(`${label}: 调不起解释器 ${PY}（${r.error.message}）`)
    return undefined
  }
  if (r.status !== 0) {
    const err = (r.stderr || '').trim().split(/\r?\n/).slice(-3).join(' | ')
    bad(`${label}: python 退出码 ${r.status}；stderr=${err}`)
    return undefined
  }
  try {
    return JSON.parse(r.stdout)
  } catch (e) {
    bad(`${label}: stdout 不是合法 JSON（${e.message}）：${String(r.stdout).trim().slice(0, 200)}`)
    return undefined
  }
}

console.log('== sim/config.py 仲裁优先级 ==')

const PRIORITY = runPy(
  'import json;from sim.config import PRIORITY;print(json.dumps(PRIORITY))',
  'PRIORITY',
)
const ORDER = runPy(
  'import json;from sim.config import PRIORITY_ORDER;print(json.dumps(PRIORITY_ORDER))',
  'PRIORITY_ORDER',
)

if (PRIORITY === undefined || ORDER === undefined) {
  // 取不到常量时后面每条断言都会连锁报错，直接收口
  console.log(`priority: ${failures} 条失败`)
  process.exit(1)
}

const keys = Object.keys(PRIORITY)
const order = Array.isArray(ORDER) ? ORDER : []

// ---------- 断言 1：键集合与 PRIORITY_ORDER 完全一致
{
  const a = [...keys].sort()
  const b = [...order].sort()
  if (show(a) === show(b) && new Set(order).size === order.length) {
    ok(`键集合一致（${keys.length} 个键）`)
  } else {
    const missing = order.filter((k) => !keys.includes(k))
    const extra = keys.filter((k) => !order.includes(k))
    bad(
      `键集合不一致：PRIORITY_ORDER 有而 PRIORITY 缺 ${show(missing)}；` +
        `PRIORITY 有而 PRIORITY_ORDER 缺 ${show(extra)}；` +
        `期望 ${show(b)}，实际 ${show(a)}`,
    )
  }
}

// ---------- 断言 2：权重必须是 int 且互不相等（相等 = 优先级没定义清楚）
{
  const byValue = new Map()
  const nonInt = []
  for (const k of keys) {
    const v = PRIORITY[k]
    if (!Number.isInteger(v)) { nonInt.push(`${k}=${show(v)}`); continue }
    if (!byValue.has(v)) byValue.set(v, [])
    byValue.get(v).push(k)
  }
  if (nonInt.length) bad(`权重不是整数：${nonInt.join(', ')}`)
  else ok('权重都是整数')
  const dup = [...byValue.entries()].filter(([, ks]) => ks.length > 1)
  if (dup.length === 0) {
    ok('权重互不相等')
  } else {
    bad(
      `权重有重复（同时有效时无法判定归属）：` +
        dup.map(([v, ks]) => `${v} → ${show(ks)}`).join('；') +
        `；期望 6 个互不相同的整数，实际 ${show([...byValue.keys()].sort((x, y) => x - y))}`,
    )
  }
}

// ---------- 断言 3：按键权降序排序后的顺序 == PRIORITY_ORDER
{
  const sorted = [...keys].sort((x, y) => (PRIORITY[y] - PRIORITY[x]) || x.localeCompare(y))
  const same =
    sorted.length === order.length && sorted.every((k, i) => k === order[i])
  if (same) {
    ok(`权重降序 == PRIORITY_ORDER：${sorted.join(' > ')}`)
  } else {
    const firstDiff = sorted.findIndex((k, i) => k !== order[i])
    bad(
      `权重降序与 PRIORITY_ORDER 不同：第 ${firstDiff + 1} 位起分歧，` +
        `期望 ${show(order[firstDiff])}，实际 ${show(sorted[firstDiff])}；` +
        `期望 ${show(order)}，实际 ${show(sorted)}`,
    )
  }
}

// ---------- 断言 4：没有任何键的权重是负数
{
  const neg = keys.filter((k) => typeof PRIORITY[k] === 'number' && PRIORITY[k] < 0)
  if (neg.length === 0) {
    ok('没有负权重')
  } else {
    bad(
      `存在负权重：` +
        neg.map((k) => `${k}=${PRIORITY[k]}`).join(', ') +
        `；期望每项 >= 0，实际 ${show(PRIORITY)}`,
    )
  }
}

// ---------- 断言 5：config 自己的 PRIORITY_ORDER == 硬编码的权威顺序
// （断言 1 只证明两处常量集合一致；这条证明顺序本身没被改）
{
  const same = show(order) === show(EXPECTED_ORDER)
  if (same) {
    ok('PRIORITY_ORDER == 权威顺序')
  } else {
    bad(
      `PRIORITY_ORDER 偏离权威顺序：期望 ${show(EXPECTED_ORDER)}，实际 ${show(order)}`,
    )
  }
}

if (failures > 0) {
  console.log(`priority: ${failures} 条失败`)
  process.exit(1)
}
console.log('priority: ok')
process.exit(0)
