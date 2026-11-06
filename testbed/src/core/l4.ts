import * as P from './params.ts';
import type { Bus } from './bus.ts';
import { gateWaypoints, type Scenario } from './scenario.ts';
import type { BaselineCfg } from './baselines.ts';
import type { Rng } from './rng.ts';
import type { PlanResult, LLMProvider } from './llm.ts';

export interface Snapshot {
  pos: { x: number; z: number };
  battery: number;
  clearance: number;
  severity: number;
  cause: string;
  dock: { x: number; z: number };
}

interface Pending {
  gen: number;
  tMs: number;
  deliverAt: number;
  plan: PlanResult;
}

/**
 * L4 规划协调：异步请求 + 代际失效 + **age 校验**（v0.1 只有超时，没有这条）。
 * 执行器只认"还没过期的计划"，其余按 stalePolicy 处理。
 */
export class L4 {
  waypoints: { x: number; z: number }[] = [];
  gen = 0;
  requests = 0;
  adoptions = 0;
  staleAdopted = 0;
  staleDiscarded = 0;
  preemptions = 0;
  completed = false;
  /** L2 否决后：当前计划作废，下一次 tick 立刻带 avoid 上下文重规划（v0.1 的"挂起"） */
  notifyReflex(): void {
    this.waypoints = [];
    this.excited = true;
    this.lastRequestAt = -1e9;
  }

  /** 连续作废到降级阈值后，改由本地状态机接管（LLM 不可用的降级路径） */
  degraded = false;
  degradeCount = 0;
  private consecutiveDiscards = 0;

  private pending: Pending | null = null;
  private lastRequestAt = -1e9;
  private prevSeverity = 0;
  private excited = false;
  private patrolIdx = 0;

  constructor(
    private readonly bus: Bus,
    private readonly cfg: BaselineCfg,
    private readonly sc: Scenario,
    private readonly llm: LLMProvider,
    private readonly rng: Rng,
    private readonly latencyS: number,
    private readonly jitterS: number,
  ) {}

  private request(t: number, snap: Snapshot): void {
    const tMs = t * 1000;
    this.gen++;
    this.requests++;
    const naive = this.sc.naiveFirstPlan && !this.excited;
    const plan = this.llm.plan(
      {
        gen: this.gen,
        tMs,
        pos: snap.pos,
        battery: snap.battery,
        clearance: snap.clearance,
        dock: snap.dock,
        naive,
      },
      this.sc,
    );
    const latency = Math.max(0, this.latencyS + this.rng.normal(0, this.jitterS));
    this.pending = { gen: this.gen, tMs, deliverAt: tMs + latency * 1000, plan };
    this.lastRequestAt = t;
    this.bus.emit(tMs, 'L4', 'plan_request', {
      decision_id: `l4_${this.gen}`,
      gen: this.gen,
      latency_s: Math.round(latency * 1000) / 1000,
      naive,
    });
  }

  /** 返回期望速度（世界坐标），由 Sim 应用。 */
  tick(t: number, snap: Snapshot): { vx: number; vz: number } {
    const tMs = t * 1000;

    // 抢占：需求从"没有"变成"有"的那一刻，作废在途请求（只在开启抢占时）
    if (this.cfg.preemption && this.pending && this.prevSeverity === 0 && snap.severity > 0) {
      this.preemptions++;
      this.bus.emit(tMs, 'L4', 'preempt', {
        decision_id: `pre_${this.preemptions}`,
        gen: this.pending.gen,
        cause: snap.cause,
      });
      this.pending = null;
    }
    this.prevSeverity = snap.severity;

    if (this.pending && tMs >= this.pending.deliverAt) {
      const plan = this.pending.plan;
      const age = tMs - plan.requestedSimTime;
      if (this.cfg.stalePolicy === 'discard' && age > P.STALE_AGE_MS) {
        this.staleDiscarded++;
        this.bus.emit(tMs, 'L4', 'stale_command', {
          decision_id: `stale_${this.staleDiscarded}`,
          gen: plan.gen,
          age_ms: Math.round(age),
          policy: 'discard',
        });
        this.pending = null;
        this.lastRequestAt = t;
        this.consecutiveDiscards++;
        if (this.consecutiveDiscards >= 2 && !this.degraded) {
          this.degraded = true;
          this.degradeCount++;
          this.bus.emit(tMs, 'L4', 'degrade', {
            decision_id: `deg_${this.degradeCount}`,
            reason: 'plans_always_stale',
            latency_s: this.latencyS,
            policy_speed_scale: P.DEGRADE_SPEED_SCALE,
          });
        }
      } else {
        this.adoptions++;
        if (age > P.STALE_AGE_MS) this.staleAdopted++;
        this.waypoints = plan.waypoints.map((w) => ({ x: w.x, z: w.z }));
        this.pending = null;
        this.excited = true;
        this.consecutiveDiscards = 0;
        this.degraded = false;
        this.bus.emit(tMs, 'L4', 'plan_adopted', {
          decision_id: `adopt_${plan.gen}`,
          gen: plan.gen,
          age_ms: Math.round(age),
          stale: age > P.STALE_AGE_MS,
          note: plan.note,
        });
      }
    }

    const need = snap.battery < P.BATTERY_LOW;

    // 降级：不再问 LLM，直接盯"此刻"的目标点做闭环（v0.1 的 --llm-disconnect 路径）。
    // 关键差别：本地控制器看的是当前世界，不是 2 秒前的世界。
    if (this.degraded) {
      const gate = gateWaypoints(this.sc);
      const z = snap.pos.z;
      const cz = this.sc.wall.cz;
      // 单调状态机：用**位置**分档，不用"路点是否已消费"——后者每帧重建会被重置回起点
      // （已踩过的坑：机器人在 gate0 原地打转 25 秒）。
      if (z < cz - 0.5) {
        this.waypoints = [gate[0]];
      } else if (z < cz + 0.15) {
        this.waypoints = [gate[1]];
      } else {
        this.waypoints = [{ x: snap.dock.x, z: snap.dock.z }];
      }
    }

    if (!this.degraded && this.waypoints.length === 0 && !this.pending && need && t - this.lastRequestAt >= 0.5) {
      this.request(t, snap);
    }

    // 没有需求时来回巡逻（v0.1 的行为）：让电量持续下降，需求在 ~2.5 s 触发
    if (!need && !this.degraded && this.waypoints.length === 0 && !this.pending) {
      const z = this.sc.start.z + (this.patrolIdx % 2 === 0 ? 0.35 : -0.05);
      this.patrolIdx++;
      this.waypoints = [{ x: this.sc.start.x, z }];
    }

    if (this.waypoints.length === 0) return { vx: 0, vz: 0 };

    const wp = this.waypoints[0];
    const dx = wp.x - snap.pos.x;
    const dz = wp.z - snap.pos.z;
    const dist = Math.hypot(dx, dz);
    // 降级模式的目标每帧重算，"到达"不能靠消费路点——否则会在半径边界上每帧清零停住。
    if (!this.degraded && dist < P.HOLD_RADIUS) {
      this.waypoints.shift();
      if (this.waypoints.length === 0) this.completed = true;
      return { vx: 0, vz: 0 };
    }
    const speed = Math.min(P.MAX_SPEED, P.DRIVE_KP * dist);
    return { vx: (dx / dist) * speed, vz: (dz / dist) * speed };
  }
}
