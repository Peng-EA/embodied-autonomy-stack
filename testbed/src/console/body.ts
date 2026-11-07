// 身体契约：房间里"会动的东西"都实现这一份输入/输出。
//
// 目的：胶囊（Rapier）和 G1（MuJoCo）用同一套指令与状态，L1–L4、遥测、
// 反射判定只认 BodyState，不认具体引擎。换身体 = 换实现，不换上层代码。
//
// 坐标系：three 的右手系（y 上）。MuJoCo 侧（z 上）在适配器里转，不往上漏。

export interface BodyCommand {
  /** 世界系速度目标，m/s（three 的 x / z） */
  vx: number;
  vz: number;
  /** 期望朝向：three 的 y 轴旋转，弧度 */
  yaw: number;
}

export interface BodyState {
  /** 仿真秒（不是墙钟） */
  t: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  /** 实际水平速度，m/s */
  speed: number;
  /** 本帧"想走走不动"：物理给的真信号 */
  blocked: boolean;
  /** 关节名 → 角度；胶囊身体没有关节，返回空对象 */
  joints: Record<string, number>;
}

export interface Body {
  readonly kind: 'rapier-capsule' | 'mujoco-humanoid';
  readonly label: string;
  readonly jointNames: string[];
  reset(x: number, z: number): void;
  step(dt: number, cmd: BodyCommand): BodyState;
  state(): BodyState;
}

/** 撞上了 = 想要的速度没兑现。两个后端共用这一条判据。 */
export function isBlocked(want: number, got: number): boolean {
  return want > 0.002 && got < want * 0.35;
}
