#!/usr/bin/env node
// sim/env/scene_room.json 是不是 physics.ts 的忠实副本 —— 机械比对，不一致 exit 1。
//
// 为什么需要它：场景的数值真源是 testbed/src/console/physics.ts 的 fixed(...)（Rapier 侧），
// sim/env/scene_room.json 是它的搬运副本（MuJoCo 侧；2026-11-08 从 sim_server/ 搬到底座目录：
// sim/ 更底层、也要读同一份房间，真源不能留在上层）。只要允许「两边各写一遍」，迟早分叉成两个房间。
// 这个脚本把「单一真源」从口头承诺变成一条能跑的命令：
//
//   node scripts/check-scene-parity.mjs
//
// 比对口径（少一条都不行）：
//   1. physics.ts 里每个 fixed(x, y, z, hx, hy, hz); // 名字  ->  scene_room.json 里同 label 的盒子
//      center(x,y,z) / half(hx,hy,hz) 逐字段比；
//   2. physics.ts 的 CAPSULE_R / CAPSULE_HALF -> scene_room.json 的 agent.capsule_r / capsule_half；
//   3. 双向：scene_room.json 多出来的盒子也算不一致（那等于凭空加家具）；
//   4. 条数对不上（少搬 / 多搬）直接失败；
//   5. 出生点唯一来源：physics.ts 的 .setTranslation(...) 水平分量必须是导出的 SPAWN.x / SPAWN.z
//      （或与 SPAWN 同值的数），两者逐字段比——防"常量改了、setTranslation 没跟"，也防又写回字面量；
//   6. 出生点净空：spawn(x, z) 到每个 box（地板除外）水平 AABB 的最小距离必须 >= 0.60 m。
//      这条不是"防抄错"，是防**选点**：出生就贴着家具时 L2 的 clearance 从 t=0 触发，
//      2026-11-08 那次 spawn=(0, 0.4) 到边几只有 0.273 m（实测起步 clearance 0.127 < 0.22）。
//      0.60 = 告警线 0.22 的 2.7 倍；不是阈值，别拿它去改 C.CLEARANCE_WARN。
//
// 退出码：0 = 全部一致；1 = 有差异（差异逐条打到 stderr）。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EPS = 1e-9;

function arg(flag, fallback) {
  const i = process.argv.indexOf(flag);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const physicsPath = path.resolve(REPO, arg('--physics', 'testbed/src/console/physics.ts'));
const scenePath = path.resolve(REPO, arg('--scene', 'sim/env/scene_room.json'));

const problems = [];
const fmt = (v) => (Number.isFinite(v) ? String(Number(v)) : String(v));

/** physics.ts 里的 fixed(...) 调用 -> [{ label, center:[3], half:[3], line }] */
function parsePhysicsFixed(source) {
  const out = [];
  const lines = source.split(/\r?\n/);
  // fixed(x, y, z, hx, hy, hz); // 名字
  // 定义行 `const fixed = (x: number, ...)` 里没有字面量数字，不会命中这条正则。
  const re =
    /^\s*fixed\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)\s*;\s*(?:\/\/\s*(.+?))?\s*$/;
  lines.forEach((raw, i) => {
    const m = raw.match(re);
    if (!m) return;
    const nums = m.slice(1, 7).map(Number);
    const label = (m[7] || '').trim();
    if (!label) {
      problems.push('physics.ts:' + (i + 1) + ' 的 fixed(...) 后面没有 "// 名字" 注释，无法和 scene_room.json 对齐');
    }
    out.push({ label, center: nums.slice(0, 3), half: nums.slice(3, 6), line: i + 1 });
  });
  return out;
}

/** physics.ts 的胶囊常数 */
function parseCapsule(source) {
  const grab = (name) => {
    const m = source.match(new RegExp('export const ' + name + ' = (-?\\d+(?:\\.\\d+)?)'));
    if (!m) problems.push('physics.ts 里找不到 export const ' + name);
    return m ? Number(m[1]) : NaN;
  };
  return { capsule_r: grab('CAPSULE_R'), capsule_half: grab('CAPSULE_HALF') };
}

/** physics.ts 的出生点唯一来源：export const SPAWN = { x: 1.3, z: 1.2 } as const */
function parseSpawn(source) {
  const m = source.match(
    /export const SPAWN\s*=\s*\{\s*x:\s*(-?\d+(?:\.\d+)?)\s*,\s*z:\s*(-?\d+(?:\.\d+)?)\s*,?\s*\}\s*as const/,
  );
  if (!m) {
    problems.push(
      'physics.ts 里找不到 export const SPAWN = { x, z } as const（出生点唯一来源；页面与无头探针都 import 它）',
    );
    return null;
  }
  return { x: Number(m[1]), z: Number(m[2]) };
}

function compareVec(label, field, got, want) {
  const keys = ['x', 'y', 'z'];
  for (let i = 0; i < 3; i += 1) {
    const a = got[i];
    const b = want[i];
    if (!Number.isFinite(a) || Math.abs(a - b) > EPS) {
      problems.push(
        label + '.' + field + '.' + keys[i] + ' 不一致：scene_room.json=' + fmt(a) + '  physics.ts=' + fmt(b),
      );
    }
  }
}

