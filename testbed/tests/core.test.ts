import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { Sim } from '../src/core/sim.ts';
import { Rng } from '../src/core/rng.ts';
import { wilson, meanCi } from '../src/bench/stats.ts';
import { BASELINES } from '../src/core/baselines.ts';

test('同 seed 两次运行状态哈希相同（虚拟时间确定性）', async () => {
  const a = (await Sim.create({ scenario: 'stage2-moving', baseline: 'full', latencyS: 0.7, seed: 3, horizonS: 6 })).run();
  const b = (await Sim.create({ scenario: 'stage2-moving', baseline: 'full', latencyS: 0.7, seed: 3, horizonS: 6 })).run();
  assert.equal(a.stateHash, b.stateHash);
});

test('不同 seed 会得到不同轨迹（否则种子没接进去）', async () => {
  const a = (await Sim.create({ scenario: 'stage2-moving', baseline: 'full', latencyS: 0.7, seed: 1, horizonS: 6 })).run();
  const b = (await Sim.create({ scenario: 'stage2-moving', baseline: 'full', latencyS: 0.7, seed: 2, horizonS: 6 })).run();
  assert.notEqual(a.stateHash, b.stateHash);
});

test('过期策略：full 丢弃、b0f 采纳（2 s 延迟下必然发生）', async () => {
  const full = (await Sim.create({ scenario: 'stage2-moving', baseline: 'full', latencyS: 2.0, seed: 1, horizonS: 12 })).run();
  const b0f = (await Sim.create({ scenario: 'stage2-moving', baseline: 'b0f', latencyS: 2.0, seed: 1, horizonS: 12 })).run();
  assert.ok(full.staleDiscarded > 0, 'full 应该丢弃过期计划');
  assert.ok(b0f.staleAdopted > 0, 'b0f 应该采纳过期计划');
});

test('基线开关是唯一事实源：b0f 没有反射、采纳过期', () => {
  assert.equal(BASELINES.b0f.l2, false);
  assert.equal(BASELINES.b0f.stalePolicy, 'adopt');
  assert.equal(BASELINES.full.stalePolicy, 'discard');
});

test('Rng 可复现且落在 [0,1)', () => {
  const a = new Rng(42);
  const b = new Rng(42);
  for (let i = 0; i < 100; i++) {
    const v = a.next();
    assert.equal(v, b.next());
    assert.ok(v >= 0 && v < 1);
  }
});

test('Wilson 区间：0/10 的上界不是 0，10/10 的下界不是 1', () => {
  const lo = wilson(0, 10);
  const hi = wilson(10, 10);
  assert.ok(lo[1] > 0.2 && lo[1] < 0.35);
  assert.ok(hi[0] > 0.65 && hi[0] < 0.8);
});

test('n<8 时不给均值区间（不编置信区间）', () => {
  assert.equal(meanCi([1, 2, 3]), null);
  assert.ok(meanCi([1, 2, 3, 4, 5, 6, 7, 8, 9]) !== null);
});

test('core 里不允许 Math.random', () => {
  const dir = path.resolve(import.meta.dirname, '../src/core');
  for (const f of fs.readdirSync(dir)) {
    const src = fs.readFileSync(path.join(dir, f), 'utf8');
    // 只禁真实调用；注释里提到这个名字不算
    assert.ok(!src.includes('Math.random('), f + ' 用了 Math.random()');
  }
});
