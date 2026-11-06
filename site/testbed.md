---
layout: doc
title: 实时跑分台
description: v0.2 的 ETS-Testbed：Rapier 物理 + Three.js 渲染，场景/基线/延迟可切，数字与批跑同源
---

# 实时跑分台：同一份代码，浏览器里跑，命令行里批

这里的画面**不是录像**，也不是另写一套演示逻辑：它跑的是 `testbed/src/core/`，
和 `npm run bench` 生成那张曲线图用的是**同一个 `Sim.step()`**。

<ClientOnly>
<iframe
  src="/testbed/"
  style="width:100%;height:640px;border:1px solid #1f2937;background:#080b0c"
  title="ETS-Testbed"
/>
</ClientOnly>

> 应用不加载时（本地没构建过），先 `cd testbed && npm install && npm run dev`。
> 部署版由 CI 在站点构建前把 `testbed/dist` 拷进 `/testbed/`。

## 能切什么

| 控件 | 作用 |
|---|---|
| 场景 | `stage1-static`（静墙+静桩，对照）/ `stage2-moving`（会移动的充电桩） |
| 基线 | `full`（分层全开）/ `b0f`（公平 LLM 中心）/ `b1` / `b2` |
| LLM 延迟 | 0.1 – 2.0 s，**仿真时间**上的可控延迟，不是网络实测 |

面板上的电量、间隙、速度、过期丢弃次数都来自 Worker 回传的同一份采样。

## 第一份数据（含一条阴性结果）

n=20（S2）/ n=10（S1），区间是 Wilson 95%，由 `evals/v0.2/results/2026-11-06/` 生成：

| 基线 | 0.1 s | 0.7 s | 1.2 s | 2.0 s |
|---|---|---|---|---|
| `full`（过期即丢 + 降级） | 20/20 · 6.72 s | 20/20 · 10.31 s | **18/20** · 11.05 s | **0/20** |
| `b0f`（照旧执行过期计划） | 20/20 · 6.72 s | 20/20 · 7.42 s | 20/20 · 7.86 s | **20/20** · 9.61 s |

**严格"过期就丢"输给了"照旧执行"**。原因写在
[`evals/v0.2/README.md`](https://github.com/Peng-EA/embodied-autonomy-stack/blob/main/evals/v0.2/README.md)：
丢弃会把系统推进永久降级，而降级限速追不上会动的目标点；
在这个场景里"过期"的后果只是多跑一段路，不是撞上去。

静态对照组（S1）三条基线全部 10/10——台子本身不偏袒任何一方。

## 自己跑

```bash
cd testbed
npm install
npm run dev                      # 上面这个界面
npm run bench -- --quick         # 30 秒级别的冒烟批跑
npm test                         # 同 seed 两次运行必须同 stateHash
```

完整的复现命令、区间、局限与下一步见
[evals/v0.2](https://github.com/Peng-EA/embodied-autonomy-stack/tree/main/evals/v0.2)
与 [docs/testbed.md](https://github.com/Peng-EA/embodied-autonomy-stack/blob/main/docs/testbed.md)。
