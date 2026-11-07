#!/usr/bin/env node
// 纯 Node 校验「一帧 geom 位姿 + manifest + STL」能不能拼出真的 G1：
// 不渲染、不开浏览器，只算世界矩阵与包围盒 —— 界面画的东西就是这批顶点。
//
// 用法（仓库根目录）：
//   node testbed/scripts/probe-mesh.mjs              # 连 sim_server（没起就自己 spawn）取一帧
//   node testbed/scripts/probe-mesh.mjs --frame f.json   # 用存下来的首帧（不需要服务端）
//   node testbed/scripts/probe-mesh.mjs --no-spawn --url ws://127.0.0.1:8788/sim
//
// 判据（不达标 exit 1）：
//   mesh_count       >= 30      真正摆出来的网格数（按网格名去重）
//   g1_bbox_height   1.30–1.36  整机包围盒高（m；mj 的 z = three 的 y）
//   另报告 head_top_y / geoms / missing / 全部网格 scale
//
// 为什么这三个数能证明数学没错（口径）：
//   - 不烘焙网格系（直接拿 STL 顶点的包围盒）→ 1.8373 m，脚陷地里 8.8 cm；
//   - 用 STL 包围盒中心顶替 MuJoCo 的 mesh_pos → 1.4048 m（超判据上限），
//     因为编译器算的是体积质心 + 主轴（left_ankle_roll_link 的 quat 离单位四元数 1.41）；
//   - 用首帧 geom.meshMeta 的 pos/quat/scale 烘焙 → 1.3228 m，与 MuJoCo mesh_vert 逐位一致。
// 所以 1.30–1.36 这一条不是「大概像个人」，是「网格系没算错」。
//
// 数学与浏览器共用 testbed/src/console/g1-mesh.ts（这里不复制一份）。

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { G1BodyWs, DEFAULT_WS_URL } from '../src/console/g1-body-ws.ts';
import {
  bakeMeshPositions,
  composeWorldBounds,
  parseStl,
  threeToMjMat,
  threeToMjPos,
} from '../src/console/g1-mesh.ts';
import { ROOT, ensureServer, killQuiet, wsCtor } from './lib/sim-server.mjs';

const G1_DIR = path.join(ROOT, 'testbed', 'public', 'mujoco', 'g1');
const LIMITS = { meshCount: 30, heightMin: 1.3, heightMax: 1.36 };

function parseArgs(argv) {
  const out = { url: DEFAULT_WS_URL, spawn: true, frame: null, json: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--url') out.url = String(argv[++i]);
    else if (a === '--frame') out.frame = String(argv[++i]);
    else if (a === '--no-spawn') out.spawn = false;
    else if (a === '--json') out.json = true;
    else if (a === '--help' || a === '-h') out.help = true;
  }
  return out;
}

function fmt(x, digits = 4) {
  return Number.isFinite(x) ? x.toFixed(digits) : 'NaN';
}

