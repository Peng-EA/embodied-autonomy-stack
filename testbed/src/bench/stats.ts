export interface Cell {
  scenario: string;
  baseline: string;
  latencyS: number;
  n: number;
  success: number;
  successRate: number;
  successCi: [number, number];
  reachMeanS: number | null;
  reachCi: [number, number] | null;
  collisionsMean: number;
  batteryMean: number;
  staleAdoptedMean: number;
  staleDiscardedMean: number;
  degradedRuns: number;
}

/** Wilson 区间：比例的小样本区间，n 很小时比正态近似诚实。 */
export function wilson(k: number, n: number, z = 1.96): [number, number] {
  if (n === 0) return [0, 0];
  const p = k / n;
  const denom = 1 + (z * z) / n;
  const center = (p + (z * z) / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / denom;
  return [Math.max(0, center - half), Math.min(1, center + half)];
}

/** 均值的 95% 区间（正态近似，n>=8 才给；否则返回 null 而不是编一个） */
export function meanCi(xs: number[]): [number, number] | null {
  if (xs.length < 8) return null;
  const n = xs.length;
  const mean = xs.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(xs.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1));
  const half = (1.96 * sd) / Math.sqrt(n);
  return [mean - half, mean + half];
}

export function mean(xs: number[]): number {
  return xs.length === 0 ? 0 : xs.reduce((a, b) => a + b, 0) / xs.length;
}

export function aggregate(rows: any[]): Cell[] {
  const groups = new Map<string, any[]>();
  for (const r of rows) {
    const key = `${r.scenario}|${r.baseline}|${r.latencyS}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(r);
  }
  const cells: Cell[] = [];
  for (const [, rs] of groups) {
    const n = rs.length;
    const ok = rs.filter((r) => r.success).length;
    const reached = rs.filter((r) => r.reachedAtS !== null).map((r) => r.reachedAtS as number);
    cells.push({
      scenario: rs[0].scenario,
      baseline: rs[0].baseline,
      latencyS: rs[0].latencyS,
      n,
      success: ok,
      successRate: ok / n,
      successCi: wilson(ok, n),
      reachMeanS: reached.length ? Math.round(mean(reached) * 100) / 100 : null,
      reachCi: reached.length ? meanCi(reached) : null,
      collisionsMean: Math.round(mean(rs.map((r) => r.collisions)) * 100) / 100,
      batteryMean: Math.round(mean(rs.map((r) => r.finalBattery)) * 10) / 10,
      staleAdoptedMean: Math.round(mean(rs.map((r) => r.staleAdopted)) * 100) / 100,
      staleDiscardedMean: Math.round(mean(rs.map((r) => r.staleDiscarded)) * 100) / 100,
      degradedRuns: rs.filter((r) => r.degraded).length,
    });
  }
  cells.sort((a, b) => a.scenario.localeCompare(b.scenario) || a.latencyS - b.latencyS || a.baseline.localeCompare(b.baseline));
  return cells;
}
