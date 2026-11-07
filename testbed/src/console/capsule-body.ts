import type { RoomPhysics } from './physics.ts';
import { isBlocked, type Body, type BodyCommand, type BodyState } from './body.ts';

/**
 * 胶囊身体（Rapier）：一根动态胶囊 + 静态家具，锁旋转。
 * 这是 v0.2 以来一直在用的那个"身体"，包一层之后与 G1 同接口。
 */
export class CapsuleBody implements Body {
  readonly kind = 'rapier-capsule' as const;
  readonly label = '胶囊 · Rapier';
  readonly jointNames: string[] = [];
  private blocked = false;
  private t = 0;

  constructor(private readonly room: RoomPhysics) {}

  reset(x: number, z: number): void {
    this.room.setPos(x, z);
    this.blocked = false;
  }

  step(dt: number, cmd: BodyCommand): BodyState {
    const before = this.room.pos();
    this.room.step(cmd.vx, cmd.vz);
    const after = this.room.pos();
    const want = Math.hypot(cmd.vx, cmd.vz) * dt;
    const got = Math.hypot(after.x - before.x, after.z - before.z);
    this.blocked = isBlocked(want, got);
    this.t += dt;
    return {
      t: this.t,
      x: after.x,
      y: 0,
      z: after.z,
      yaw: cmd.yaw,
      speed: dt > 0 ? got / dt : 0,
      blocked: this.blocked,
      joints: {},
    };
  }

  state(): BodyState {
    const p = this.room.pos();
    return { t: this.t, x: p.x, y: 0, z: p.z, yaw: 0, speed: 0, blocked: this.blocked, joints: {} };
  }
}
