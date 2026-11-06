import type { Cell } from './stats.ts';

const W = 760;
const H = 300;
const PAD = { l: 56, r: 18, t: 30, b: 42 };
const COLORS: Record<string, string> = { full: '#4ade80', b0f: '#f87171', b2: '#60a5fa', b1: '#fbbf24', 'b0-legacy': '#888' };

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function line(v: number, min: number, max: number, lo: number, hi: number): number {
  if (max - min < 1e-9) return (lo + hi) / 2;
  return hi - ((v - min) / (max - min)) * (hi - lo);
}

/** 纯手写 SVG：不引图表库，CI 里也能出图。 */
export function renderCurve(cells: Cell[], scenario: string, title: string): string {
  const cs = cells.filter((c) => c.scenario === scenario);
  const baselines = [...new Set(cs.map((c) => c.baseline))];
  const lats = [...new Set(cs.map((c) => c.latencyS))].sort((a, b) => a - b);
  const xmin = Math.min(...lats);
  const xmax = Math.max(...lats);
  const lo = PAD.l;
  const hi = W - PAD.r;
  const top = PAD.t;
  const bot = H - PAD.b;
  const x = (v: number) => (xmax === xmin ? (lo + hi) / 2 : lo + ((v - xmin) / (xmax - xmin)) * (hi - lo));

  const parts: string[] = [];
  parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="ui-monospace, monospace">`);
  parts.push(`<rect width="${W}" height="${H}" fill="#0b0f12"/>`);
  parts.push(`<text x="${lo}" y="20" fill="#e5e7eb" font-size="13">${esc(title)}</text>`);

  for (const f of [0, 0.25, 0.5, 0.75, 1]) {
    const y = line(f, 0, 1, top, bot);
    parts.push(`<line x1="${lo}" y1="${y.toFixed(1)}" x2="${hi}" y2="${y.toFixed(1)}" stroke="#1f2937"/>`);
    parts.push(`<text x="${lo - 8}" y="${(y + 4).toFixed(1)}" fill="#9ca3af" font-size="10" text-anchor="end">${Math.round(f * 100)}%</text>`);
  }
  for (const l of lats) {
    parts.push(`<text x="${x(l).toFixed(1)}" y="${bot + 18}" fill="#9ca3af" font-size="10" text-anchor="middle">${l}s</text>`);
  }
  parts.push(`<text x="${(lo + hi) / 2}" y="${H - 6}" fill="#6b7280" font-size="10" text-anchor="middle">LLM 延迟（仿真时间）</text>`);

  for (const b of baselines) {
    const pts = lats
      .map((l) => cs.find((c) => c.baseline === b && c.latencyS === l))
      .filter(Boolean) as Cell[];
    if (pts.length === 0) continue;
    const d = pts
      .map((c, i) => `${i === 0 ? 'M' : 'L'}${x(c.latencyS).toFixed(1)},${line(c.successRate, 0, 1, top, bot).toFixed(1)}`)
      .join(' ');
    parts.push(`<path d="${d}" fill="none" stroke="${COLORS[b] ?? '#ccc'}" stroke-width="2"/>`);
    for (const c of pts) {
      const cx = x(c.latencyS);
      const cy = line(c.successRate, 0, 1, top, bot);
      parts.push(
        `<line x1="${cx.toFixed(1)}" y1="${line(c.successCi[0], 0, 1, top, bot).toFixed(1)}" x2="${cx.toFixed(1)}" y2="${line(c.successCi[1], 0, 1, top, bot).toFixed(1)}" stroke="${COLORS[b] ?? '#ccc'}" stroke-width="1" opacity="0.5"/>`,
      );
      parts.push(`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3" fill="${COLORS[b] ?? '#ccc'}"/>`);
    }
    const lx = lo + baselines.indexOf(b) * 96;
    parts.push(`<rect x="${lx}" y="${H - 26}" width="10" height="3" fill="${COLORS[b] ?? '#ccc'}"/>`);
    parts.push(`<text x="${lx + 14}" y="${H - 24}" fill="#d1d5db" font-size="10">${esc(b)}</text>`);
  }
  parts.push('</svg>');
  return parts.join('\n');
}

/** 轨迹图：每个场景每个基线挑一条代表性运行，俯视。 */
export function renderTrajectories(runs: any[], scenario: string): string {
  const W = 760;
  const H = 360;
  const rs = runs.filter((r) => r.scenario === scenario && r.trace?.length);
  const xs = rs.flatMap((r) => r.trace.map((s: any) => s.x)).concat([-2, 2]);
  const zs = rs.flatMap((r) => r.trace.map((s: any) => s.z)).concat([-2, 2]);
  const xmin = Math.min(...xs);
  const xmax = Math.max(...xs);
  const zmin = Math.min(...zs);
  const zmax = Math.max(...zs);
  const pad = 30;
  const sx = (v: number) => pad + ((v - xmin) / (xmax - xmin || 1)) * (W - 2 * pad);
  const sz = (v: number) => H - pad - ((v - zmin) / (zmax - zmin || 1)) * (H - 2 * pad);
  const parts: string[] = [];
  parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="ui-monospace, monospace">`);
  parts.push(`<rect width="${W}" height="${H}" fill="#0b0f12"/>`);
  parts.push(`<text x="${pad}" y="20" fill="#e5e7eb" font-size="13">${esc(scenario)} · 代表性轨迹（俯视）</text>`);
  // 墙
  parts.push(`<rect x="${sx(-0.9)}" y="${sz(0.06)}" width="${sx(0.9) - sx(-0.9)}" height="${Math.max(2, sz(-0.06) - sz(0.06))}" fill="#374151"/>`);
  const seen = new Set<string>();
  for (const r of rs) {
    if (seen.has(r.baseline)) continue;
    seen.add(r.baseline);
    const d = r.trace
      .map((s: any, i: number) => `${i === 0 ? 'M' : 'L'}${sx(s.x).toFixed(1)},${sz(s.z).toFixed(1)}`)
      .join(' ');
    parts.push(`<path d="${d}" fill="none" stroke="${COLORS[r.baseline] ?? '#ccc'}" stroke-width="1.6" opacity="0.9"/>`);
  }
  parts.push('</svg>');
  return parts.join('\n');
}
