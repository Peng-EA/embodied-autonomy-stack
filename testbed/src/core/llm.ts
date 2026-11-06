import { gateWaypoints, type Scenario } from './scenario.ts';

export interface PlanRequest {
  gen: number;
  /** 请求时刻（仿真毫秒）——执行器用它算 age */
  tMs: number;
  pos: { x: number; z: number };
  battery: number;
  clearance: number;
  /** 请求那一刻的目标点位置（世界在动，计划必须带"我是照着哪一刻的世界算的"） */
  dock: { x: number; z: number };
  /** 第一次规划给天真直线（复刻 v0.1 的必然触发反射） */
  naive: boolean;
}

export interface PlanResult {
  gen: number;
  requestedSimTime: number;
  waypoints: { x: number; z: number }[];
  note: string;
}

export interface LLMProvider {
  readonly id: string;
  /** 同步返回；真实 HTTP provider 应把结果塞进队列，由 tick 取（见 docs/testbed.md） */
  plan(req: PlanRequest, sc: Scenario): PlanResult;
}

/**
 * 离线 Mock：不是模型，是"规则规划器"。它看得见静态墙，看不见移动障碍的未来位置——
 * 这正是要测的：计划生成那一刻的世界，与执行那一刻的世界，差了多少。
 */
export class MockLLM implements LLMProvider {
  readonly id = 'mock';

  plan(req: PlanRequest, sc: Scenario): PlanResult {
    const charger = { x: req.dock.x, z: req.dock.z };
    if (req.naive) {
      return {
        gen: req.gen,
        requestedSimTime: req.tMs,
        waypoints: [charger],
        note: 'naive: 直线冲向充电桩',
      };
    }
    return {
      gen: req.gen,
      requestedSimTime: req.tMs,
      waypoints: [...gateWaypoints(sc), charger],
      note: 'competent-static: 绕墙右侧（对移动障碍无预测）',
    };
  }
}
