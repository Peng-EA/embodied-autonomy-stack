import type { BodyCommand } from './body.ts';

// 上行指令闸门（P0d）：页面每帧到底发什么给身体，只从这里出。
//
// 三条不变量（页面与无头探针 testbed/scripts/probe-idle.mjs、probe-autonomy.mjs 共用同一份
// 实现，不复制逻辑）：
//   1. 用户没有真输入（键盘 / 点击 / 摇杆 / 聊天）之前，发出的 cmd 恒为 (0,0,0)。
//      —— 靠**显式授权标志**，不靠"数值刚好是 0"。
//   2. yaw 是**角速度**指令，按"目标朝向 − 当前真实朝向"闭环算（归一化 + 限幅）；
//      不转向（没在走）时恒为 0。首帧对齐（align）只用来钉零位、让静止时精确为 0。
//   3. 授权来源是**分开的两条通道**（P0e）：'user' 恒放行；'autonomy'（L4 自主计划）默认
//      **关**，只有 autonomyEnabled 为真（页面：URL ?auto=1 或 window.__allowAutonomy=true）
//      才生效。默认没开时 L4 想动也动不了——四层闭环要能开，但不能默认开。
//
// 被修掉的 bug（P0d）：上行 yaw 直接取 char.rotation.y - yawOffset，而页面为了构图
// 把 char 转了 π*0.18（≈0.565 rad）；服务端 MOVE_YAW_ENTER = 0.30，于是首帧就
// 进 POLICY —— 页面一打开机器人就开走。**显示朝向不是指令**，两者必须分开。
//
// 本文件不 import three / DOM：Node 探针能直接跑同一份逻辑。

/** 授权来源：人（键盘/点击/聊天/显式指令）或 L4 自主计划 */
export type AuthSource = 'user' | 'autonomy';

export interface CmdGateOptions {
  /**
   * 自主通道是否放行（默认 false）。
   * 用函数形式，是因为页面允许运行期打开（window.__allowAutonomy = true），
   * 而不是只能在模块加载那一刻读一次 URL。
   */
  autonomyEnabled?: boolean | (() => boolean);
}

export class CmdGate {
  /** 显式授权：见 authorize()。false = 上行恒零 */
  private enabled = false;
  /** 最近一次**成功**授权的来源（没授权过 = null） */
  private src: AuthSource | null = null;
  /** 自主通道被拒次数（诊断：L4 想动但没 ?auto=1 时 +1） */
  private deniedCount = 0;
  /** 首帧对齐做过没有（只做一次） */
  private alignedOnce = false;
  /** 指令朝向零位 = 首帧 state 的真实 yaw */
  private yawZero = 0;
  /** 想要的朝向（世界系；null = 不转向，yaw 指令恒 0） */
  private aim: number | null = null;
  /** 最近一帧真实 yaw（页面每帧喂进来；没喂过 = 首帧对齐值） */
  private stateYaw = 0;
  /** 最近一次真正交给身体的指令（诊断用；没发过 = null） */
  lastCommand: BodyCommand | null = null;
  /** 自主通道闸门（见 CmdGateOptions.autonomyEnabled） */
  private autonomyGate: boolean | (() => boolean);

  constructor(opts: CmdGateOptions = {}) {
    this.autonomyGate = opts.autonomyEnabled ?? false;
  }

  /**
   * 唯一的放行开关，按来源分流：
   *   'user'     —— 人真输入（键盘/点击/摇杆/聊天/显式指令），恒放行
   *   'autonomy' —— L4 自主计划，只在 autonomyEnabled 时放行，否则返回 false 且不改状态
   * 返回是否放行。被拒时闸门保持原状（不因为一次自主请求就把身体放出去）。
   */
  authorize(source: AuthSource): boolean {
    if (source === 'autonomy' && !this.autonomyEnabled) {
      this.deniedCount++;
      return false;
    }
    this.enabled = true;
    this.src = source;
    return true;
  }

  get authorized(): boolean {
    return this.enabled;
  }

  /** 授权来源：'user' / 'autonomy' / null（从没授权过） */
  get source(): AuthSource | null {
    return this.src;
  }

  /** 自主通道现在是否可用（默认 false） */
  get autonomyEnabled(): boolean {
    return typeof this.autonomyGate === 'function' ? this.autonomyGate() === true : this.autonomyGate === true;
  }

  /** 自主通道被拒过几次（诊断/探针用） */
  get denied(): number {
    return this.deniedCount;
  }

  /** 与首帧真实状态对齐过没有 */
  get aligned(): boolean {
    return this.alignedOnce;
  }

  /** 首帧真实 yaw 到手后调用一次；记下零位（probe-idle 的 first_cmd 判据就靠它） */
  align(stateYaw: number): void {
    if (this.alignedOnce) return;
    this.alignedOnce = true;
    this.yawZero = stateYaw;
    this.stateYaw = stateYaw;
  }

  /** 每帧喂一次真实 yaw：转向指令 = 目标朝向与**当前**朝向的差（不是首帧的差） */
  trackYaw(stateYaw: number): void {
    if (!Number.isFinite(stateYaw)) return;
    this.stateYaw = stateYaw;
  }

  /** 去某个朝向（世界系，按服务端口径：yaw = 机头相对世界 +x 的夹角） */
  turnTo(worldYaw: number): void {
    this.aim = worldYaw;
  }

  /**
   * 停止转向：yaw 指令精确为 0。
   * 不走路时必须这么做：否则"上一次转向留下的角度差"会一直挂着（实测 2.49 rad），
   * 服务端 |yaw| ≥ MOVE_YAW_EXIT 就永远退不出 POLICY（原地踏步、缓慢漂移）。
   */
  releaseTurn(): void {
    this.aim = null;
  }

  /**
   * 上行 yaw 是**角速度**指令（服务端 MOVE_YAW_ENTER = 0.30 rad/s 就是拿它比的），
   * 不是"目标角度 - 零位"。所以这里把角度差归一化后乘增益、再限幅：
   * 差了就转、对上了就是 0、不会在目标角度上来回冲（P0e 前的老写法把角度当角速度灌进去，
   * 实测会一路转到底、连转向带平移打转，最后撞上矮桌）。
   */
  private yawRate(): number {
    if (this.aim === null) return 0;
    const err = Math.atan2(
      Math.sin(this.aim - this.stateYaw),
      Math.cos(this.aim - this.stateYaw),
    );
    return Math.max(-YAW_RATE_MAX, Math.min(YAW_RATE_MAX, err * YAW_GAIN));
  }

  /** 这一帧真正要发出去的指令。没授权 = (0,0,0)，vx/vz 也不透传。 */
  command(vx: number, vz: number): BodyCommand {
    const cmd: BodyCommand = this.enabled
      ? { vx, vz, yaw: this.yawRate() }
      : { vx: 0, vz: 0, yaw: 0 };
    this.lastCommand = cmd;
    return cmd;
  }
}

/** 转向增益（1/s）与上限（rad/s）：上限低于服务端 0.30 会退不出 POLICY，选 0.5 留余量 */
const YAW_GAIN = 1.6;
const YAW_RATE_MAX = 0.5;
