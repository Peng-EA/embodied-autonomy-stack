import loadMujoco from '@mujoco/mujoco';
import { isBlocked, type Body, type BodyCommand, type BodyState } from './body.ts';

// G1-29DOF（MuJoCo menagerie / unitree_ros g1_29dof_rev_1_0，BSD-3-Clause）。
//
// 边界（写清楚，免得被当成"人形机器人仿真"）：
// - 底盘速度是**注入**的（每步写 qvel[0:2]），不是靠腿走出来；没有步态、没有平衡控制器。
// - 朝向**每步钉死**（等价于 Rapier 那边的锁旋转），所以不会倒。
// - 29 个关节用 MJCF 自带的位置执行器保持 stand 关键帧，能被动接受关节目标角。
// 也就是说：这一层现在和胶囊身体是同一水平的"运动学木偶 + 真关节"，
// 区别只在关节有真拓扑、真限位、真接触力。

const SUBSTEP = 0.002; // MJCF option timestep

export interface G1Part {
  /** MuJoCo 的 geom 下标：位姿直接取 geom_xpos / geom_xmat，别再自己拼 */
  geomId: number;
  mesh: string;
}

export interface G1Info {
  nq: number;
  nv: number;
  nu: number;
  nbody: number;
  ngeom: number;
  timestep: number;
  jointNames: string[];
}

/** 直接从 MuJoCo 编译结果里取网格：顶点已被它自己居中过，别再读 STL */
export interface G1Mesh {
  name: string;
  vert: Float32Array;
  face: Int32Array;
}

export interface G1Pose {
  geomXpos: Float64Array | number[];
  geomXmat: Float64Array | number[];
  ngeom: number;
}

export class G1Body implements Body {
  readonly kind = 'mujoco-humanoid' as const;
  readonly label = 'G1 · MuJoCo 29-DOF';
  readonly jointNames: string[];
  readonly parts: G1Part[];
  readonly info: G1Info;
  readonly meshes: G1Mesh[];

  private blocked = false;
  private t = 0;
  private yaw = 0;
  private x = 0;
  private z = 0;
  private speed = 0;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private constructor(private readonly m: any, private readonly model: any, private readonly data: any, private readonly jointAdr: number[]) {
    this.jointNames = this.jointAdr.map((_, i) => String(model.jnt(i + 1).name));
    this.info = {
      nq: model.nq,
      nv: model.nv,
      nu: model.nu,
      nbody: model.nbody,
      ngeom: model.ngeom,
      timestep: model.opt.timestep,
      jointNames: this.jointNames,
    };
    this.parts = collectParts(model);
    this.meshes = collectMeshes(model);
    this.reset(0, 0);
  }

  static async create(
    baseUrl: string,
    onProgress?: (done: number, total: number, what: string) => void,
  ): Promise<G1Body> {
    const m = await loadMujoco({ locateFile: (p: string) => baseUrl + p });

    onProgress?.(0, 1, 'manifest');
    const manifest = await (await fetch(baseUrl + 'g1/manifest.json')).json() as { meshes: string[] };
    const total = manifest.meshes.length;

    // Emscripten 虚拟文件系统：mj_loadXML 只认路径，所以先写进去
    m.FS.mkdir('/g1');
    m.FS.mkdir('/g1/assets');
    for (const f of ['room.xml', 'g1.xml']) {
      const txt = await (await fetch(baseUrl + 'g1/' + f)).text();
      m.FS.writeFile('/g1/' + f, txt);
    }
    for (let i = 0; i < total; i++) {
      const name = manifest.meshes[i];
      const buf = new Uint8Array(await (await fetch(baseUrl + 'g1/assets/' + name)).arrayBuffer());
      m.FS.writeFile('/g1/assets/' + name, buf);
      onProgress?.(i + 1, total, name);
    }

    const model = m.MjModel.mj_loadXML('/g1/room.xml');
    const data = new m.MjData(model);

    // 关节索引：qpos[0..6] 是 free joint，从 7 开始才是 29 个铰链
    const jointAdr: number[] = [];
    for (let j = 1; j < model.njnt; j++) {
      const adr = Number(model.jnt_qposadr[j]);
      if (adr > 6) jointAdr.push(adr);
    }
    const trimmedModel = model;
    return new G1Body(m, trimmedModel, data, jointAdr);
  }

  reset(x: number, z: number): void {
    this.m.mj_resetDataKeyframe(this.model, this.data, 0);
    this.m.mj_forward(this.model, this.data);
    this.x = x;
    this.z = z;
    this.yaw = 0;
    this.t = 0;
    this.blocked = false;
    this.writeBase(x, z, 0, this.yaw);
    this.m.mj_forward(this.model, this.data);
  }

  /** 把 three 坐标 + 朝向写回 free joint（qpos[0..6]） */
  private writeBase(x: number, z: number, y: number, yaw: number): void {
    const q = this.data.qpos;
    q[0] = x;
    q[1] = -z;
    q[2] = y;
    q[3] = Math.cos(yaw / 2);
    q[4] = 0;
    q[5] = 0;
    q[6] = Math.sin(yaw / 2);
  }

