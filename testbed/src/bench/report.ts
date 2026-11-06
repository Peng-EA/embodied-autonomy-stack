#!/usr/bin/env tsx
/**
 * 由 runs.jsonl / traces.jsonl 重新生成 cells.json / summary.md / SVG。
 * 不重跑仿真——改图表或统计口径时用这个，几秒出结果。
 *
 *   npm run report -- --dir ../evals/v0.2/results/2026-11-06
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { SCENARIOS } from '../core/scenario.ts';
import { aggregate } from './stats.ts';
import { renderCurve, renderTrajectories } from './svg.ts';

function arg(name: string, dflt: string): string {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}

const dir = path.resolve(arg('--dir', '../evals/v0.2/results/2026-11-06'));
const readJsonl = (f: string) =>
  fs.existsSync(path.join(dir, f))
    ? fs.readFileSync(path.join(dir, f), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l))
    : [];

const runs = readJsonl('runs.jsonl');
const traces = readJsonl('traces.jsonl');
if (runs.length === 0) {
  console.error('runs.jsonl 为空：' + dir);
  process.exit(2);
}

const cells = aggregate(runs);
fs.writeFileSync(path.join(dir, 'cells.json'), JSON.stringify(cells, null, 2) + '\n');

const scenarios = [...new Set(cells.map((c) => c.scenario))];
for (const sc of scenarios) {
  const label = SCENARIOS[sc] ? SCENARIOS[sc].label : sc;
  fs.writeFileSync(path.join(dir, 'curve-' + sc + '.svg'), renderCurve(cells, sc, label + ' · 任务成功率 vs 延迟'));
  fs.writeFileSync(path.join(dir, 'trajectories-' + sc + '.svg'), renderTrajectories(traces, sc));
}

const L: string[] = [];
L.push('# v0.2 批跑汇总（自动生成）');
L.push('');
L.push('> 由 npm run report 从 runs.jsonl 生成，任何数字都不要手改。');
L.push('> 比例给 Wilson 95% 区间；均值区间只在 n≥8 时给（n<8 显示 —，不编区间）。');
L.push('');
L.push('| 场景 | 基线 | 延迟(s) | n | 成功 | 成功率 [95% CI] | 到达(s) [95% CI] | 碰撞 | 终电量 | 过期采纳 | 过期丢弃 | 降级 |');
L.push('|---|---|---|---|---|---|---|---|---|---|---|---|');
for (const c of cells) {
  const ci = (c.successCi[0] * 100).toFixed(0) + '–' + (c.successCi[1] * 100).toFixed(0) + '%';
  const reach = c.reachMeanS === null ? '—' : String(c.reachMeanS) + (c.reachCi ? ' [' + c.reachCi[0].toFixed(1) + '–' + c.reachCi[1].toFixed(1) + ']' : '');
  L.push('| ' + c.scenario + ' | ' + c.baseline + ' | ' + c.latencyS + ' | ' + c.n + ' | ' + c.success + '/' + c.n + ' | ' + (c.successRate * 100).toFixed(0) + '% [' + ci + '] | ' + reach + ' | ' + c.collisionsMean + ' | ' + c.batteryMean + '% | ' + c.staleAdoptedMean + ' | ' + c.staleDiscardedMean + ' | ' + c.degradedRuns + '/' + c.n + ' |');
}
fs.writeFileSync(path.join(dir, 'summary.md'), L.join('\n') + '\n');
console.log('已生成 ' + cells.length + ' 格 / ' + scenarios.length + ' 个场景 → ' + dir);
