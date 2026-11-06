#!/usr/bin/env node
/**
 * 参数对账：params.ts 里标了 @mirror 的常量，必须和 sim/config.py 同名常量一致。
 *
 * 为什么需要：v0.1 的教训是"两处各抄一份数字"，最后没人知道哪个是真的。
 * 这里不生成代码（生成会把 Python 当事实源，反了），只做**报警**：
 * 不一致就红，让人决定是改镜像还是显式标成 @own。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '../..');
const PY = path.join(REPO, 'sim', 'config.py');
const TS = path.resolve(HERE, '../src/core/params.ts');

if (!fs.existsSync(PY)) {
  console.error('找不到 sim/config.py（在仓库根跑这个脚本）');
  process.exit(2);
}

const PY_RE = /^([A-Z][A-Z0-9_]*)\s*=\s*(-?[0-9.]+)/gm;
const py = new Map();
for (const m of fs.readFileSync(PY, 'utf8').matchAll(PY_RE)) py.set(m[1], Number(m[2]));

const TS_RE = /export const ([A-Z][A-Z0-9_]*)\s*=\s*(-?[0-9.]+)[^;]*;([^\n]*)/g;
const rows = [];
for (const m of fs.readFileSync(TS, 'utf8').matchAll(TS_RE)) {
  const [, name, val, tail] = m;
  if (!tail.includes('@mirror')) continue;
  if (tail.includes('@mirror*')) continue; // 显式放宽的派生值
  if (!py.has(name)) continue;
  rows.push({ name, ts: Number(val), py: py.get(name) });
}

let bad = 0;
for (const r of rows) {
  const ok = Math.abs(r.ts - r.py) < 1e-9;
  if (!ok) {
    bad++;
    console.error('MISMATCH ' + r.name + ': params.ts=' + r.ts + '  sim/config.py=' + r.py);
  }
}
console.log('参数对账：' + rows.length + ' 个 @mirror 常量，' + bad + ' 处不一致');
process.exit(bad === 0 ? 0 : 1);