  step(dt: number, cmd: BodyCommand): BodyState {
    const beforeX = Number(this.data.qpos[0]);
    const beforeY = Number(this.data.qpos[1]);
    this.yaw = cmd.yaw;

    const steps = Math.max(1, Math.min(16, Math.round(dt / SUBSTEP)));
    for (let i = 0; i < steps; i++) {
      const v = this.data.qvel;
      v[0] = cmd.vx;
      v[1] = -cmd.vz;
      this.m.mj_step(this.model, this.data);
      // 钉住姿态（等价 Rapier 的 lockRotations）与角速度
      const q = this.data.qpos;
      q[3] = Math.cos(this.yaw / 2);
      q[4] = 0;
      q[5] = 0;
      q[6] = Math.sin(this.yaw / 2);
      v[3] = 0;
      v[4] = 0;
      v[5] = 0;
    }

    const afterX = Number(this.data.qpos[0]);
    const afterY = Number(this.data.qpos[1]);
    const want = Math.hypot(cmd.vx, cmd.vz) * dt;
    const got = Math.hypot(afterX - beforeX, afterY - beforeY);
    this.blocked = isBlocked(want, got);
    this.speed = dt > 0 ? got / dt : 0;
    this.x = afterX;
    this.z = -afterY;
    this.t += dt;
    return this.state();
  }

  state(): BodyState {
    const joints: Record<string, number> = {};
    for (let i = 0; i < this.jointAdr.length; i++) {
      joints[this.jointNames[i]] = Number(this.data.qpos[this.jointAdr[i]]);
    }
    return {
      t: this.t,
      x: this.x,
      y: Number(this.data.qpos[2]),
      z: this.z,
      yaw: this.yaw,
      speed: this.speed,
      blocked: this.blocked,
      joints,
    };
  }

  /** 渲染用：每个可视 geom 的世界位姿（MuJoCo 坐标，view 那边转 three） */
  pose(): G1Pose {
    return { geomXpos: this.data.geom_xpos, geomXmat: this.data.geom_xmat, ngeom: this.model.ngeom };
  }

  /** 调试用：直接设某个关节目标角（rad），验证"关节通道"是通的 */
  setJoint(name: string, angle: number): boolean {
    const i = this.jointNames.indexOf(name);
    if (i < 0) return false;
    this.data.ctrl[this.ctrlIndex[name] ?? i] = angle;
    return true;
  }

  private get ctrlIndex(): Record<string, number> {
    const map: Record<string, number> = {};
    for (let a = 0; a < this.model.nu; a++) map[String(this.model.actuator(a).name)] = a;
    return map;
  }
}

/**
 * 网格顶点取自模型本身（mesh_vert / mesh_face）。
 * MuJoCo 会把顶点按 mesh_pos/mesh_quat 居中，再用 geom_pos 补回去；
 * 直接读 STL 会把这个偏移算两遍——我第一版就是这么浮在半空的。
 */
function collectMeshes(model: any): G1Mesh[] {
  const V = model.mesh_vert;
  const F = model.mesh_face;
  const out: G1Mesh[] = [];
  for (let i = 0; i < model.nmesh; i++) {
    const va = Number(model.mesh_vertadr[i]);
    const vn = Number(model.mesh_vertnum[i]);
    const fa = Number(model.mesh_faceadr[i]);
    const fn = Number(model.mesh_facenum[i]);
    if (!vn || !fn) continue;
    const vert = new Float32Array(vn * 3);
    for (let k = 0; k < vn * 3; k++) vert[k] = Number(V[va * 3 + k]);
    const face = new Int32Array(fn * 3);
    let base = 0;
    for (let k = 0; k < fn * 3; k++) base = Math.max(base, Number(F[fa * 3 + k]));
    // 面索引有的是全局、有的是相对——按最大值判断，别猜
    const offset = base >= va && va > 0 ? va : 0;
    for (let k = 0; k < fn * 3; k++) face[k] = Number(F[fa * 3 + k]) - offset;
    out.push({ name: String(model.mesh(i).name), vert, face });
  }
  return out;
}

/**
 * 找出可视网格 geom（group 2）。group 3 是碰撞体，用同一份 STL，
 * 全画会重影；坐标也不该自己拼——geom_xpos/xmat 才是 MuJoCo 认定的位姿。
 */
function collectParts(model: any): G1Part[] {
  const parts: G1Part[] = [];
  const MESH = 7; // mjGEOM_MESH
  for (let g = 0; g < model.ngeom; g++) {
    const ax = model.geom(g);
    if (Number(ax.type) !== MESH) continue;
    if (Number(ax.group) !== 2) continue;
    const meshId = Number(ax.dataid);
    if (meshId < 0) continue;
    parts.push({ geomId: g, mesh: String(model.mesh(meshId).name) });
  }
  return parts;
}
