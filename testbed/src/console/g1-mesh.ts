// G1 网格合成：STL 解析 + MuJoCo 网格系烘焙 + 每帧世界矩阵。
//
// 这个文件**零依赖**（不引 three、不引 STL 库），因为浏览器与 node 探针
// （testbed/scripts/probe-mesh.mjs）必须跑同一份数学：探针量出来的包围盒就是界面画的
// 那堆顶点算出来的，不许两边各写一遍然后再去对数字。
//
// 坐标系约定（唯一一处定义）：
// - 服务端首帧的 geom.xpos/xmat 是 **three 系**（y 上，见 sim_server/world.py）。
// - MuJoCo 自己的网格顶点（mesh_vert）在 **mj 系**（z 上），烘焙结果也留在 mj 系，
//   与 G1View（container.rotation.x = -90°）配套。three 的 y 就是 mj 的 z。
//
// 烘焙公式（逐位对齐 MuJoCo 编译器的结果，实测包围盒一致）：
//     v_mesh = R(q)ᵀ · (v_stl · scale − pos)
// 其中 pos / quat / scale 是首帧 geom.meshMeta 给的网格系。**别拿 STL 包围盒中心去顶替
// pos**：MuJoCo 的 pos 是网格体积算出来的质心，quat 是主轴 —— 实测 pelvis 差 4 mm，
// left_ankle_roll_link 的 quat 离单位四元数 1.41（≈转 90°）。用包围盒中心顶替，整机包围盒
// 会从 1.3228 m 虚胖到 1.4048 m，脚还会陷进地里 8 cm（probe-mesh 的三个数就是量这个的）。

export interface MeshMeta {
  /** 网格系原点（mj 系），m */
  pos: number[];
  /** 网格系朝向，MuJoCo 四元数 (w, x, y, z) */
  quat: number[];
  /** 顶点缩放（本模型 35 个网格全是 1） */
  scale?: number[];
}

/** MuJoCo 系位姿（给 G1View.sync 吃的形状；与 g1-body.ts 的 G1Pose 结构兼容） */
export interface MjPose {
  geomXpos: Float64Array;
  geomXmat: Float64Array;
  ngeom: number;
}

export interface ParsedStl {
  /** 三角形顶点流，xyz 平铺，mj 系，未烘焙 */
  positions: Float32Array;
  /** 三角形数 */
  triangles: number;
}

export interface MeshBounds {
  /** 真正摆放出来的网格数（按网格名去重；MuJoCo 里 visual/collision 成对复用同一份） */
  meshCount: number;
  /** 带网格的 geom 个数 */
  geoms: number;
  /** mj z 的最小/最大值（= three 的 y，单位 m） */
  zMin: number;
  zMax: number;
  /** 整机包围盒高，m */
  height: number;
  /** 最高点（头顶）高度，= three 的 head_top_y */
  headTopY: number;
  /** 头顶是哪个网格 */
  headMesh: string;
}

// ---------------------------------------------------------------- STL

/**
 * 解析 STL（二进制与 ASCII 都认）。只用 STL 自带的三角形，不做索引合并 ——
 * G1 的 STL 本来就是三角汤（35 个文件、约 30 万顶点），合并索引省不了显存还添乱。
 */
export function parseStl(buf: ArrayBuffer | Uint8Array): ParsedStl {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  // 二进制判定：文件头 80 字节 + count(4) + 50·n。ASCII 的 "solid" 开头不可靠
  // （有些二进制 STL 也写 solid），只有字节数对得上才算二进制。
  if (bytes.byteLength >= 84) {
    const n = view.getUint32(80, true);
    if (84 + n * 50 === bytes.byteLength) {
      const positions = new Float32Array(n * 9);
      let off = 84;
      for (let t = 0; t < n; t++) {
        off += 12; // 跳过法线：法线交给渲染端重算，这里不存
        for (let k = 0; k < 9; k++) {
          positions[t * 9 + k] = view.getFloat32(off, true);
          off += 4;
        }
        off += 2; // 属性字节
      }
      return { positions, triangles: n };
    }
  }

  // ASCII：逐行取 "vertex x y z"。G1 的 STL 就是这种（"solid P2"）。
  const text = new TextDecoder().decode(bytes);
  const out: number[] = [];
  for (const line of text.split('\n')) {
    const t = line.trim().split(/\s+/);
    if (t.length === 4 && t[0] === 'vertex') {
      out.push(Number(t[1]), Number(t[2]), Number(t[3]));
    }
  }
  return { positions: new Float32Array(out), triangles: out.length / 9 };
}