/** 取一帧（含 geom.mesh / geom.meshMeta）：优先 --frame，否则连服务端 */
async function loadFrame(args) {
  if (args.frame) {
    const raw = JSON.parse(readFileSync(args.frame, 'utf8'));
    const geom = raw?.geom ? raw.geom : raw?.snapshot?.geom;
    if (!geom) throw new Error('--frame 里没有 geom（要一条完整状态帧，含首帧的 meshMeta）');
    return { geom, source: 'frame=' + args.frame, child: null };
  }

  const server = await ensureServer(args.url, args.spawn);
  const ws = wsCtor();
  let body = null;
  try {
    body = await G1BodyWs.create(args.url, { WebSocketCtor: ws.ctor, rttEveryMs: 0 });
    const frame = body.frame();
    if (!frame?.geom) throw new Error('首帧没有 geom');
    return { geom: frame.geom, source: 'ws=' + args.url + ' tSim=' + frame.tSim, child: server.child };
  } catch (e) {
    killQuiet(server.child);
    throw e;
  } finally {
    body?.close();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('node testbed/scripts/probe-mesh.mjs [--url ws://127.0.0.1:8788/sim] [--no-spawn] [--frame f.json] [--json]');
    return 0;
  }

  const { geom, source, child } = await loadFrame(args);
  try {
    const names = geom.names ?? [];
    const meshOf = geom.mesh ?? [];
    const meta = geom.meshMeta ?? null;
    if (!meshOf.length || !meta) {
      throw new Error('这一帧没带 geom.mesh / geom.meshMeta（首帧才有；用 --frame 存首帧，或连 P0c 之后的服务端）');
    }
    const ngeom = Math.floor((geom.xpos ?? []).length / 3);
    if (!ngeom) throw new Error('这一帧没有 geom.xpos');

    const manifest = JSON.parse(readFileSync(path.join(G1_DIR, 'manifest.json'), 'utf8'));
    const files = manifest.meshFiles ?? {};

    // 服务端发的是 three 系（y 上），网格顶点是 mj 系（z 上）：翻回去，与 G1View 同一套
    const xpos = new Float64Array(ngeom * 3);
    const xmat = new Float64Array(ngeom * 9);
    for (let g = 0; g < ngeom; g++) {
      const p = g * 3;
      const [mx, my, mz] = threeToMjPos(geom.xpos[p], geom.xpos[p + 1], geom.xpos[p + 2]);
      xpos[p] = mx;
      xpos[p + 1] = my;
      xpos[p + 2] = mz;
      xmat.set(threeToMjMat(geom.xmat.slice(g * 9, g * 9 + 9)), g * 9);
    }

    const used = new Set();
    for (const name of meshOf) if (name) used.add(name);

    const meshes = new Map();
    const missing = [];
    let triangles = 0;
    let unitScale = true;
    for (const name of used) {
      const file = files[name];
      if (!file) {
        missing.push(name);
        continue;
      }
      let buf;
      try {
        buf = readFileSync(path.join(G1_DIR, 'assets', file));
      } catch {
        missing.push(name);
        continue;
      }
      const m = meta[name];
      if (!m) {
        missing.push(name + '（无 meshMeta）');
        continue;
      }
      for (const s of m.scale ?? [1, 1, 1]) if (Math.abs(s - 1) > 1e-9) unitScale = false;
      const parsed = parseStl(new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength));
      meshes.set(name, bakeMeshPositions(parsed.positions, m));
      triangles += parsed.triangles;
    }

    const b = composeWorldBounds(meshes, meshOf, xpos, xmat);
    // geoms 里含 59 个网格 geom；这里再报一遍「有多少块网格真的被摆到了世界系」
    const report = {
      mesh_count: b.meshCount,
      g1_bbox_height: b.height,
      head_top_y: b.headTopY,
      head_mesh: b.headMesh,
      geoms: b.geoms,
      mesh_geoms: ngeom,
      missing: missing.length,
      missing_names: missing.slice(0, 5),
      y_min: b.zMin,
      scale_all_1: unitScale,
      triangles,
      source,
      geom_names: names.slice(0, 3),
    };

    if (args.json) {
      console.log(JSON.stringify(report, null, 2));
    } else {
      console.log('mesh_count=' + b.meshCount);
      console.log('g1_bbox_height=' + fmt(b.height));
      console.log('head_top_y=' + fmt(b.headTopY));
      console.log(
        'head_mesh=' + (b.headMesh || 'n/a') +
          ' geoms=' + b.geoms + ' ngeom=' + ngeom +
          ' missing=' + missing.length + (missing.length ? '(' + missing.slice(0, 3).join(',') + ')' : '') +
          ' y_min=' + fmt(b.zMin) + ' triangles=' + triangles +
          ' scale_all_1=' + unitScale,
      );
      console.log('source=' + source);
    }

    const fails = [];
    if (!(b.meshCount >= LIMITS.meshCount)) fails.push('mesh_count ' + b.meshCount + ' < ' + LIMITS.meshCount);
    if (!(b.height >= LIMITS.heightMin && b.height <= LIMITS.heightMax)) {
      fails.push('g1_bbox_height ' + fmt(b.height) + ' 不在 ' + LIMITS.heightMin + '..' + LIMITS.heightMax);
    }
    if (missing.length) fails.push('缺 ' + missing.length + ' 块网格：' + missing.slice(0, 5).join(', '));
    if (!unitScale) fails.push('有网格 scale != 1（烘焙公式需要按 geom 维度缩放，先看 meshMeta）');
    if (fails.length) {
      for (const f of fails) console.error('FAIL: ' + f);
      return 1;
    }
    return 0;
  } finally {
    killQuiet(child);
  }
}

main().then(
  (code) => process.exit(code),
  (e) => {
    console.error('[probe-mesh] ' + (e instanceof Error ? e.stack || e.message : String(e)));
    process.exit(1);
  },
);
