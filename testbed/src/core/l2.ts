import * as P from './params.ts';
import type { Bus } from './bus.ts';
import type { BaselineCfg } from './baselines.ts';
import type { Sense } from './world.ts';

export interface ReflexOut {
  active: boolean;
  vx: number;
  vz: number;
  reason: string;
}

/** L2 反射：射线间隙 / 接触力 → 硬否决。不接受任何情绪量改写（docs/safety.md）。 */
export class L2 {
  private armed = true;
  private untilMs = -1;
  private cooldownUntil = -1;
  count = 0;
  firedThisTick = false;
  lastClearance = P.RAY_LEN;
  lastReason = '';

  constructor(private readonly bus: Bus, private readonly cfg: BaselineCfg) {}

  tick(t: number, sense: Sense, contact: number): ReflexOut {
    this.firedThisTick = false;
    this.lastClearance = sense.clearance;
    if (!this.cfg.l2) return { active: false, vx: 0, vz: 0, reason: '' };

    if (sense.clearance >= P.CLEARANCE_SAFE) this.armed = true;

    const stress = contact > P.STRESS_WARN;
    const near = sense.clearance < P.CLEARANCE_WARN;
    if ((stress || near) && this.armed && t * 1000 >= this.cooldownUntil) {
      this.armed = false;
      this.count++;
      this.firedThisTick = true;
      this.untilMs = t * 1000 + P.OVERRIDE_HOLD_MS;
      this.cooldownUntil = t * 1000 + P.REFLECT_COOLDOWN * 1000;
      this.lastReason = stress ? 'contact_stress' : 'clearance';
      this.bus.emit(t * 1000, 'L2', 'reflex_override', {
        decision_id: `l2_${this.count}`,
        reason: this.lastReason,
        clearance: Math.round(sense.clearance * 1000) / 1000,
        stress: Math.round(contact),
      });
    }

    // 迟滞：只要还有东西在 WARN 距离内，否决就继续生效（不是"推一下就走"）。
    // 否则机器人会反复贴上去——v0.1 的 0.25 s 固定保持在这里不够。
    const active = t * 1000 < this.untilMs || sense.clearance < P.CLEARANCE_WARN;
    // 对本栈的移动障碍，"停住等它过去"比"反向推一下"安全：
    // 反向推会把机器人顶进另一侧（墙 / 障碍的下一段），于是来回磨。
    return {
      active,
      vx: 0,
      vz: 0,
      reason: this.lastReason,
    };
  }
}
