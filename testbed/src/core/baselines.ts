export interface BaselineCfg {
  id: string;
  label: string;
  /** L1 测量并广播（公平 B0 也保留：它得能读电量才能自己决定充电） */
  l1: boolean;
  /** L2 反射硬否决 */
  l2: boolean;
  /** L3 情绪层 */
  l3: boolean;
  /** 情绪是否真的调制行为 */
  affectModulates: boolean;
  /** L1 能否打断 L4 在途请求 */
  preemption: boolean;
  /** 收到过期计划怎么办 */
  stalePolicy: 'discard' | 'adopt';
  /** 复刻 v0.1 稻草人：电量钉死 */
  batteryPinned: boolean;
  note: string;
}

/**
 * v0.2 的唯一事实源（不写 YAML：这套栈是单语言，跨语言漂移正是 v0.1 需要 YAML 的原因）。
 * `bench --print-config` 会把生效开关写进每次运行的元数据。
 */
export const BASELINES: Record<string, BaselineCfg> = {
  full: {
    id: 'full',
    label: '本系统（全开）',
    l1: true,
    l2: true,
    l3: true,
    affectModulates: true,
    preemption: true,
    stalePolicy: 'discard',
    batteryPinned: false,
    note: '主张：分层 + 反射 + age 校验',
  },
  b0f: {
    id: 'b0f',
    label: '公平 B0（LLM 中心，可读电量）',
    l1: true,
    l2: false,
    l3: false,
    affectModulates: false,
    preemption: false,
    stalePolicy: 'adopt',
    batteryPinned: false,
    note: '同样的感知与信息，但闭环只由 LLM 计划闭合',
  },
  b1: {
    id: 'b1',
    label: 'B1（关情绪调制）',
    l1: true,
    l2: true,
    l3: true,
    affectModulates: false,
    preemption: true,
    stalePolicy: 'discard',
    batteryPinned: false,
    note: '情绪是不是装饰',
  },
  b2: {
    id: 'b2',
    label: 'B2（关 L1 抢占）',
    l1: true,
    l2: true,
    l3: true,
    affectModulates: true,
    preemption: false,
    stalePolicy: 'discard',
    batteryPinned: false,
    note: '抢占的净效应',
  },
  'b0-legacy': {
    id: 'b0-legacy',
    label: 'v0.1 稻草人 B0（电量钉死）',
    l1: false,
    l2: false,
    l3: false,
    affectModulates: false,
    preemption: false,
    stalePolicy: 'adopt',
    batteryPinned: true,
    note: '反面教材，不是证据：电量被钉在 30.0%',
  },
};

export function getBaseline(id: string): BaselineCfg {
  const b = BASELINES[id];
  if (!b) throw new Error(`未知基线：${id}（可选：${Object.keys(BASELINES).join(', ')}）`);
  return b;
}
