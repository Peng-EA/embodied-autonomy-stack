import type { BodyCommand } from './body.ts';

// 上行指令闸门（P0d）：页面每帧到底发什么给身体，只从这里出。
//
// 两条不变量（页面与无头探针 testbed/scripts/probe-idle.mjs 共用同一份实现，不复制逻辑）：
//   1. 用户没有真输入（键盘 / 点击 / 摇杆 / 聊天）之前，发出的 cmd 恒为 (0,0,0)。
//      —— 靠**显式授权标志**，不靠"数值刚好是 0"。
//   2. 首帧 state 到手后做一次对齐：把 yaw 指令的零位钉在真实 yaw 上。
//      上行 yaw = heading - yawZero，所以对齐之后静止时精确为 0。
//
// 被修掉的 bug（P0d）：上行 yaw 直接取 char.rotation.y - yawOffset，而页面为了构图
// 把 char 转了 π*0.18（≈0.565 rad）；服务端 MOVE_YAW_ENTER = 0.30，于是首帧就
// 进 POLICY —— 页面一打开机器人就开走。**显示朝向不是指令**，两者必须分开。
//
// 本文件不 import three / DOM：Node 探针能直接跑同一份逻辑。

export class CmdGate {
  /** 显式授权：见 authorize()。false = 上行恒零 */
  private enabled = false;
  /** 首帧对齐做过没有（只做一次） */
  private alignedOnce = false;
  /** 指令朝向零位 = 首帧 state 的真实 yaw */
  private yawZero = 0;
  /** 当前指令朝向（世界系，three 的 y 轴；0 = +Z） */
  private heading = 0;
  /** 最近一次真正交给身体的指令（诊断用；没发过 = null） */
  lastCommand: BodyCommand | null = null;

  /** 唯一的放行开关：用户真输入（键盘/点击/摇杆/聊天）或显式运动意图时调用 */
  authorize(): void {
    this.enabled = true;
  }

  get authorized(): boolean {
    return this.enabled;
  }

  /** 与首帧真实状态对齐过没有 */
  get aligned(): boolean {
    return this.alignedOnce;
  }

  /** 首帧真实 yaw 到手后调用一次；之后 yaw 指令都以它为 0 */
  align(stateYaw: number): void {
    if (this.alignedOnce) return;
    this.alignedOnce = true;
    this.yawZero = stateYaw;
    this.heading = stateYaw;
  }

  /** 走路/转向：世界系目标朝向（页面里是 atan2(dir.x, dir.z)） */
  turnTo(worldYaw: number): void {
    this.heading = worldYaw;
  }

  /** 这一帧真正要发出去的指令。没授权 = (0,0,0)，vx/vz 也不透传。 */
  command(vx: number, vz: number): BodyCommand {
    const cmd: BodyCommand = this.enabled
      ? { vx, vz, yaw: this.heading - this.yawZero }
      : { vx: 0, vz: 0, yaw: 0 };
    this.lastCommand = cmd;
    return cmd;
  }
}
