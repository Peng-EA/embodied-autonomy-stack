import RAPIER from '@dimforge/rapier3d-compat';

/**
 * 房间物理：只有"一个胶囊 + 静态家具"。
 *
 * 边界：
 *   - 角色是**动态胶囊**（锁旋转），靠每帧 setLinvel 驱动；碰撞由 Rapier 解算。
 *   - 骨架动画与物理**解耦**：胶囊决定"人在哪"，骨骼决定"长什么样"。
 *     所以撞到桌子不会再穿模，但也不会被桌子"推倒"——因为角色不是刚体链。
 *   - 不用角色网格做碰撞：皮肤网格 2.7 万顶点进物理会卡死，也没必要。
 */
export const CAPSULE_R = 0.22;
export const CAPSULE_HALF = 0.5; // 圆柱段半高，总高 ≈ 2*(0.5+0.22) = 1.44 m

/**
 * 出生点（three 系：x 右、y 上、z 朝屏幕外）——**唯一来源**，别的地方一律 import，不许再抄这两位数。
 *
 * 只放水平分量：竖直是"胶囊站高"= CAPSULE_HALF + CAPSULE_R + 0.02，由物理常数推出来，
 * 写死成字面量就等于又多了一份数。
 *
 * 谁在读它：页面 main.ts（角色起点 / 中间·原地预设 / 重置按钮）、无头探针
 * probe-idle.mjs 与 probe-autonomy.mjs；scripts/check-scene-parity.mjs 机械断言
 * 下面的 setTranslation(...) 与它同值，且两者都与 sim/env/scene_room.json 的 agent.spawn 一致。
 *
 * 2026-11-08：从 (0, 0.4) 迁到 (1.3, 1.2) —— 旧点离边几只有 0.273 m（低于 L2 的 0.22 告警线附近）。
 */
export const SPAWN = { x: 1.3, z: 1.2 } as const;

export class RoomPhysics {
  private readonly world: any;
  private readonly body: any;
  private readonly eq: any;

  // 这里不用参数属性（constructor(private readonly world: any)）：Node 的 strip-only /
  // --experimental-strip-types 只接受可擦除语法，参数属性在 import 这一层就报错。
  // 无头探针要直接 import 本文件拿 SPAWN，所以保持显式字段 + 赋值。
  private constructor(world: any, body: any, eq: any) {
    this.world = world;
    this.body = body;
    this.eq = eq;
  }

  static async create(): Promise<RoomPhysics> {
    await RAPIER.init();
    const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
    world.timestep = 1 / 100;
    const eq = new RAPIER.EventQueue(true);
    const R = RAPIER;

    const fixed = (x: number, y: number, z: number, hx: number, hy: number, hz: number) => {
      const b = world.createRigidBody(R.RigidBodyDesc.fixed().setTranslation(x, y, z));
      world.createCollider(R.ColliderDesc.cuboid(hx, hy, hz).setFriction(0.6), b);
    };

    fixed(0, -0.05, 0, 8, 0.05, 8); // 地板
    fixed(0, 1.6, -4.06, 4, 1.6, 0.06); // 后墙
    fixed(-4.06, 1.6, 0, 0.06, 1.6, 4); // 左墙
    fixed(1.7, 0.36, -2.4, 0.75, 0.36, 0.35); // 桌
    fixed(-2.4, 0.22, 1.4, 0.8, 0.22, 1.0); // 床
    fixed(-1.9, 0.25, -1.0, 0.25, 0.25, 0.25); // 凳
    fixed(0.45, 0.16, -0.25, 0.34, 0.16, 0.26); // 矮桌
    fixed(-0.45, 0.16, 0.62, 0.18, 0.16, 0.18); // 边几
    fixed(2.9, 0.2, -3.2, 0.2, 0.2, 0.2); // 植物盆

    const body = world.createRigidBody(
      R.RigidBodyDesc.dynamic()
        .setTranslation(SPAWN.x, CAPSULE_HALF + CAPSULE_R + 0.02, SPAWN.z) // 出生点：水平来自 SPAWN，竖直=胶囊站高
        .lockRotations()
        .setLinearDamping(0.4)
        .setCcdEnabled(true),
    );
    world.createCollider(
      R.ColliderDesc.capsule(CAPSULE_HALF, CAPSULE_R).setFriction(0.1).setDensity(0.8),
      body,
    );

    return new RoomPhysics(world, body, eq);
  }

  /** dt 固定的 10 ms 步；调用方每帧只给"想要的水平速度"。 */
  step(vx: number, vz: number): void {
    const v = this.body.linvel();
    this.body.setLinvel({ x: vx, y: v.y, z: vz }, true);
    this.world.step(this.eq);
    this.eq.drainCollisionEvents(() => {});
  }

  pos(): { x: number; z: number } {
    const p = this.body.translation();
    return { x: p.x, z: p.z };
  }

  setPos(x: number, z: number): void {
    this.body.setTranslation({ x, y: CAPSULE_HALF + CAPSULE_R + 0.02, z }, true);
    this.body.setLinvel({ x: 0, y: 0, z: 0 }, true);
  }
}
