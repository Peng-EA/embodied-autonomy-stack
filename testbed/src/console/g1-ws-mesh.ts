import { G1View } from './g1-view.ts';
import { bakeMeshPositions, parseStl } from './g1-mesh.ts';
import type { G1Mesh, G1Part } from './g1-body.ts';
import type { G1BodyWs } from './g1-body-ws.ts';

// WS 档的 G1 网格：几何来自 testbed/public/mujoco/g1/（manifest.json 的 meshFiles，
// 35 个 STL），位姿来自 sim_server 首帧的 geom.meshMeta + 每帧 geom.xpos/xmat。
//
// 为什么这里能做、而 g1-view.ts 的头注释说「别另读 STL」：
// 那句话针对的是**没有网格系**的裸 STL（MuJoCo 会把顶点按质心/主轴重排，直接读会飘 0.4 m）。
// P0c 起服务端把网格系（pos/quat/scale）随首帧发过来，烘焙走的是 g1-mesh.ts 那一份公式，
// 与 MuJoCo 的 mesh_vert 逐位对齐 —— probe-mesh.mjs 用同一份数学量包围盒来证明这件事。
//
// 失败一律**显式报错**（调用方打 sys 事件、退回占位角色），不许静默什么都不画。

export interface G1WsMeshView {
  view: G1View;
  /** 真正画出来的网格数（按网格名去重） */
  meshCount: number;
  triangles: number;
  /** 整机包围盒高（m）；没有帧时 = 0 */
  height: number;
  /** 缺文件 / 缺 manifest 条目的网格名 */
  missing: string[];
  /** 下载的 STL 字节数 */
  bytes: number;
}

interface G1Manifest {
  model?: string;
  meshes?: string[];
  meshFiles?: Record<string, string>;
}

export async function buildG1WsView(
  baseUrl: string,
  body: G1BodyWs,
  onProgress?: (done: number, total: number, what: string) => void,
): Promise<G1WsMeshView> {
  const meta = body.meshMeta();
  if (!meta || !Object.keys(meta).length) {
    throw new Error('首帧没带 geom.meshMeta（P0c 起才有；服务端是不是旧版？）');
  }
  const meshOf = body.geomMesh();
  if (!meshOf.length) throw new Error('首帧没带 geom.mesh（geom -> 网格名映射）');

  onProgress?.(0, 1, 'manifest');
  const res = await fetch(baseUrl + 'g1/manifest.json');
  if (!res.ok) {
    throw new Error('manifest 取不到（HTTP ' + res.status + '）：先跑 node scripts/fetch-mujoco.mjs');
  }
  const manifest = (await res.json()) as G1Manifest;
  const files = manifest.meshFiles ?? {};
  if (!Object.keys(files).length) throw new Error('manifest.json 里没有 meshFiles');

  // 只下画面里真用得到的网格（59 个 geom 去重后 35 个）
  const names: string[] = [];
  const seen = new Set<string>();
  for (const name of meshOf) {
    if (!name || seen.has(name)) continue;
    seen.add(name);
    names.push(name);
  }

  const meshes: G1Mesh[] = [];
  const missing: string[] = [];
  let bytes = 0;
  for (let i = 0; i < names.length; i++) {
    const name = names[i];
    const file = files[name];
    if (!file) {
      missing.push(name);
      onProgress?.(i + 1, names.length, name + '（manifest 没这条）');
      continue;
    }
    const meshRes = await fetch(baseUrl + 'g1/assets/' + file);
    if (!meshRes.ok) {
      missing.push(name);
      onProgress?.(i + 1, names.length, file + '（HTTP ' + meshRes.status + '）');
      continue;
    }
    const buf = await meshRes.arrayBuffer();
    bytes += buf.byteLength;
    const parsed = parseStl(buf);
    const vert = bakeMeshPositions(parsed.positions, meta[name]);
    // G1View 要的是三角形索引流；STL 本来就是三角汤，顺序索引即可（不合并顶点）
    const face = new Int32Array(vert.length / 3);
    for (let k = 0; k < face.length; k++) face[k] = k;
    meshes.push({ name, vert, face });
    onProgress?.(i + 1, names.length, file);
  }
  if (!meshes.length) {
    throw new Error('一张网格都没取到（' + missing.slice(0, 3).join(', ') + '）');
  }

  // 一块网格一个节点，绑在**第一个**用它的 geom 上：visual(group 2) 与 collision(group 3)
  // 共用同一份 STL 且位姿全等（实测 24 对），画两遍只是重复阴影。
  const parts: G1Part[] = [];
  const used = new Set<string>();
  for (let g = 0; g < meshOf.length; g++) {
    const name = meshOf[g];
    if (!name || used.has(name) || !meshes.some((m) => m.name === name)) continue;
    used.add(name);
    parts.push({ geomId: g, mesh: name });
  }

  const view = G1View.build(parts, meshes);
  const pose = body.pose();
  const height = pose ? view.calibrate(pose) : 0;

  let triangles = 0;
  for (const m of meshes) triangles += m.face.length / 3;
  return { view, meshCount: meshes.length, triangles, height, missing, bytes };
}
