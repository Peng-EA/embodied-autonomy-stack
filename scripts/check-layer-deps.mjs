#!/usr/bin/env node
// 分层依赖护栏：sim/（底座）不许反向 import sim_server/（上层）。
//
// 为什么需要它（S0）：场景真源已经搬到 sim/env/scene_room.json —— sim/ 要读同一份房间，
// 而 sim/ 在依赖上更底层。真正的保证不是"大家记得别 import"，而是有人真写了那一行时机器会红：
//
//   node scripts/check-layer-deps.mjs
//
// 规则：sim/**/*.py 里出现 `import sim_server` / `from sim_server ...`（含 sim_server.xxx）
// 即违规。**注释与字符串不算** —— 只认真正的 import 语句（文档里写"不许 import sim_server"
// 不会被误判）。违者 exit 1，并把 文件:行 与那一行打到 stderr。
//
// 退出码：0 = 干净；1 = 有违规（或一个 .py 都没扫到，说明规则指错了目录）。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..');
const SKIP_DIRS = new Set(['.git', 'node_modules', '.venv', 'dist', 'logs', '__pycache__', '.tmp']);

// 规则表：dir 是受检的底座目录，banned 是它不许依赖的上层包（按顶层包名比，含子模块）
const RULES = [
  {
    dir: 'sim',
    banned: ['sim_server'],
    why: 'sim/ 是底座（Python 评价体系），依赖方向是 sim_server -> sim/env，不许反过来',
  },
];

/** 递归收集 dir 下的 .py（跳过缓存/依赖目录） */
function collectPython(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      collectPython(path.join(dir, entry.name), out);
    } else if (entry.name.endsWith('.py')) {
      out.push(path.join(dir, entry.name));
    }
  }
  return out;
}

/**
 * 把注释与字符串/文档字符串都抹掉，只留可执行代码（保留换行，行号对得上）。
 * 目的：`# 不许 from sim_server import x` 里那句话不触发规则，而真正的 import 一定触发。
 */
function stripCommentsAndStrings(src) {
  let out = '';
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === '#') {
      while (i < n && src[i] !== '\n') i += 1;
      continue;
    }
    if (c === '"' || c === "'") {
      const q = c;
      if (src.startsWith(q.repeat(3), i)) {
        // 三引号（文档字符串/多行字符串）：整段跳过，但保留其中的换行以维持行号
        i += 3;
        while (i < n && !src.startsWith(q.repeat(3), i)) {
          if (src[i] === '\\') i += 1;
          else if (src[i] === '\n') out += '\n';
          i += 1;
        }
        i += 3;
        continue;
      }
      // 单引号/双引号字符串：跳过内容；未闭合就按行尾结束（不吞后面的行）
      i += 1;
      while (i < n) {
        const d = src[i];
        if (d === '\\') { i += 2; continue; }
        if (d === q) { i += 1; break; }
        if (d === '\n') { out += '\n'; i += 1; break; }
        i += 1;
      }
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

/** 一行代码里的 import 来源（顶层包名）：from a.b import c / import a.b as d, e */
function importRoots(line) {
  const roots = [];
  const fromRe = /(?:^|[;:])\s*from\s+(\.*[A-Za-z_][\w.]*)\s+import\b/;
  const m = line.match(fromRe);
  if (m) {
    const mod = m[1].replace(/^\.+/, '');
    if (mod) roots.push(mod.split('.')[0]);
  }
  const importRe = /(?:^|[;:])\s*import\s+([^;]+)/;
  const mi = line.match(importRe);
  if (mi) {
    for (const rawPart of mi[1].split(',')) {
      const part = rawPart.trim().replace(/\s+as\s+[\w.]+$/, '');
      if (!part) continue;
      roots.push(part.split('.')[0]);
    }
  }
  return roots;
}

const rel = (p) => path.relative(REPO, p).split(path.sep).join('/');
const problems = [];
let scanned = 0;

for (const rule of RULES) {
  const dir = path.resolve(REPO, rule.dir);
  const files = collectPython(dir);
  if (files.length === 0) {
    problems.push({ file: rule.dir + '/', line: 0, text: '一个 .py 都没扫到 —— 规则指错目录了，护栏是假的' });
    continue;
  }
  for (const file of files) {
    scanned += 1;
    const code = stripCommentsAndStrings(fs.readFileSync(file, 'utf8'));
    code.split('\n').forEach((line, i) => {
      const roots = importRoots(line);
      const bannedRoot = roots.find((r) => rule.banned.includes(r));
      if (bannedRoot) {
        problems.push({ file: rel(file), line: i + 1, text: line.trim(), rule: rule.id ?? rule.dir, why: rule.why });
      }
    });
  }
}

if (problems.length > 0) {
  console.error('layer-deps: FAIL —— 依赖方向倒了');
  for (const p of problems) {
    const where = p.line ? p.file + ':' + p.line : p.file;
    console.error('  ' + where + '  ' + p.text);
  }
  for (const rule of RULES) {
    console.error('  规则：' + rule.dir + '/**/*.py 不许 import ' + rule.banned.join(' / ') + '（' + rule.why + '）');
    console.error('  读数：可读真源在 sim/env/scene_room.json，用法见 sim_server/scene.py 与 sim_server/README.md');
  }
  process.exit(1);
}

console.log(
  'layer-deps: OK —— 扫描 ' + scanned + ' 个 .py（' + RULES.map((r) => r.dir + '/**').join(', ') + '），' +
    '没有 import ' + RULES.flatMap((r) => r.banned).join(' / ') + '（注释/字符串不算）',
);
process.exit(0);
