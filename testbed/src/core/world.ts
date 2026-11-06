import RAPIER from '@dimforge/rapier3d-compat';
import * as P from './params.ts';
import type { Scenario } from './scenario.ts';

export interface Pose {
  x: number;
  z: number;
}

export interface Sense {
  /** 最近一条射线的命中距离（米）；无命中为 RAY_LEN */
  clearance: number;
  /** 最近障碍方向（单位向量，世界坐标 x/z） */
  dir: { x: number; z: number };
}

/**
 * Rapier 世界的薄包装。只做三件事：造场景、读感知、推固定步长。
 *
 * 边界（写进 docs/testbed.md）：底盘是**刚体但旋转锁定**、由速度直接驱动，
 * 不做悬挂/轮胎摩擦建模。本台账测的是时间尺度，不是底盘动力学保真度。
 */
export class World {
  private readonly sc: Scenario;
  private readonly world: any;
  private readonly eq: any;
  private readonly robot: any;
  private readonly obstacle?: any;
  private contactMax = 0;

  private constructor(sc: Scenario) {
    this.sc = sc;
    const R = RAPIER;
    this.world = new R.World({ x: 0, y: -9.81, z: 0 });
    this.world.timestep = P.PHYSICS_DT;
    this.eq = new R.EventQueue(true);

    const ground = this.world.createRigidBody(R.RigidBodyDesc.fixed().setTranslation(0, -0.05, 0));
    this.world.createCollider(R.ColliderDesc.cuboid(6, 0.05, 6), ground);

    const wall = this.world.createRigidBody(
      R.RigidBodyDesc.fixed().setTranslation(sc.wall.cx, 0.2, sc.wall.cz),
    );
    this.world.createCollider(R.ColliderDesc.cuboid(sc.wall.hx, 0.2, sc.wall.hz).setFriction(0.6), wall);

    if (sc.obstacle.enabled) {
      const ob = this.world.createRigidBody(
        R.RigidBodyDesc.kinematicPositionBased().setTranslation(
          sc.obstacle.center.x,
          sc.obstacle.half,
          sc.obstacle.center.z,
        ),
      );
      this.world.createCollider(
        R.ColliderDesc.cuboid(sc.obstacle.half, sc.obstacle.half, sc.obstacle.half),
        ob,
      );
      this.obstacle = ob;
    }

    this.robot = this.world.createRigidBody(
      R.RigidBodyDesc.dynamic()
        .setTranslation(sc.start.x, 0.121, sc.start.z)
        .lockRotations()
        .setLinearDamping(0.05),
    );
    this.world.createCollider(
      R.ColliderDesc.cuboid(0.18, 0.12, 0.25)
        .setFriction(0.2)
        .setActiveEvents(R.ActiveEvents.CONTACT_FORCE_EVENTS)
        .setContactForceEventThreshold(0.5),
      this.robot,
    );
  }

  static async create(sc: Scenario): Promise<World> {
    await RAPIER.init();
    return new World(sc);
  }

  get scenario(): Scenario {
    return this.sc;
  }

  pose(): Pose {
    const p = this.robot.translation();
    return { x: p.x, z: p.z };
  }

  speed(): number {
    const v = this.robot.linvel();
    return Math.hypot(v.x, v.z);
  }

  setVelocity(vx: number, vz: number): void {
    const v = this.robot.linvel();
    this.robot.setLinvel({ x: vx, y: v.y, z: vz }, true);
  }

  /** 12 条射线，从车体表面 0.185 m 起射——与 v0.1 同口径。 */
  sense(): Sense {
    const R = RAPIER;
    const p = this.robot.translation();
    let best = P.RAY_LEN;
    let bx = 0;
    let bz = 0;
    for (let i = 0; i < P.RAY_COUNT; i++) {
      const a = (i / P.RAY_COUNT) * Math.PI * 2;
      const dx = Math.cos(a);
      const dz = Math.sin(a);
      const ox = p.x + dx * P.RAY_START_R;
      const oz = p.z + dz * P.RAY_START_R;
      const ray = new R.Ray({ x: ox, y: 0.2, z: oz }, { x: dx, y: 0, z: dz });
      const hit: any = this.world.castRay(
        ray,
        P.RAY_LEN,
        true,
        undefined,
        undefined,
        undefined,
        this.robot,
      );
      if (hit) {
        const toi = Number(hit.timeOfImpact ?? hit.toi ?? 0);
        if (toi > 0 && toi < best) {
          best = toi;
          bx = dx;
          bz = dz;
        }
      }
    }
    return { clearance: best, dir: { x: bx, z: bz } };
  }

  obstaclePos(): Pose | null {
    if (!this.obstacle) return null;
    const p = this.obstacle.translation();
    return { x: p.x, z: p.z };
  }

  /** 移动障碍：正弦往复。t 用仿真时间（秒）。 */
  updateObstacle(t: number): void {
    if (!this.obstacle) return;
    const o = this.sc.obstacle;
    const s = o.amp * Math.sin((2 * Math.PI * (t + o.phaseS)) / o.periodS);
    const x = o.center.x + (o.axis === 'x' ? s : 0);
    const z = o.center.z + (o.axis === 'z' ? s : 0);
    this.obstacle.setNextKinematicTranslation({ x, y: o.half, z });
  }

  /** 推进 n 个物理子步；期间累计最大接触力。 */
  stepPhysics(n: number): void {
    for (let i = 0; i < n; i++) {
      this.world.step(this.eq);
      this.eq.drainContactForceEvents((e: any) => {
        const f = Number(e.totalForceMagnitude?.() ?? 0);
        if (f > this.contactMax) this.contactMax = f;
      });
    }
  }

  /** 取走本控制周期的最大接触力并清零。 */
  takeContactForce(): number {
    const f = this.contactMax;
    this.contactMax = 0;
    return f;
  }
}
