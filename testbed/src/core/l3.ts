import * as P from './params.ts';
import type { Bus } from './bus.ts';
import type { BaselineCfg } from './baselines.ts';

/**
 * L3 情感/动机：情绪向量只调制**偏好**（速度上限、探索率），
 * 绝不进入 L2 的安全判定——见 docs/safety.md 的三条硬约束。
 */
export class L3 {
  fear = 0;
  curiosity = P.CURIOSITY_BASE;
  speedScale = 1;
  private lastEmit = -1;

  constructor(private readonly bus: Bus, private readonly cfg: BaselineCfg) {}

  tick(t: number, reflexFired: boolean): void {
    if (!this.cfg.l3) {
      this.speedScale = 1;
      return;
    }
    this.fear *= Math.exp(-P.DT / P.AFFECT_TAU);
    if (reflexFired) this.fear = Math.min(1, this.fear + P.FEAR_GAIN * 0.5);
    this.curiosity = P.CURIOSITY_BASE * (1 - 0.5 * this.fear);

    const mod = this.cfg.affectModulates ? 1 - 0.45 * this.fear : 1;
    this.speedScale = Math.max(0.55, mod);

    if (t - this.lastEmit >= 0.25) {
      this.lastEmit = t;
      this.bus.emit(t * 1000, 'L3', 'affect_vector', {
        decision_id: `l3_${Math.round(t * 1000)}`,
        fear: Math.round(this.fear * 1000) / 1000,
        curiosity: Math.round(this.curiosity * 1000) / 1000,
        speed_scale: Math.round(this.speedScale * 1000) / 1000,
      });
    }
  }
}
