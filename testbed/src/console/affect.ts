/**
 * L3 情感/动机：四个标量 + 一个缓动。
 *
 * 边界（docs/safety.md 的硬约束）：
 *   - 情绪**只能**改"偏好类"参数：步速、视线灵敏度、待机频率。
 *   - 情绪**不得**改安全判定（遇障后退的触发距离由 L2 的常数决定，不看 fear）。
 *   - 所有量有上下限、有衰减，不会锁死。
 */
export class Affect {
  /** 0–1：被吓到（遇障/被打断时上升，缓慢回落） */
  fear = 0.05;
  /** 0–1：好奇（闲时上升，走路时下降；决定视线跟得多紧） */
  curiosity = 0.55;
  /** 0–1：疲劳（走路累积，停下回落） */
  fatigue = 0.1;

  tick(dt: number, walking: boolean): void {
    this.fear *= Math.exp(-dt / 6); // 半衰期 ~4 s
    const curiosityTarget = walking ? 0.3 : 0.65;
    this.curiosity += (curiosityTarget - this.curiosity) * Math.min(1, dt * 0.7);
    this.fatigue = clamp(this.fatigue + (walking ? 0.02 : -0.015) * dt);
  }

  onReflex(): void {
    this.fear = clamp(this.fear + 0.45);
  }

  onCommand(): void {
    this.curiosity = clamp(this.curiosity + 0.15);
  }

  /** 只调制"走多快"：0.55–1.0。安全阈值不在这里。 */
  speedScale(): number {
    return Math.max(0.55, 1 - 0.35 * this.fear - 0.25 * this.fatigue);
  }

  /** 视线跟随强度：好奇时看得很紧，怕的时候更敏感但更短 */
  gazeGain(): number {
    return 0.35 + 0.45 * this.curiosity + 0.2 * this.fear;
  }

  /** 舒适度：不是情绪本身，是给面板看的一个合成量 */
  comfort(pressure: number): number {
    return clamp(1 - 0.45 * pressure - 0.35 * this.fear - 0.2 * this.fatigue);
  }

  reset(): void {
    this.fear = 0.05;
    this.curiosity = 0.55;
    this.fatigue = 0.1;
  }
}

const clamp = (v: number) => Math.max(0, Math.min(1, v));