function compareNum(label, got, want) {
  if (!Number.isFinite(got) || Math.abs(got - want) > EPS) {
    problems.push(label + ' 不一致：scene_room.json=' + fmt(got) + '  physics.ts=' + fmt(want));
  }
}

// ---------- 读两边 ----------

if (!fs.existsSync(physicsPath)) {
  console.error('scene-parity: 找不到 ' + path.relative(REPO, physicsPath) + '（--physics 可指定别的）');
  process.exit(1);
}
if (!fs.existsSync(scenePath)) {
  console.error('scene-parity: 找不到 ' + path.relative(REPO, scenePath) + '（--scene 可指定别的）');
  process.exit(1);
}

const physicsSrc = fs.readFileSync(physicsPath, 'utf8');
const scene = JSON.parse(fs.readFileSync(scenePath, 'utf8'));

const fixed = parsePhysicsFixed(physicsSrc);
const capsule = parseCapsule(physicsSrc);
const boxes = Array.isArray(scene.boxes) ? scene.boxes : [];

if (fixed.length === 0) {
  problems.push('physics.ts 里一个 fixed(...) 都没解析到（正则与源码格式对不上了）');
}

// ---------- 逐条比 ----------

const byLabel = new Map(boxes.map((b) => [String(b.label || ''), b]));
for (const entry of fixed) {
  const box = byLabel.get(entry.label);
  if (!box) {
    problems.push('physics.ts:' + entry.line + ' 的 "' + entry.label + '" 在 scene_room.json 里没有对应盒子');
    continue;
  }
  compareVec(entry.label, 'center', Array.isArray(box.center) ? box.center.map(Number) : [], entry.center);
  compareVec(entry.label, 'half', Array.isArray(box.half) ? box.half.map(Number) : [], entry.half);
  byLabel.delete(entry.label);
}
for (const leftover of byLabel.values()) {
  problems.push('scene_room.json 的 "' + leftover.label + '"（id=' + leftover.id + '）在 physics.ts 里没有对应的 fixed(...)');
}

const agent = scene.agent || {};
compareNum('agent.capsule_r', Number(agent.capsule_r), capsule.capsule_r);
compareNum('agent.capsule_half', Number(agent.capsule_half), capsule.capsule_half);

// 出生点：physics.ts 的 .setTranslation(SPAWN.x, CAPSULE_HALF + CAPSULE_R + 0.02, SPAWN.z)
// 水平分量必须是 SPAWN 的 x/z（或与 SPAWN 同值的数），竖直分量必须是胶囊站高的表达式——
// 两条都是"只许有一份数"：竖直写死等于又抄一遍常数，水平写字面量等于 SPAWN 成了摆设。
// 这组数再从 SPAWN 搬进 scene_room.json 的 agent.spawn（下面接着比）。
const spawnConst = parseSpawn(physicsSrc);
const spawnMatch = physicsSrc.match(
  /\.setTranslation\(\s*(-?\d+(?:\.\d+)?|SPAWN\.x)\s*,\s*([^,]+?)\s*,\s*(-?\d+(?:\.\d+)?|SPAWN\.z)\s*\)/,
);
const spawn = agent.spawn || {};
/** setTranslation 的水平实参 -> 数值；写 SPAWN.x / SPAWN.z 就走常量，写字面量就按字面量比 */
const resolveTerm = (tok, axis) => {
  if (tok === 'SPAWN.x' || tok === 'SPAWN.z') return spawnConst ? spawnConst[axis] : NaN;
  return Number(tok);
};
if (!spawnMatch) {
  problems.push('physics.ts 里没解析到 .setTranslation(x, ..., z)（胶囊出生点）');
} else {
  const [, rawX, sy, rawZ] = spawnMatch;
  if (!/CAPSULE_HALF/.test(sy) || !/CAPSULE_R/.test(sy)) {
    problems.push('physics.ts 的出生点竖直分量不再是 CAPSULE_HALF + CAPSULE_R + ...（scene_room.json 的 y 是从它推的）');
  }
  const px = resolveTerm(rawX, 'x');
  const pz = resolveTerm(rawZ, 'z');
  // 断言：setTranslation(...) 与导出的 SPAWN 必须同值（防两处漂移 / 防又写回字面量）
  if (spawnConst) {
    if (!Number.isFinite(px) || Math.abs(px - spawnConst.x) > EPS) {
      problems.push(
        'physics.ts 的 setTranslation 横坐标与导出的 SPAWN.x 不一致：setTranslation=' + rawX +
          '  SPAWN=(' + fmt(spawnConst.x) + ', ' + fmt(spawnConst.z) + ')',
      );
    }
    if (!Number.isFinite(pz) || Math.abs(pz - spawnConst.z) > EPS) {
      problems.push(
        'physics.ts 的 setTranslation 纵坐标与导出的 SPAWN.z 不一致：setTranslation=' + rawZ +
          '  SPAWN=(' + fmt(spawnConst.x) + ', ' + fmt(spawnConst.z) + ')',
      );
    }
    if (rawX !== 'SPAWN.x' || rawZ !== 'SPAWN.z') {
      problems.push(
        'physics.ts 的 setTranslation 水平分量必须直接引用 SPAWN.x / SPAWN.z（当前是 ' + rawX + ', ' + rawZ +
          '）——字面量与 SPAWN 是两份数，迟早漂移',
      );
    }
  }
  compareNum('agent.spawn.x', Number(spawn.x), px);
  compareNum('agent.spawn.z', Number(spawn.z), pz);
}

