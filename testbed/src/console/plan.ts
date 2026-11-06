import type { Vitals } from './vitals.ts';

export interface Intent {
  kind: 'goto' | 'idle' | 'say';
  target?: { x: number; z: number };
  say: string;
  why: string;
}

export interface PlanOutcome {
  intent: Intent;
  /** 计划到达时，距离它基于的那一帧世界过了多久（毫秒） */
  ageMs: number;
  provider: string;
  latencyMs: number;
  stale: boolean;
}

const PLACES: Record<string, { x: number; z: number }> = {
  冰箱: { x: 1.05, z: -1.15 },
  水杯: { x: -1.25, z: 0.95 },
  床: { x: -0.95, z: -1.95 },
  桌边: { x: 0.6, z: -0.6 },
  窗边: { x: 0.0, z: 0.4 },
};

/** 超过这个年龄的计划不再执行（与 v0.2 的 STALE_AGE_MS 同口径） */
export const STALE_AGE_MS = 1500;

/**
 * L4 客户端：只做三件事——发请求、等结果、算年龄。
 * 服务不在线时用同一套规则本地降级，并把 provider 标成 local-fallback（不藏）。
 */
export class Planner {
  gen = 0;
  provider = '未连接';
  latencyMs = 0;
  lastAgeMs = 0;
  private base: string;

  constructor(base = 'http://127.0.0.1:8787') {
    this.base = base;
  }

  async request(
    v: Vitals,
    affect: { fear: number; curiosity: number; fatigue: number },
    simMs: number,
  ): Promise<PlanOutcome> {
    const gen = ++this.gen;
    const t0 = performance.now();
    let provider = 'server';
    let body: any = null;

    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 2600);
      const res = await fetch(this.base + '/plan', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        signal: ctl.signal,
        body: JSON.stringify({
          gen,
          requested_sim_time_ms: Math.round(simMs),
          state: {
            hunger: v.hunger,
            thirst: v.thirst,
            energy: v.energy,
            temperature: v.temperature,
            fear: affect.fear,
            curiosity: affect.curiosity,
            fatigue: affect.fatigue,
            pos: { x: 0, z: 0 },
          },
        }),
      });
      clearTimeout(timer);
      body = await res.json();
      provider = body.provider ?? 'server';
    } catch {
      body = null;
    }

    const latencyMs = Math.round(performance.now() - t0);
    if (!body) {
      provider = 'local-fallback';
      body = {
        intent: localRule(v, affect),
        latency_ms: latencyMs,
      };
    }

    const ageMs = Math.max(0, Math.round(simMs - (body.requested_sim_time_ms ?? simMs)) + latencyMs);
    this.provider = provider;
    this.latencyMs = latencyMs;
    this.lastAgeMs = ageMs;

    return {
      intent: body.intent,
      ageMs,
      provider,
      latencyMs,
      stale: ageMs > STALE_AGE_MS,
    };
  }
}

/** 服务端 rule_plan 的同款兜底（必须与服务端保持一致，改一处就要改两处——已知债） */
function localRule(v: Vitals, affect: { fear: number; curiosity: number; fatigue: number }): Intent {
  // 情绪进规划：累了先休息；怕了先退到床/角落；好奇值高且没需求时四处看看
  if (affect.fatigue > 0.75 && v.thirst < 0.85)
    return { kind: 'goto', target: PLACES.床, say: '我有点累了，去躺一下。', why: 'fatigue>0.75' };
  if (affect.fear > 0.7 && v.thirst < 0.85)
    return { kind: 'goto', target: PLACES.床, say: '刚才吓到了，我想退到角落里。', why: 'fear>0.7' };
  if (v.thirst >= 0.85 && v.thirst >= v.hunger)
    return { kind: 'goto', target: PLACES.水杯, say: '我渴了，去喝水。', why: 'thirst>0.85' };
  if (v.hunger >= 0.8)
    return { kind: 'goto', target: PLACES.冰箱, say: '肚子饿了，去冰箱看看。', why: 'hunger>0.8' };
  if (v.energy <= 0.2)
    return { kind: 'goto', target: PLACES.床, say: '有点撑不住了，去躺一会儿。', why: 'energy<0.2' };
  if (v.thirst >= 0.6)
    return { kind: 'goto', target: PLACES.水杯, say: '去喝口水。', why: 'thirst>0.6' };
  if (affect.curiosity > 0.7)
    return { kind: 'goto', target: PLACES.窗边, say: '去窗边看看。', why: 'curiosity>0.7' };
  return { kind: 'idle', say: '', why: '暂无需求' };
}