// ---------------------------------------------------------------- 四元数 / 坐标变换

/** MuJoCo 四元数 (w, x, y, z) -> 3x3 行主序矩阵（与 mujoco.mju_quat2Mat 同向） */
export function quatToMat3(q: ArrayLike<number>): Float64Array {
  const w = q[0], x = q[1], y = q[2], z = q[3];
  return new Float64Array([
    1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
    2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
    2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
  ]);
}

/** 原始 STL 顶点 -> MuJoCo 网格系顶点（v = R(q)ᵀ · (v·scale − pos)） */
export function bakeMeshPositions(positions: Float32Array, meta: MeshMeta): Float32Array {
  const R = quatToMat3(meta.quat);
  const [px, py, pz] = meta.pos;
  const s = meta.scale ?? [1, 1, 1];
  const out = new Float32Array(positions.length);
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i] * s[0] - px;
    const y = positions[i + 1] * s[1] - py;
    const z = positions[i + 2] * s[2] - pz;
    // 乘 R 的转置：out[j] = Σ_k R[k][j] · v[k]
    out[i] = R[0] * x + R[3] * y + R[6] * z;
    out[i + 1] = R[1] * x + R[4] * y + R[7] * z;
    out[i + 2] = R[2] * x + R[5] * y + R[8] * z;
  }
  return out;
}

/** three 系位置 -> mj 系（x, -z, y）；p_three = A p_mj */
export function threeToMjPos(x: number, y: number, z: number): [number, number, number] {
  return [x, -z, y];
}

/** three 系旋转（9 元素行主序）-> mj 系：R_mj = Aᵀ R_three A（A = [[1,0,0],[0,0,1],[0,-1,0]]） */
export function threeToMjMat(m: ArrayLike<number>): Float64Array {
  // X = M·A：X[:,0]=M[:,0]、X[:,1]=−M[:,2]、X[:,2]=M[:,1]（A 的列是 e0、−e2、e1）
  // R_mj = Aᵀ·X：行 0 = X[0,:]，行 1 = −X[2,:]，行 2 = X[1,:]
  const x0 = m[0], x1 = -m[2], x2 = m[1];
  const x3 = m[3], x4 = -m[5], x5 = m[4];
  const x6 = m[6], x7 = -m[8], x8 = m[7];
  return new Float64Array([
    x0, x1, x2,
    -x6, -x7, -x8,
    x3, x4, x5,
  ]);
}

// ---------------------------------------------------------------- 合成

/**
 * 按「一帧 geom 位姿 + 网格顶点」算整机包围盒（mj 系）。
 *
 * 同一个网格可能被两个 geom 复用（visual group 2 与 collision group 3 位置一致，
 * 实测 24 对全等），所以按网格名去重，一块网格只摆一次 —— 这就是界面上真正画的节点数。
 */
export function composeWorldBounds(
  meshes: Map<string, Float32Array>,
  meshOfGeom: readonly string[],
  xpos: ArrayLike<number>,
  xmat: ArrayLike<number>,
): MeshBounds {
  let zMin = Infinity;
  let zMax = -Infinity;
  let headMesh = '';
  let geoms = 0;
  let meshCount = 0;
  const done = new Set<string>();

  for (let g = 0; g < meshOfGeom.length; g++) {
    const name = meshOfGeom[g];
    if (!name) continue;
    geoms++;
    if (done.has(name)) continue;
    const verts = meshes.get(name);
    if (!verts || verts.length === 0) continue;
    done.add(name);
    meshCount++;

    // mj 的 z 就是 three 的 y：只要世界系 z 的最小/最大值（包围盒与头顶）。
    const r = g * 9;
    const p = g * 3;
    const m20 = xmat[r + 6], m21 = xmat[r + 7], m22 = xmat[r + 8];
    const pz = xpos[p + 2];

    let best = -Infinity;
    for (let i = 0; i < verts.length; i += 3) {
      const wz = m20 * verts[i] + m21 * verts[i + 1] + m22 * verts[i + 2] + pz;
      if (wz < zMin) zMin = wz;
      if (wz > best) best = wz;
    }
    if (best > zMax) {
      zMax = best;
      headMesh = name;
    }
  }

  return {
    meshCount,
    geoms,
    zMin,
    zMax,
    height: zMax - zMin,
    headTopY: zMax,
    // 头顶就是世界系最高点：mj z 与 three y 同值
    headMesh,
  };
}
