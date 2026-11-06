#!/usr/bin/env tsx
/**
 * 批跑器：场景 x 基线 x 延迟 x seed -> runs.jsonl / cells.json / summary.md / *.svg
 *
 * 用虚拟时间（不睡墙钟），同一 seed 一定得到同一 stateHash。
 *
 *   npm run bench -- --scenario stage2-moving --seeds 20 \\
 *     --baselines full,b0f,b2 --latencies 0.1,0.35,0.7,1.2,2.0 \\
 *     --out ../evals/v0.2/results/2026-11-06
 *   npm run bench -- --quick
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { execFileSync } from 'node:child_process';
import { Sim } from '../core/sim.ts';
import { SCENARIOS } from '../core/scenario.ts';
import { getBaseline } from '../core/baselines.ts';
import { aggregate } from './stats.ts';
import { renderCurve, renderTrajectories } from './svg.ts';

function arg(name: string, dflt: string): string {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const has = (name: string) => process.argv.includes(name);

const quick = has('--quick');
const scenario = arg('--scenario', 'stage2-moving');
const baselines = arg('--baselines', quick ? 'full,b0f' : 'full,b0f,b2').split(',').map((s) => s.trim());
const latencies = arg('--latencies', quick ? '0.1,2.0' : '0.1,0.35,0.7,1.2,2.0').split(',').map(Number);
const seeds = Number(arg('--seeds', quick ? '2' : '20'));
const outDir = path.resolve(arg('--out', '../../.tmp/bench'));
const append = has('--append');

if (!SCENARIOS[scenario]) {
  console.error('未知场景：' + scenario + '（可选：' + Object.keys(SCENARIOS).join(', ') + '）');
  process.exit(2);
}
for (const b of baselines) getBaseline(b);

fs.mkdirSync(outDir, { recursive: true });

const startedAt = new Date().toISOString();
const rows: any[] = [];
const traces: any[] = [];
const total = baselines.length * latencies.length * seeds;
console.log('bench · ' + scenario + ' · ' + baselines.join('/') + ' · ' + latencies.length + ' 档延迟 · ' + seeds + ' seeds = ' + total + ' 次');

for (const baseline of baselines) {
  for (const latencyS of latencies) {
    let ok = 0;
    for (let seed = 1; seed <= seeds; seed++) {
      const sim = await Sim.create({ scenario, baseline, latencyS, seed });
      const r = sim.run();
      rows.push({ ...r, trace: undefined });
      if (r.success) ok++;
      if (seed === 1) traces.push({ scenario, baseline, latencyS, seed, trace: r.trace });
    }
    process.stdout.write('  ' + baseline.padEnd(4) + ' ' + String(latencyS).padEnd(5) + ' ' + ok + '/' + seeds + '\n');
  }
}

let allRows = rows;
if (append) {
  const prev = path.join(outDir, 'runs.jsonl');
  if (fs.existsSync(prev)) {
    const old = fs.readFileSync(prev, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
    const key = (r: any) => r.scenario + '|' + r.baseline + '|' + r.latencyS + '|' + r.seed;
    const seen = new Set(rows.map(key));
    allRows = [...old.filter((r: any) => !seen.has(key(r))), ...rows];
  }
}

let allTraces = traces;
if (append) {
  const prevT = path.join(outDir, 'traces.jsonl');
  if (fs.existsSync(prevT)) {
    const oldT = fs.readFileSync(prevT, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
    const keyT = (r: any) => r.scenario + '|' + r.baseline + '|' + r.latencyS + '|' + r.seed;
    const seenT = new Set(traces.map(keyT));
    allTraces = [...oldT.filter((r: any) => !seenT.has(keyT(r))), ...traces];
  }
}

const cells = aggregate(allRows);
fs.writeFileSync(path.join(outDir, 'runs.jsonl'), allRows.map((r) => JSON.stringify(r)).join('\n') + '\n');
fs.writeFileSync(path.join(outDir, 'traces.jsonl'), allTraces.map((r) => JSON.stringify(r)).join('\n') + '\n');
fs.writeFileSync(path.join(outDir, 'cells.json'), JSON.stringify(cells, null, 2) + '\n');

const meta: any = { startedAt, node: process.version, runs: allRows.length };
try {
  const pkg = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../../package.json'), 'utf8'));
  meta.rapier = pkg.dependencies['@dimforge/rapier3d-compat'];
} catch {}
try {
  meta.commit = execFileSync('git', ['rev-parse', '--short', 'HEAD'], { encoding: 'utf8' }).trim();
} catch {}
fs.writeFileSync(path.join(outDir, 'meta.json'), JSON.stringify(meta, null, 2) + '\n');

const scenarios = [...new Set(allRows.map((r) => r.scenario))];
for (const sc of scenarios) {
  const label = SCENARIOS[sc] ? SCENARIOS[sc].label : sc;
  const title = label + ' · 任务成功率 vs 延迟（n=' + seeds + '）';
  fs.writeFileSync(path.join(outDir, 'curve-' + sc + '.svg'), renderCurve(cells, sc, title));
  fs.writeFileSync(path.join(outDir, 'trajectories-' + sc + '.svg'), renderTrajectories(allTraces, sc));
}

const lines: string[] = [];
lines.push('# ' + scenario + ' · 批跑汇总（自动生成）');
lines.push('');
lines.push('> 由 npm run bench 生成，数字不要手改。n 是每格种子数；比例给 Wilson 95% 区间。');
lines.push('');
lines.push('| 基线 | 延迟(s) | n | 成功 | 成功率 [95% CI] | 到达(s) [95% CI] | 碰撞 | 终电量 | 过期采纳 | 降级 |');
lines.push('|---|---|---|---|---|---|---|---|---|---|');
for (const c of cells) {
  if (c.scenario !== scenario) continue;
  const ci = (c.successCi[0] * 100).toFixed(0) + '–' + (c.successCi[1] * 100).toFixed(0) + '%';
  const reach = c.reachMeanS === null ? '—' : String(c.reachMeanS) + (c.reachCi ? ' [' + c.reachCi[0].toFixed(1) + '–' + c.reachCi[1].toFixed(1) + ']' : '');
  lines.push('| ' + c.baseline + ' | ' + c.latencyS + ' | ' + c.n + ' | ' + c.success + '/' + c.n + ' | ' + (c.successRate * 100).toFixed(0) + '% [' + ci + '] | ' + reach + ' | ' + c.collisionsMean + ' | ' + c.batteryMean + '% | ' + c.staleAdoptedMean + ' | ' + c.degradedRuns + '/' + c.n + ' |');
}
fs.writeFileSync(path.join(outDir, 'summary.md'), lines.join('\n') + '\n');

console.log('');
console.log('产物：' + outDir);
console.log('  runs.jsonl / cells.json / summary.md / curve-*.svg / trajectories-*.svg');
