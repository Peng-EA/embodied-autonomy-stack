// clearance 的几何数学：只吃数字，不 import three、不碰 DOM。
//
// 盒子真源是 sim/env/scene_room.json（serve-demo.mjs 原样转发，不在前端抄一份坐标）。
// 该文件自己写着 "frames.stored = physics.ts / Rapier / three：x 向右、y 向上、z 指向屏幕外"，
// 而 sim_server 的 state.x / state.z 也是同一套 three 系 —— 所以这里全是二维 xz 计算，
// 不需要任何坐标换算。
//
// 边界（不许含糊）：这是**给眼睛看的间隙提示**，不是碰撞判据。真实的接触/碰撞由
// sim_server 的 MuJoCo 权威环给（ncon / blocked），本文件刻意不去猜它。

export interface RoomBox {
  id: string;
  label: string;
  kind: string;
  /** 中心（three 系，m） */
  cx: number;
  cy: number;
  cz: number;
  /** 半长（m） */
  hx: number;
  hy: number;
  hz: number;
  collider: string;
}

export interface RayHit {
  /** 单位方向（three 系的 xz 平面） */
  dx: number;
  dz: number;
  /** 从原点沿该方向到最近盒子的距离（m）；没打到 = maxDist */
  dist: number;
  /** 打到的盒子 id；没打到 = null */
  id: string | null;
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : NaN);
const isVec3 = (v: unknown): v is number[] =>
  Array.isArray(v) && v.length === 3 && v.every((x) => Number.isFinite(x as number));

/** scene_room.json -> 盒子表。字段不对就跳过那一条并报出来，不猜。 */
export function parseSceneRoom(raw: unknown): { boxes: RoomBox[]; skipped: string[] } {
  const boxes: RoomBox[] = [];
  const skipped: string[] = [];
  const list = (raw as { boxes?: unknown })?.boxes;
  if (!Array.isArray(list)) return { boxes, skipped: ['boxes 不是数组'] };
  for (const item of list) {
    const b = item as Record<string, unknown>;
    const id = typeof b?.id === 'string' ? b.id : '';
    const center = b?.center;
    const half = b?.half;
    if (!id || !isVec3(center) || !isVec3(half)) {
      skipped.push(id || '(无 id)');
      continue;
    }
    boxes.push({
      id,
      label: typeof b.label === 'string' && b.label ? b.label : id,
      kind: typeof b.kind === 'string' ? b.kind : '',
      cx: num(center[0]),
      cy: num(center[1]),
      cz: num(center[2]),
      hx: Math.abs(num(half[0])),
      hy: Math.abs(num(half[1])),
      hz: Math.abs(num(half[2])),
      collider: typeof b.collider === 'string' ? b.collider : 'box',
    });
  }
  return { boxes, skipped };
}

/** 参与射线判定的盒子：地板（collider=plane）不挡路。 */
export function obstaclesOf(boxes: RoomBox[]): RoomBox[] {
  return boxes.filter((b) => b.collider !== 'plane');
}

/**
 * 二维射线 vs 轴对齐盒（盒按机器人半径 r 外扩一圈，等于"圆扫过"）。
 * slab 法；返回沿射线方向到盒面的距离（<= 0 表示起点就在盒内，按 0 报）。
 */
export function castRay(
  ox: number,
  oz: number,
  dx: number,
  dz: number,
  maxDist: number,
  boxes: RoomBox[],
  r: number,
): { dist: number; id: string | null } {
  let best = maxDist;
  let hit: string | null = null;
  for (const b of boxes) {
    const minX = b.cx - b.hx - r;
    const maxX = b.cx + b.hx + r;
    const minZ = b.cz - b.hz - r;
    const maxZ = b.cz + b.hz + r;
    let t0 = 0;
    let t1 = maxDist;
    // x 方向 slab
    if (Math.abs(dx) < 1e-9) {
      if (ox < minX || ox > maxX) continue;
    } else {
      let a = (minX - ox) / dx;
      let c = (maxX - ox) / dx;
      if (a > c) [a, c] = [c, a];
      t0 = Math.max(t0, a);
      t1 = Math.min(t1, c);
      if (t0 > t1) continue;
    }
    // z 方向 slab
    if (Math.abs(dz) < 1e-9) {
      if (oz < minZ || oz > maxZ) continue;
    } else {
      let a = (minZ - oz) / dz;
      let c = (maxZ - oz) / dz;
      if (a > c) [a, c] = [c, a];
      t0 = Math.max(t0, a);
      t1 = Math.min(t1, c);
      if (t0 > t1) continue;
    }
    if (t0 < best) {
      best = t0;
      hit = b.id;
    }
  }
  return { dist: Math.max(0, best), id: hit };
}

/** 射线的方位角偏移（度，相对机头）：0 是正前方。7 条，左右对称。 */
export const FAN_DEG = [0, -25, 25, -50, 50, -75, 75];

/**
 * 沿机头方向摊开一组射线。
 * 服务端口径：机头前向 = (cos yaw, −sin yaw)（见 g1-body-ws.ts 的 toBody）。
 */
export function clearanceFan(
  ox: number,
  oz: number,
  yaw: number,
  boxes: RoomBox[],
  r: number,
  maxDist = 3,
  degs: number[] = FAN_DEG,
): RayHit[] {
  return degs.map((deg) => {
    // 机头朝向 + 偏移：服务端口径 yaw 就是「相对世界 +x 的夹角」，前向 = (cos, −sin)。
    // 直接加到 yaw 上就够（偏移左右对称，符号不影响扇形形状）。
    const th = yaw + (deg * Math.PI) / 180;
    const ux = Math.cos(th);
    const uz = -Math.sin(th);
    const h = castRay(ox, oz, ux, uz, maxDist, boxes, r);
    return { dx: ux, dz: uz, dist: h.dist, id: h.id };
  });
}
