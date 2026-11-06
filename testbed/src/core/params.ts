/**
 * v0.2 参数表。
 *
 * @mirror 与 sim/config.py 同名的常量——值必须一致，scripts/check-params.mjs 会对账。
 * @own    本栈自己的常量（场景/时序/统计口径），sim 里没有对应物。
 */

// ---- 时间
export const CONTROL_HZ = 100;              // @mirror
export const DT = 1 / CONTROL_HZ;
export const PHYSICS_DT = 0.002;            // @mirror  MuJoCo timestep 500 Hz
export const STEPS_PER_CONTROL = 5;         // @mirror*

// ---- 底盘
export const MAX_SPEED = 1.35;              // @mirror
export const DRIVE_KP = 1.6;                // @mirror
export const HOLD_RADIUS = 0.16;            // @mirror

// ---- L1 稳态
export const BATTERY_START = 28.0;          // @mirror
export const BATTERY_LOW = 25.0;            // @mirror
export const BATTERY_EMPTY = 5.0;           // @mirror
export const BATTERY_DRAIN_IDLE = 0.5;      // @mirror
export const BATTERY_DRAIN_MOVE = 2.6;      // @mirror
export const BATTERY_CHARGE = 9.0;          // @mirror
export const SAMPLE_L1 = 0.05;              // @mirror

// ---- 感知
export const RAY_COUNT = 12;                // @mirror
export const RAY_LEN = 2.5;                 // @mirror
export const RAY_START_R = 0.185;           // @mirror

// ---- L2 反射
export const CLEARANCE_WARN = 0.22;         // @mirror
export const CLEARANCE_SAFE = 0.40;         // @mirror
export const REFLECT_COOLDOWN = 0.8;        // @mirror
export const REFLEX_PUSH = 0.55;            // @mirror
export const STRESS_WARN = 85.0;            // @mirror
export const OVERRIDE_HOLD_MS = 250;        // @own  一次否决保持多久

// ---- L3 情绪
export const AFFECT_TAU = 1.6;              // @mirror
export const FEAR_GAIN = 0.85;              // @mirror
export const CURIOSITY_BASE = 0.42;         // @mirror

// ---- L4 规划
export const STALE_AGE_MS = 500;            // @own  v0.1 只有超时、没有 age 校验；这是 v0.2 的核心机制
export const PLAN_TIMEOUT_MS = 2000;        // @mirror* PLAN_TIMEOUT_S=1.2 → 放宽到 2 s 以覆盖 2 s 延迟档
export const DEGRADE_SPEED_SCALE = 0.85;    // @mirror

// ---- 场景
export const CHARGER_REACH = 0.55;          // @mirror
