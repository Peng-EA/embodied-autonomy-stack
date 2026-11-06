import { Bus } from './bus.ts';
import { World } from './world.ts';
import { L1 } from './l1.ts';
import { L2 } from './l2.ts';
import { L3 } from './l3.ts';
import { L4 } from './l4.ts';
import { MockLLM, type LLMProvider } from './llm.ts';
import { getBaseline, type BaselineCfg } from './baselines.ts';
import { SCENARIOS, dockPosition, type Scenario } from './scenario.ts';
import { Rng } from './rng.ts';
import * as P from './params.ts';

export interface SimOptions {
  scenario: string;
  baseline: string;
  latencyS: number;
  seed: number;
  horizonS?: number;
  jitterS?: number;
  provider?: LLMProvider;
}

export interface Sample {
  t: number;
  x: number;
  z: number;
  battery: number;
  clearance: number;
  speed: number;
}

export interface RunResult {
  scenario: string;
  baseline: string;
  latencyS: number;
  seed: number;
  success: boolean;
  reachedAtS: number | null;
  finalBattery: number;
  minBattery: number;
  collisions: number;
  contacts: number;
  maxContactForce: number;
  staleAdopted: number;
  staleDiscarded: number;
  preemptions: number;
  planRequests: number;
  planAdoptions: number;
  reflexCount: number;
  degraded: boolean;
  degradeCount: number;
  controlTicks: number;
  stateHash: string;
  trace: Sample[];
}

const FNV_OFFSET = 2166136261;

function fnv1a(str: string): string {
  let h = FNV_OFFSET >>> 0;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

/** 一次运行 = 一个场景 + 一条基线 + 一个延迟 + 一个 seed。 */
export class Sim {
  readonly bus: Bus;
  readonly sc: Scenario;
  readonly cfg: BaselineCfg;
  readonly opts: SimOptions;

  private tick = 0;
  private finished = false;
  private success = false;
  private reachedAt: number | null = null;
  private collisions = 0;
  private contacts = 0;
  private wasColliding = false;
  private maxContactForce = 0;
  private minBattery = P.BATTERY_START;
  private trace: Sample[] = [];

  private constructor(
    readonly world: World,
    readonly l1: L1,
    readonly l2: L2,
    readonly l3: L3,
    readonly l4: L4,
    opts: SimOptions,
    private readonly horizonS: number,
    bus: Bus,
  ) {
    this.sc = world.scenario;
    this.cfg = getBaseline(opts.baseline);
    this.opts = opts;
    this.bus = bus;
  }

  static async create(opts: SimOptions): Promise<Sim> {
    const sc = SCENARIOS[opts.scenario];
    if (!sc) throw new Error(`未知场景：${opts.scenario}`);
    const cfg = getBaseline(opts.baseline);
    const world = await World.create(sc);
    const bus = new Bus();

    const rng = new Rng(opts.seed);
    const l1 = new L1(bus, cfg);
    const l2 = new L2(bus, cfg);
    const l3 = new L3(bus, cfg);
    const l4 = new L4(
      bus,
      cfg,
      sc,
      opts.provider ?? new MockLLM(),
      rng,
      opts.latencyS,
      opts.jitterS ?? 0.05,
    );
    return new Sim(world, l1, l2, l3, l4, { ...opts }, opts.horizonS ?? sc.horizonS, bus);
  }

  get t(): number {
    return this.tick * P.DT;
  }

  get done(): boolean {
    return this.finished;
  }

  step(): void {
    if (this.finished) return;
    const t = this.t;
    const pose = this.world.pose();
    const sense = this.world.sense();
    const contact = this.world.takeContactForce();
    if (contact > this.maxContactForce) this.maxContactForce = contact;

    // 碰撞只算"硬接触"：>20 N。5 N 级别的擦碰另记 contacts（见 result）。
    if (contact > 20.0 && !this.wasColliding) this.collisions++;
    if (contact > 5.0) this.contacts++;
    this.wasColliding = contact > 20.0;

    const speed = this.world.speed();
    const dock = dockPosition(this.sc, t);
    const charging = Math.hypot(pose.x - dock.x, pose.z - dock.z) < P.CHARGER_REACH;

    this.l1.tick(t, speed, contact, charging);
    this.minBattery = Math.min(this.minBattery, this.l1.battery);

    const reflex = this.l2.tick(t, sense, contact);
    if (reflex.firedThisTick) this.l4.notifyReflex();
    this.l3.tick(t, reflex.firedThisTick);

    const cmd = this.l4.tick(t, {
      pos: pose,
      battery: this.l1.battery,
      clearance: sense.clearance,
      severity: this.l1.severity,
      cause: this.l1.cause,
      dock,
    });

    const degradeScale = this.l4.degraded ? P.DEGRADE_SPEED_SCALE : 1;
    let vx = cmd.vx * this.l3.speedScale * degradeScale;
    let vz = cmd.vz * this.l3.speedScale * degradeScale;
    if (reflex.active) {
      vx = reflex.vx;
      vz = reflex.vz;
    }
    this.world.setVelocity(vx, vz);
    this.world.updateObstacle(t);
    this.world.stepPhysics(P.STEPS_PER_CONTROL);

    if (charging && this.reachedAt === null) {
      this.reachedAt = t;
      this.success = true;
    }
    if (this.l1.battery <= P.BATTERY_EMPTY) this.finished = true;

    if (this.tick % 10 === 0) {
      const p = this.world.pose();
      this.trace.push({
        t: Math.round(t * 1000) / 1000,
        x: Math.round(p.x * 1000) / 1000,
        z: Math.round(p.z * 1000) / 1000,
        battery: Math.round(this.l1.battery * 10) / 10,
        clearance: Math.round(this.world.sense().clearance * 1000) / 1000,
        speed: Math.round(this.world.speed() * 1000) / 1000,
      });
    }

    this.tick++;
    if (this.success || t >= this.horizonS) this.finished = true;
  }

  run(): RunResult {
    while (!this.finished) this.step();
    return this.result();
  }

  result(): RunResult {
    const p = this.world.pose();
    const hash = fnv1a(
      [
        Math.round(p.x * 1000),
        Math.round(p.z * 1000),
        Math.round(this.l1.battery * 1000),
        this.tick,
        this.collisions,
        this.l4.staleAdopted,
        this.l4.staleDiscarded,
      ].join('|'),
    );
    return {
      scenario: this.sc.id,
      baseline: this.cfg.id,
      latencyS: this.opts.latencyS,
      seed: this.opts.seed,
      success: this.success,
      reachedAtS: this.reachedAt === null ? null : Math.round(this.reachedAt * 1000) / 1000,
      finalBattery: Math.round(this.l1.battery * 10) / 10,
      minBattery: Math.round(this.minBattery * 10) / 10,
      collisions: this.collisions,
      contacts: this.contacts,
      maxContactForce: Math.round(this.maxContactForce * 10) / 10,
      staleAdopted: this.l4.staleAdopted,
      staleDiscarded: this.l4.staleDiscarded,
      preemptions: this.l4.preemptions,
      planRequests: this.l4.requests,
      planAdoptions: this.l4.adoptions,
      reflexCount: this.l2.count,
      degraded: this.l4.degraded,
      degradeCount: this.l4.degradeCount,
      controlTicks: this.tick,
      stateHash: hash,
      trace: this.trace,
    };
  }
}
