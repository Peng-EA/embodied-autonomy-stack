---
layout: doc
title: 实验回放
description: 把 MuJoCo 的运行录成回放带：画面、数据条、指标表读到的是同一次运行
---

# 实验回放：让画面、数据、结论指认同一件事

贴一张 GIF 或一段视频，读者只能相信你剪的那 3 秒。这里的做法不一样：

1. **录制**：`sim/export_replay.py` 跑真实仿真（物理 100 Hz，L4 真模型），把每 20 ms 的
   状态写成一条回放带（`site/public/replay/*.json`）；
2. **回放**：下面的播放器读这条带子画俯视图——机器人轨迹、间隙、电量、杯子受力；
3. **对账**：指标表里的每个数字都来自同一份 JSON 的 `metrics` 字段，
   **本页没有手写任何一个数**；
4. **指认**：每条结论（M1/M2…）都能点一下跳到那一帧，自己看当时发生了什么。

> 回放**不是**浏览器里的实时仿真：拖动时间轴不会触发新推理，也不会改变结论。
> 它是一次运行的录像 + 同一次运行的指标，用途是让"我说它过期了"变成"你看这一帧"。

---

## 一、T1 · 基线：慢推理撞上 100 Hz 物理

L4 用真模型（`qwen2.5:7b`，Ollama），L2 走规则代理。这一段要看的不是"任务完成没有"，
而是**请求与结果之间那 0.7–1.1 s 里，世界走到了哪里**。

<ClientOnly>
<SimReplay src="/replay/t1-baseline.json" :marks="[
  { t: 7040, label: 'L4 请求 g1 发出（此时机器人在 y≈-1.6）', tone: 'info' },
  { t: 7090, label: 'L1 广播 battery_low → 抢占，g1 立刻作废', tone: 'bad' },
  { t: 8990, label: 'g1 迟到 686 ms 到达，已被作废（inv=1）', tone: 'bad' },
  { t: 10120, label: 'g2 被采纳，但目标点已是 1 s 前的世界（stale=1）', tone: 'warn' },
  { t: 24000, label: '25 s 结束：仍在起点片区打转，始终没走到充电桩', tone: 'bad' }
]" />
</ClientOnly>

**这一段能看出三件事**

| 现象 | 证据（同一份 JSON） |
|---|---|
| L4 结果普遍迟到 0.6–1.1 s | `M5_transport.latency_p50` |
| 被抢占的请求，结果回来时已经被判死 | 7.09 s 发生 `preempt`，8.99 s 返回的 `plan_result` 带 `inv=1` |
| 没被抢占的结果，也可能基于过期状态 | 10.12 s 采纳的 g2 带 `stale=1`（拒绝它的策略在 `--stale-policy discard`） |
| 物理没被拖住 | `M6_physics.step_gap_p99` ≈ 10.3 ms，`deadline_miss` = 0 |
| **任务没完成，而且看得出为什么** | 十次采纳的目标点都在起点附近（`adopt` 事件的 `x/y`），机器人被按在原地打转——L4 的规划**语义上说得通、任务目标丢了**。这不是渲染问题，是这一段最该被看到的结果 |

---

## 二、T5 · 操作：2-DOF 臂抓易碎杯

底盘定位 + 固定臂姿把杯子夹起来，杯子受力超过 **5 N 判碎**。这一段看的是
**力曲线在抓取瞬间顶到哪**，以及"夹住了"和"夹碎了"之间那 0.7 N 的余量。

<ClientOnly>
<SimReplay src="/replay/t5-manip.json" :height="330" :marks="[
  { t: 4160, label: 'L2 反射否决：间隙 0.21 m，先躲', tone: 'warn' },
  { t: 4730, label: '采纳抓取目标 (1.05, 1.45)，底盘去站位', tone: 'info' },
  { t: 13550, label: '杯子抬起 → 任务完成（杯力红线 5 N）', tone: 'ok' }
]" />
</ClientOnly>

**这一段的读法**

- 画面里黄色十字是两指中点，杯子是青色圆；**杯力**显示在 HUD 与数据条里。
- 抓取成功那一帧的 `cup_force` 明显低于 5 N——这是"力控参数够用"的直接证据，
  不是"我们做了力控"这句话。
- 如果哪天有人把 `GRIP_CLOSE_*` 调大，这条曲线会先顶到红线再碎：**判碎与夹住的差别只有 0.7 N**。

---

## 三、T6 · 断网：端点消失，物理不停

第 3 秒把 L4 端点指到一个已关闭的端口，复现"连接被拒"。整段用假服务跑，
所以它可复现、也不依赖模型——重点看**谁死了、谁没死**。

<ClientOnly>
<SimReplay src="/replay/t6-fault.json" :height="320" :marks="[
  { t: 3000, label: '端点故障注入（指向已关闭端口）', tone: 'bad' },
  { t: 4110, label: '第一次拒连：连接超时 510 ms 后失败', tone: 'bad' }
]" />
</ClientOnly>

**这一段能看出什么**

| 事实 | 读数 |
|---|---|
| 端点坏了，L4 请求全部失败 | `M5_transport.connection_errors` = 7/10 |
| 物理完全不受影响 | `M6_physics.physics_hz_actual` ≈ 100 Hz、`deadline_miss` = 0 |
| L2 没跟着崩 | `collision_ticks` = 0，底盘仍在按动作块走 |
| 只有"语义层"降级 | 之前采纳的目标仍在执行，新请求全部退回 |

---

## 四、这些数字的出处

| 你想核对的 | 去哪看 |
|---|---|
| 指标定义与阈值 | [evaluation.md](evaluation.md)、[`evals/metrics.md`](https://github.com/Peng-EA/embodied-autonomy-stack/blob/main/evals/metrics.md) |
| 探针的真/假边界（L2 是规则代理） | [`sim/vla/README.md`](https://github.com/Peng-EA/embodied-autonomy-stack/blob/main/sim/vla/README.md) |
| 每段回放的参数 | 页面顶部标签（provider / 模型 / 时长 / 录制时间） |
| 原始运行日志 | `logs/vla/r*/…jsonl`（不随仓库分发，体积原因） |
| 怎么重跑出同样的形状 | 下面第五节 |

## 五、自己重录一遍

```bash
# 全部四个用例（需要本机 Ollama + qwen2.5:7b；t6 用假服务，不需要模型）
python -m sim.export_replay

# 只重录一段
python -m sim.export_replay --only t1-baseline

# 看看会执行什么（不真跑）
python -m sim.export_replay --dry
```

产物写到 `site/public/replay/<id>.json`，`cd site && pnpm run docs:build` 即进站点。

## 六、诚实边界（与前几节一致）

- **L2 的策略是规则代理，不是 VLA。** 这些画面里的"动作块"由 `sim/vla/scripted.py` 产出。
- **延迟是真的**（Ollama 推理，本机 AMD Vulkan，约 0.6–1.1 s）；不是人为 sleep。
- **样本很小**：单次运行、单场景、确定性环境。上面每张表的 n 都写在表里。
- **M1 允许出现 0%**：那只是"这次没抓到过期的结果"，不是"过期问题不存在"。
- 回放带是**录制件**：它证明"发生过"，不证明"必然发生"。
