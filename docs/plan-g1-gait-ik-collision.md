# G1 三件事：步态 / IK / 场景碰撞 —— 交接方案

> 状态：草案 · 最后更新 2026-11-07 · 面向：接手的新会话（不需要读之前对话）
> 基线提交：`918924d`（双身体契约 + G1 进演示台）。开工前先 `git pull`，确认 `git log -1` 是它或更新。

## 0. 五分钟上手

```bash
cd embodied-autonomy-stack/testbed
node scripts/fetch-mujoco.mjs     # 模型 30 MB + wasm 到 public/mujoco/（不进仓库，换机器要重跑）
npm run dev                       # http://localhost:5173/console.html
# 右上角「身体」按钮切 G1 / 鲸鱼娘；?body=capsule 强制胶囊
```

已实测（本机 Edge headless，别重新猜）：

| 项 | 数值 | 怎么来的 |
|---|---|---|
| 模型高 | 1.34 m | `G1View.calibrate()` 量包围盒 |
| 三角面 | 393k | 35 个可视 geom 求和 |
| 自由度 | nq 36 / nv 35 / nu 29 / geom 72 | `G1Body.info` |
| 加载 | 约 1 s（本机） | 页面日志 |
| 站立 | 6 仿真秒不摔（rootZ 0.790→0.7916） | Node 探针 3000 步 |
| 移动 | goto 1.9 m / 约 5 s | 浏览器 `__state()` 前后对比 |

## 1. 现状锚点

### 1.1 文件职责

| 文件 | 管什么 | 改它要注意 |
|---|---|---|
| `src/console/body.ts` | `BodyCommand` / `BodyState` / `Body` 合同 + `isBlocked` | **上层的唯一接口**，加字段要同步两个后端 |
| `src/console/capsule-body.ts` | Rapier 胶囊实现（鲸鱼娘那一档） | 行为不要变，它是对照组 |
| `src/console/g1-body.ts` | MuJoCo 加载、步进、钉姿态、29 关节读写 | 坐标转换与 pin 都在这 |
| `src/console/g1-view.ts` | G1 渲染（geom 级） | 几何只能取模型数组，别读 STL |
| `src/console/physics.ts` | Rapier 房间与家具（**手写坐标**） | 第 3 条工作线要把它改成读 `scene.ts` |
| `src/console/main.ts` | 场景、L1/L3/L4、动作、相机、切换 | 运动段只调 `body.step()` |
| `scripts/fetch-mujoco.mjs` | 模型/wasm 落盘 + `manifest.json` | 改了 MJCF 要重跑 |

### 1.2 数据流（当前）

```
L1 vitals ─┐
L3 affect ─┴→ L4 /plan (FastAPI) ─→ intent{action:goto, target}
                                       │
                        main.ts 算方向/速度 vx,vz（+yaw）
                                       │
                     BodyCommand{vx,vz,yaw}
                       ├──────────────┴──────────────┐
                 CapsuleBody.step              G1Body.step
                 (Rapier 平动)          (qvel 注入 + 姿态钉死)
                       └──────────── BodyState ────────┘
                                │
            L1 积分 / L3 刷新 / 遥测 / 遇障判定（同一份代码）
```

### 1.3 三个断点（这次要补的）

1. **腿不迈**：底盘速度是注入的，关节保持 `stand`，看起来像滑行。
2. **没有 IK**：`G1Body.setJoint(name, rad)` 通道通，但没人调用；L4 只到“去哪”，不到“手怎么伸”。
3. **两套几何**：家具碰撞在 Rapier（`physics.ts` 手写），MuJoCo 里只有地板 + 四面墙 → G1 会穿桌子。

## 2. 三条工作线

顺序建议：**3 → 1 → 2**。理由：IK 的目标点（水杯/冰箱）和步态的碰撞都依赖场景单一真源；
先做 3 只需改两处、风险最低，还能立刻让“撞桌子”变成可观测信号。

### 2.1 第 3 件：场景单一真源（先做，半天）

现在家具坐标在 `physics.ts` 里硬编码一遍，MuJoCo 里没有。改成一份数据、两个消费者。

**新增** `src/console/scene.ts`：

```ts
export interface SceneObject {
  name: string;
  /** three 坐标（y 上）；中心点，不是角点 */
  pos: [number, number, number];
  /** 半长（不是全长） */
  half: [number, number, number];
}
export const SCENE: SceneObject[] = [
  // 从 physics.ts 现有数值搬过来，一个都不许改（改了要说明为什么）
];
```

