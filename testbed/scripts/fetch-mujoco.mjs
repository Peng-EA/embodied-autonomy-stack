#!/usr/bin/env node
// 把 G1（MuJoCo menagerie, BSD-3）与 MuJoCo wasm 准备到 public/mujoco/。
// 这两个目录不进仓库（体积 30 MB 量级），所以换机器要重跑一次：
//   node scripts/fetch-mujoco.mjs
// 数据来源优先级：仓库内 .assets/g1 → 通过代理下载 menagerie。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const outDir = path.join(root, 'public', 'mujoco');
const g1Dir = path.join(outDir, 'g1');
const localSrc = path.resolve(root, '..', '.assets', 'g1');
const MIRRORS = [
  'https://cdn.jsdelivr.net/gh/google-deepmind/mujoco_menagerie@main/unitree_g1',
  'https://gh-proxy.com/https://raw.githubusercontent.com/google-deepmind/mujoco_menagerie/main/unitree_g1',
];
const MESHES = fs.existsSync(localSrc)
  ? collectLocalMeshes()
  : [];

function collectLocalMeshes() {
  const dir = path.join(localSrc, 'assets');
  return fs.readdirSync(dir).filter((f) => f.toLowerCase().endsWith('.stl'));
}

const ROOM_XML = `<mujoco model="g1 room">
  <include file="g1.xml"/>
  <worldbody>
    <geom name="floor" type="plane" size="0 0 0.05"/>
    <geom name="wall_left"  type="box" pos="-4 0 1.5" size="0.05 4 1.5"/>
    <geom name="wall_back"  type="box" pos="0 4 1.5"  size="4 0.05 1.5"/>
    <geom name="wall_right" type="box" pos="4 0 1.5"  size="0.05 4 1.5"/>
    <geom name="wall_front" type="box" pos="0 -4 1.5" size="4 0.05 1.5"/>
  </worldbody>
</mujoco>
`;

async function fetchTo(url, dest) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(url + ' -> ' + res.status);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(dest, buf);
  return buf.length;
}

async function main() {
  fs.mkdirSync(path.join(g1Dir, 'assets'), { recursive: true });

  if (!fs.existsSync(localSrc)) {
    console.error('[fetch-mujoco] 找不到 ' + localSrc);
    console.error('[fetch-mujoco] 先拉 menagerie 的 unitree_g1（或手放一份）再重跑。');
    process.exit(1);
  }

  // MJCF 与网格
  for (const f of ['g1.xml', 'scene.xml']) {
    fs.copyFileSync(path.join(localSrc, f), path.join(g1Dir, f));
  }
  let copied = 0;
  for (const f of MESHES) {
    fs.copyFileSync(path.join(localSrc, 'assets', f), path.join(g1Dir, 'assets', f));
    copied++;
  }
  fs.writeFileSync(path.join(g1Dir, 'room.xml'), ROOM_XML);

  // wasm：从依赖里搬一份，免得 vite 处理二进制时踩坑
  const wasmSrc = path.join(root, 'node_modules', '@mujoco', 'mujoco', 'mujoco.wasm');
  if (!fs.existsSync(wasmSrc)) {
    console.error('[fetch-mujoco] 缺 @mujoco/mujoco，先 npm i @mujoco/mujoco');
    process.exit(1);
  }
  fs.copyFileSync(wasmSrc, path.join(outDir, 'mujoco.wasm'));

  // 清单：浏览器里没法列目录，而且 MuJoCo 去掉扩展名当 mesh 名，
  // 所以这里显式给出 stem → 文件 的映射（否则按名字拼路径会 404）
  // MJCF 里有的 mesh 带 name（与文件名不同，如 waist_yaw_link → waist_yaw_link_rev_1_0.STL），
  // 只按文件名拼路径会 404 到 index.html，解析成垃圾数据
  const meshFiles = {};
  const xml = fs.readFileSync(path.join(g1Dir, 'g1.xml'), 'utf8');
  for (const tag of xml.match(/<mesh\b[^>]*>/g) ?? []) {
    const file = /file="([^"]+)"/.exec(tag)?.[1];
    if (!file) continue;
    const name = /name="([^"]+)"/.exec(tag)?.[1] ?? file.replace(/\.stl$/i, '');
    meshFiles[name] = file;
  }
  fs.writeFileSync(
    path.join(g1Dir, 'manifest.json'),
    JSON.stringify({ model: 'unitree_g1', license: 'BSD-3-Clause', meshes: MESHES, meshFiles }, null, 2) + '\n',
  );

  const size = (p) => (fs.statSync(p).size / 1048576).toFixed(1) + ' MB';
  console.log('[fetch-mujoco] 模型 ' + copied + ' 个 STL · ' + size(path.join(g1Dir, 'assets')));
  console.log('[fetch-mujoco] wasm ' + size(path.join(outDir, 'mujoco.wasm')));
  console.log('[fetch-mujoco] 完成 → public/mujoco/（不进仓库）');
}

main().catch((e) => { console.error('[fetch-mujoco] 失败：' + e.message); process.exit(1); });
