import * as P from './params.ts';
import type { Bus } from './bus.ts';
import type { BaselineCfg } from './baselines.ts';

/** L1 内感受/稳态：电量、温度、接触应力 → 广播。 */
export class L1 {
  battery = P.BATTERY_START;
  temp = 24;
  severity = 0;
  cause = '';
  broadcasts = 0;
  private lastSample = -1;

  constructor(private readonly bus: Bus, private readonly cfg: BaselineCfg) {}

  tick(t: number, speed: number, contact: number, charging: boolean): void {
    if (!this.cfg.l1) {
      if (this.cfg.batteryPinned) {
        // 复刻 v0.1：每控制周期 max(30, b - 0.02) —— 从 28% 起跑，第一步就被抬到 30%
        this.battery = Math.max(30.0, this.battery - 0.02);
      }
      this.severity = 0;
      return;
    }

    if (charging) {
      this.battery = Math.min(100, this.battery + P.BATTERY_CHARGE * P.DT);
    } else {
      const ratio = Math.min(1, speed / P.MAX_SPEED);
      this.battery -= (P.BATTERY_DRAIN_IDLE + P.BATTERY_DRAIN_MOVE * ratio) * P.DT;
    }
    this.battery = Math.max(0, this.battery);

    this.temp += ((24 + 14 * Math.min(1, speed / P.MAX_SPEED)) - this.temp) * 0.55 * P.DT;

    this.severity = 0;
    this.cause = '';
    if (this.battery < P.BATTERY_LOW) {
      this.severity = Math.max(this.severity, (P.BATTERY_LOW - this.battery) / P.BATTERY_LOW);
      this.cause = 'battery_low';
    }
    if (contact > P.STRESS_WARN) {
      this.severity = Math.max(this.severity, 1);
      this.cause = 'joint_stress';
    }

    if (t - this.lastSample >= P.SAMPLE_L1 - 1e-9) {
      this.lastSample = t;
      this.broadcasts++;
      this.bus.emit(t * 1000, 'L1', 'homeostatic_broadcast', {
        decision_id: `l1_${Math.round(t * 1000)}`,
        battery: round(this.battery),
        temp: round(this.temp),
        stress: round(contact),
        severity: round(this.severity),
        cause: this.cause || 'ok',
      });
    }
  }
}

const round = (v: number) => Math.round(v * 1000) / 1000;