- `physics.ts`：按 `SCENE` 建静态碰撞体，删掉硬编码。
- `g1-body.ts`：`G1Body.create()` 里用它**生成 MJCF 文本**（不要手写第二份 XML）：
  - 坐标映射：three `(x, y, z)` → MuJoCo `(x, -z, y)`（three 的 z 是 MuJoCo 的 -y，y 是 z）。
  - MuJoCo `size` 是**半长**，正好对上 `half`。
  - 把生成的 XML 写进 VFS（`m.FS.writeFile("/g1/room.xml", xml)`）再 `mj_loadXML`。

**验收**（缺一不可）：

| 判据 | 阈值 | 测法 |
|---|---|---|
| G1 撞桌被识别 | 连续 6 帧内 `BodyState.blocked === true` | 让 L4 goto 一个“桌子后面”的点 |
| 胶囊同样被识别 | 同上 | `?body=capsule` 跑同一目标 |
| 不穿透 | 角色根与盒面距离 ≥ -0.02 m | 每帧读 `qpos[0:3]` 与盒面做符号距离 |
| 回归 | 现有 5 个动作、L4、遥测不坏 | 手点一遍 + 下文 4 节的脚本 |

**坑**：`room.xml` 里 `<include file="g1.xml"/>` 必须保留（模型本体）；生成的盒子放 `<worldbody>`；
别把墙写成 `plane`（`plane` 无限大，会挡死）。

### 2.2 第 1 件：步态（1–2 天）

分两级，**必须先交 L0 再评估 L1**，不许跳过直接说“会走了”。

**L0 相位步态（visual）**：

- 相位 `φ += 2π f dt`（f 先用 1.2 Hz，标记 `@own` 并在文档写“手调未标定”）。
- 29 个关节目标角 = `stand` 基线 + 正弦叠加（髋 pitch ±0.35、膝 0–0.5、踝 pitch 反相 ±0.2）。
- 底盘**仍然注入**速度，但幅度跟随相位（`vx * (0.6 + 0.4*|sin φ|)`），至少不再匀速滑。
- 采样点：`data.qpos` 里 29 个铰链角，对照 `model.jnt_range` 检查是否超限。

**L1 物理推进（physical，允许失败）**：

- 关掉 qvel 注入，只给关节目标角，让脚底摩擦推动。
- 允许并预期“会摔”：记录摔倒时间、摔倒方向、是否自恢复。
- **负结果必须写进文档**：这是本仓库的硬规矩（AGENTS.md §3）。

**验收**：

| 判据 | L0 | L1 |
|---|---|---|
| 连续运行 | 30 s 不摔 | 记录到第一次摔倒的时间 |
| 关节超限 | 0 次（`qpos` vs `jnt_range`） | 同 |
| 位移 | ≥ 2 m / 30 s | 实测多少写多少，可以是 0 |
| 足迹 | 每 0.8 s 至少一次足底接触（`data.ncon` + 脚 geom id） | 同 |
| 帧预算 | 每帧物理 ≤ 2 ms | `performance.now()` 记录 P50/P95 |

**API 备忘**（wasm 绑定已确认存在）：`data.ncon`、`data.contact.size()/get(i).geom1/geom2/dist`、
`data.qpos`、`data.ctrl[a]`、`model.jnt_range`、`mj_kinematics`、`mj_forward`。

### 2.3 第 2 件：IK（1–2 天）

目标：把“手到水杯”从摆姿势变成解出来的 29 维角。

**算法**：阻尼最小二乘（DLS），别引入库（没有 mink 的 JS 版）：

```
循环（最多 50 次）：
  mj_kinematics + mj_comPos  → 更新 xpos/xquat
  mj_jacBody(model, data, jacp, jacr, bodyId)   // jacp: 3×nv, jacr: 3×nv
  e = targetPos - bodyPos(3)
  Δq = Jᵀ (J Jᵀ + λ²I)⁻¹ e,   J 取 jacp 的 3×29（屏蔽 free joint 的 6 列）
  q[7..35] += clamp(Δq, ±0.1)   // 每步限幅，别一步跳飞
  mj_forward → 重算误差
  停止条件：|e| < 0.02 或 50 次或 1.5 s
```

- 末端：`right_wrist_yaw_link` 的 body 位置（先不用 site，模型里右手的 site 需要确认存在）。
- 目标点来自 `scene.ts` 的水杯位姿，**不要**再硬编码一份。
- 接到 L4：intent `drink` → 目标点 → IK → `data.ctrl` → 等 1.5 s → 判定。

**验收**：

