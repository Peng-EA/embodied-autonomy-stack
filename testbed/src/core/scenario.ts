export interface ObstacleCfg {
  enabled: boolean;
  /** 往复轴：'x' 横扫（横穿通道），'z' 纵扫 */
  axis: 'x' | 'z';
  /** 往复中心 */
  center: { x: number; z: number };
  /** 振幅（米） */
  amp: number;
  /** 往复周期（秒） */
  periodS: number;
  /** 半边长（米） */
  half: number;
  /** 相位（秒） */
  phaseS: number;
}

export interface Scenario {
  id: string;
  label: string;
  /** 墙：中心与半长（x 为横向、z 为纵向） */
  wall: { cx: number; cz: number; hx: number; hz: number };
  start: { x: number; z: number };
  charger: { x: number; z: number };
  horizonS: number;
  /** 目标点（充电桩）自身是否在动：这是"世界在计划与执行之间变了"的最小机制 */
  dock: { amp: number; periodS: number; axis: 'x' | 'z'; phaseS: number };
  obstacle: ObstacleCfg;
  /** 第一次规划是否故意给"天真直线"（复刻 v0.1 的必然触发反射） */
  naiveFirstPlan: boolean;
}

const WALL = { cx: 0, cz: 0, hx: 0.9, hz: 0.06 };

export const SCENARIOS: Record<string, Scenario> = {
  'stage1-static': {
    id: 'stage1-static',
    label: 'S1 · 静墙 + 充电桩（复刻 v0.1）',
    wall: WALL,
    start: { x: 0, z: -1.6 },
    charger: { x: 0, z: 1.6 },
    horizonS: 20,
    dock: { amp: 0, periodS: 6, axis: 'x', phaseS: 0 },
    obstacle: { enabled: false, axis: 'x', center: { x: 1.15, z: 0 }, amp: 0, periodS: 4, half: 0.18, phaseS: 0 },
    naiveFirstPlan: false,
  },
  'stage2-moving': {
    id: 'stage2-moving',
    label: 'S2 · 同一堵墙 + 会移动的充电桩（计划与执行之间世界变了）',
    wall: WALL,
    start: { x: 0, z: -1.6 },
    charger: { x: 0, z: 1.6 },
    horizonS: 25,
    dock: { amp: 0.9, periodS: 6, axis: 'x', phaseS: 0 },
    obstacle: { enabled: false, axis: 'x', center: { x: 1.15, z: 0 }, amp: 0.55, periodS: 4, half: 0.18, phaseS: 0 },
    naiveFirstPlan: false,
  },
};

/** 充电桩在 t 秒时的位置 */
export function dockPosition(sc: Scenario, t: number): { x: number; z: number } {
  const s = sc.dock.amp * Math.sin((2 * Math.PI * (t + sc.dock.phaseS)) / sc.dock.periodS);
  return {
    x: sc.charger.x + (sc.dock.axis === 'x' ? s : 0),
    z: sc.charger.z + (sc.dock.axis === 'z' ? s : 0),
  };
}

/** 绕行点：墙的右端外侧（确定性选择右侧，不用随机） */
export function gateWaypoints(sc: Scenario): { x: number; z: number }[] {
  const x = sc.wall.cx + sc.wall.hx + 0.55;
  return [
    { x, z: sc.wall.cz - 0.45 },
    { x, z: sc.wall.cz + 0.45 },
  ];
}
