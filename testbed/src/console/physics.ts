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

export class RoomPhysics {
  private constructor(
    private readonly world: any,
    private readonly body: any,
    private readonly eq: any,
  ) {}

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
        .setTranslation(0, CAPSULE_HALF + CAPSULE_R + 0.02, 0.4)
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