| 判据 | 阈值 |
|---|---|
| 末端误差 | ≤ 0.02 m（三次取中位） |
| 关节超限 | 0 次 |
| IK 单次耗时 | P95 ≤ 2 ms（wasm，主线程） |
| 与胶囊对照 | 同一句“去喝水”，两边都记录到达时间；差值写进文档 |

**坑**：`mj_jacBody` 的输出数组必须自己 new（`new Float64Array(3*nv)`），传 TypedArray 而不是普通数组；
算完要 `mj_forward` 才能读到新的 `xpos`。

### 2.4 顺手（不做也行，但要写“没做”）

- G1 的 5 个动作（坐 / 拿 / 喝水 / 被吓）：能用 IK 的用 IK，不能的在文档标关键帧。
- 遥测卡片加一行：当前身体 + gait 模式（visual/physical）+ IK 状态。

## 3. 可复现验证（新增脚本）

新增 `testbed/scripts/check-g1.mjs`（照抄 `scripts/shot.mjs` 的 puppeteer 启动方式），跑 5 条断言：

| # | 断言 | 阈值 |
|---|---|---|
| 1 | 页面加载后 `__state().body === "g1"` | 40 s 内 |
| 2 | 站立 10 s 漂移 | rootZ 变化 ≤ 0.05 m |
| 3 | 走到桌边 | 位移 ≥ 1.5 m，`blocked` 出现过 |
| 4 | IK 到水杯 | 误差 ≤ 0.02 m |
| 5 | 切回胶囊再切回 G1 | 无报错、位置连续 |

失败就红，别加 `|| true`。脚本要能被 CI 跳过（没有模型的环境），用环境变量 `SKIP_G1=1`。

## 4. 已知坑（照抄，别重踩）

1. **网格顶点必须取模型的** `mesh_vert` / `mesh_face`。另读 STL 会把 `mesh_pos` 偏移算两遍，头浮在身子上方 0.4 m。
2. **mesh 名 ≠ 文件名**：`waist_yaw_link` → `waist_yaw_link_rev_1_0.STL`；映射在 `manifest.json` 的 `meshFiles`。
3. **group 2 才是可视**，group 3 是碰撞体（同一份 STL），全画会重影。
4. `loadMujoco({ locateFile: p => base + p })`，wasm 从 `public/mujoco/mujoco.wasm` 取。
5. `m.FS.mkdir` 要先建目录，再 `writeFile`。
6. `mj_resetDataKeyframe` 之后必须 `mj_forward`，否则 `xpos` 是旧值。
7. 姿态必须每子步钉死（`qpos[3:7]` 写朝向四元数、`qvel[3:6]=0`），否则 1 秒内倒地。
8. 坐标：three `(x, y, z)` ↔ MuJoCo `(x, -z, y)`；盒子 `size` 是半长。
9. 30 MB 模型不进仓库：`.assets/` 与 `testbed/public/mujoco/` 已在 `.gitignore`。
10. 网络：`raw.githubusercontent.com` 会被 SSL 拦，用 `cdn.jsdelivr.net`（>50 MB 的单文件会被拒，例如 torso 的 2.5 MB 也可能命中包体积上限）或 `gh-proxy.com`；
    下完用二进制 STL 规则校验：`size === 84 + 50 * faceCount`，否则 MuJoCo 报 “wrong size; perhaps this is an ASCII file”。

## 5. 不做（边界，写清楚免得被当成承诺）

- **不接 RL 训练**（Isaac Lab / mjlab / unitree_rl_lab）：那是另一条栈，不进浏览器。
- **不改 L4 服务接口**（`server/main.py` 的 schema）：三条工作线都在前端闭环。
- **不为好看调阈值**：`isBlocked` 的 0.35、`STALE_AGE_MS` 的 1500 都是判据，动机要可核对。
- **不把滑行叫成步态**：L0 交的是“相位驱动 + 关节在动”，不是“会走路”。

## 6. 提交与文档

- 一个提交一件事；`testbed/src/**` 或 `testbed/package.json` 改动必须在同一提交里更新
  `docs/testbed.md` 与 `CHANGELOG.md`（CI 的 `doc-sync` 会红）。
- 每条结论带 n 与场景（例：`3/3 次到达，误差 0.8/1.1/0.6 cm`），不许写“显著提升”。
- 跑不完全部就写“没跑”，不要留假数据。

## 7. 只有两小时的话

只做第 3 件的前半：把 `physics.ts` 的家具搬进 `scene.ts`，两处消费，跑通“撞桌 → blocked”。
这一步不改变任何现有行为，却让后面两件事有了地基。