// ---------- 出生点净空（>= SPAWN_CLEAR_MIN）----------
//
// 口径：spawn 在 stored 系（physics.ts / three：x 右、y 上、z 朝屏幕外）的 (x, z) 到每个 box
// 的 AABB 在 x/z 两轴上的水平距离，取最小值。地板（collider=plane）不算：它就在脚下，
// 不是"家具"。盒子全是轴对齐的，距离就是逐轴夹一下再取模，不需要几何库。
//
// 为什么这里还要算一遍（Python 侧的 g1_world --check 也打 spawn_clearance）：两处读的东西
// 不同——这边读 scene_room.json，那边读编译后的 MjModel 几何。谁跟场景分叉都会红，
// 而且这边不依赖 Python/venv，CI 里只跑 node 也能拦住"出生点又贴回家具"。
const SPAWN_CLEAR_MIN = 0.60;
let spawnClearance = Infinity;
let spawnNearest = null;
if (Number.isFinite(Number(spawn.x)) && Number.isFinite(Number(spawn.z))) {
  const sx = Number(spawn.x);
  const sz = Number(spawn.z);
  for (const box of boxes) {
    if (String(box.collider || 'box') === 'plane') continue; // 地板不是家具
    const c = Array.isArray(box.center) ? box.center.map(Number) : [];
    const h = Array.isArray(box.half) ? box.half.map(Number) : [];
    if (c.length !== 3 || h.length !== 3 || !c.every(Number.isFinite) || !h.every(Number.isFinite)) {
      problems.push('盒子 "' + box.label + '" 的 center/half 不是 3 个有限数，算不出出生点净空');
      continue;
    }
    const dx = Math.max(c[0] - h[0] - sx, 0, sx - (c[0] + h[0]));
    const dz = Math.max(c[2] - h[2] - sz, 0, sz - (c[2] + h[2]));
    const d = Math.hypot(dx, dz);
    if (d < spawnClearance) {
      spawnClearance = d;
      spawnNearest = box;
    }
  }
  if (spawnNearest === null) {
    problems.push('一个非地板的盒子都没读到，出生点净空无法校验');
  } else if (spawnClearance < SPAWN_CLEAR_MIN) {
    problems.push(
      '出生点净空不足：spawn=(' + fmt(sx) + ', ' + fmt(sz) + ') 到最近家具 "' +
        spawnNearest.label + '"（id=' + spawnNearest.id + '）只有 ' +
        spawnClearance.toFixed(3) + ' m < ' + SPAWN_CLEAR_MIN.toFixed(2) + ' m（出生就贴家具，L2 会从 t=0 触发；改出生点，不许改阈值）',
    );
  }
}

const compared = fixed.length * 2 + 7; // 每个盒子比 center+half，再加两个胶囊常数 + 出生点 x/z + SPAWN 同值（x/z）+ 出生点净空

// ---------- 结论 ----------

if (problems.length > 0) {
  console.error('scene-parity: FAIL —— ' + path.relative(REPO, scenePath) + ' 不是 ' + path.relative(REPO, physicsPath) + ' 的忠实副本');
  for (const item of problems) console.error('  - ' + item);
  console.error('  （改数只许改 physics.ts，再同步 scene_room.json；box=' + fixed.length + ' 条，比了 ' + compared + ' 项）');
  process.exit(1);
}

console.log(
  'scene-parity: OK —— boxes=' + fixed.length + ' + agent=2，共比对 ' + compared + ' 项，全部与 ' + path.relative(REPO, physicsPath) + ' 一致',
);
console.log(
  '  范围：' + fixed.map((f) => f.label).join(' / ') + '；胶囊 r/' + fmt(capsule.capsule_r) + ' half/' + fmt(capsule.capsule_half),
);
console.log(
  '  出生点：SPAWN=(' + fmt(spawnConst ? spawnConst.x : NaN) + ', ' + fmt(spawnConst ? spawnConst.z : NaN) + ')' +
    ' setTranslation=(' + (spawnMatch ? spawnMatch[1] + ', ' + spawnMatch[3] : 'n/a') + ')' +
    ' scene_room.json=(' + fmt(spawn.x) + ', ' + fmt(spawn.z) + ')_three 净空=' +
    spawnClearance.toFixed(3) + ' m（最近 "' + spawnNearest.label + '" id=' + spawnNearest.id +
    '，门槛 ' + SPAWN_CLEAR_MIN.toFixed(2) + ' m）',
);
process.exit(0);
