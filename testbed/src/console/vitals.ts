/**
 * L1 内感受：只是几个标量 + 100 Hz 积分。
 *
 * 这里故意不接任何"AI"：饿就是饿，渴就是渴。L1 的职责是**测**，不是**想**。
 * 阈值与速率都是演示值（写在 docs/testbed.md 的参数表里），真机要重新标定。
 */
export class Vitals {
  hunger = 0.15;
  thirst = 0.25;
  energy = 0.85;
  temperature = 36.6;

  /** 需求压力：三个需求里最强的那个。没有需求时为 0。 */
  pressure(): number {
    return Math.max(this.hunger, this.thirst, 1 - this.energy, 0);
  }

  dominantNeed(): 'hunger' | 'thirst' | 'energy' | 'none' {
    if (this.thirst >= 0.8 && this.thirst >= this.hunger) return 'thirst';
    if (this.hunger >= 0.8) return 'hunger';
    if (this.energy <= 0.2) return 'energy';
    return 'none';
  }

  tick(dt: number, moving: boolean, sitting: boolean): void {
    const idleH = 0.018;
    const moveH = 0.030;
    const idleT = 0.025;
    const moveT = 0.040;

    this.hunger = clamp(this.hunger + (moving ? moveH : idleH) * dt);
    this.thirst = clamp(this.thirst + (moving ? moveT : idleT) * dt);

    if (sitting) this.energy = clamp(this.energy + 0.05 * dt);
    else this.energy = clamp(this.energy - (moving ? 0.035 : 0.012) * dt);

    const target = 36.6 + (moving ? 0.45 : 0.05) + this.hunger * 0.15;
    this.temperature += (target - this.temperature) * Math.min(1, 0.6 * dt);
  }

  reset(): void {
    this.hunger = 0.15;
    this.thirst = 0.25;
    this.energy = 0.85;
    this.temperature = 36.6;
  }
}

const clamp = (v: number) => Math.max(0, Math.min(1, v));
