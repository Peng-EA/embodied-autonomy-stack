# 验证状态：哪些结论是真跑过的

> 状态：草案 · 最后更新 2026-11-08 · 复跑环境：Windows / Edge headless / `server/.venv` Python 3.13 / 本机 CPU 单线程

这份文件只回答一个问题：**仓库里那些"已验证"的说法，今天到底有没有重新跑过。**
它不评价方法好坏，也不补新实验——只做一件事：把"声明"和"证据"分开列。

## 0. 三条读法

| 表 | 含义 | 能不能引用 |
|---|---|---|
| §1 今天复跑 | 2026-11-08 在本机重跑、退出码 0、数字在下面 | 可以，注明日期与 n |
| §2 未复跑 | 文档里写过、今天没重跑 | **可以保留原声明**，但必须标"未复跑" |
| §3 设计上就是 TBD/负结果 | 已知不成立或量不了 | 只能照抄，**不许写成"已修复"** |

## 1. 今天复跑（2026-11-08，全部 exit 0）

### 1.1 静态检查

| 项 | 命令 | 结果 |
|---|---|---|
| 文档链接/表格/排版 | `node scripts/check-docs.mjs` | 全部通过 |
| 分层护栏 | `node scripts/check-layer-deps.mjs` | 42 个 `.py`，0 处 `import sim_server` |
| 契约校验 | `cd checks && node validate-contracts.mjs` | 全部通过 |

### 1.2 四层闭环跑在真 G1 上

| 指标 | 实测 | 判据 |
|---|---|---|
| 摔倒（z_min） | 0.7159 m | > 0.50 m → 未摔 |
| 净位移 | 1.143 m（+1.30,−1.20 → +0.51,−0.38，直线距离） | 记录项 |
| L4 调用 | 3 次请求 / 3 次返回 / 超时 0 / degraded=no | 无降级 |
| 抢占 | 正确 1/1、误抢 0 | 反事实推演，非代理指标 |
| 语义不成立项 | `L1.charging` / `L3 hold_charge` / `L2.push` | 三处记 TBD，未伪造信号 |

命令：`server/.venv/Scripts/python.exe -m sim.main --baseline full --duration 6 --no-render --body g1`

### 1.3 sim_server 自检（四个模式）

| 模式 | 实测 | 判据 | 结论 |
|---|---|---|---|
| P0 HOLD 站立 30 s | 漂移 **0.0002 m**、高 1.3222 m、tick 60.0 Hz、`nan=False` | — | 通过 |
| P2b POLICY 走 5 s | 0.5531 m/s | — | 通过 |
| P2b POLICY 走 30 s | **0.5733 m/s**、累计 **16.0157 m**、z_min 0.7250、关节越限 0、推理 p95 **0.053 ms**（n=2059） | 不摔 / 0 超限 | 通过 |
| P2b 模式切换 | `hold→policy→hold` 未摔（z_min 0.7242，2 次切换） | — | 通过 |
| P2a MIMIC（`bonus/B_HandsUp`） | 441 帧 / 8.816 s、越限 0、z_min 0.7766、回 HOLD **0.208 s**、推理 p95 0.056 ms（n=473） | 回 hold ≤2 s | 通过 |
| P3 IK（`desk_cup`） | 规划误差 **2.92 mm**、末端误差 **4.54 mm**、solve p50 1.04 / p95 **1.32 ms**（n=134）、触发到到位 **0.580 s**、越限 0、未摔 | 末端 ≤10 mm / p95 ≤5 ms / ≤2.5 s | 通过 |

命令：`python -m sim_server.selfcheck --seconds 30` / `--walk --seconds 30` / `--mimic` / `--ik`

注：`--walk` 的 80 s 仿真墙钟只用了 1.78 s（它**不按墙钟配速**），所以这条是仿真真值，不是实时性。

### 1.4 演示台（浏览器 + 真实 WS）

| 项 | 实测 |
|---|---|
| 布局断言（1440×900 / 1280×800） | **26/26 ×2**，无裁切 |
| 摄像机断言 | **18/18**（解锁后 W 平移 1.05 m/1.2 s、滚轮 10.0%、右键 27.8°；锁定后三种输入全无效；回位平滑非瞬移） |
| console 错误 | **0** |
| 拖 vx 滑杆 → 真进 POLICY | 旁听 WS：`speed_p50=0.528` / `speed_max=0.753` m/s（不是页面自证） |
| 网格 | 35 块 / **393,270** 三角 / 高 **1.3424 m** / missing 0 |
| 场景 | `room.boxes=8`、`loaded=true` |
| 总判定 | `verdict=pass` |

命令：`node testbed/scripts/demo-check.mjs`

## 2. 文档里已有、今天**未复跑**（保留原声明）

| 声明 | 为什么没复跑 | 引用时怎么写 |
|---|---|---|
| P0e「跨网络暴露的 4 个真实缺陷已修」 | 需要另一台机/暴露网络，本机没法造 | "已修（2026-11-07，未复跑）" |
| `probe-autonomy` / `probe-idle` / `probe-ws` 各自数字 | 本次只跑了 `demo-check`（覆盖 mesh、reset、滑块→POLICY） | 引用时标原日期 |
| `g1-full` 基线整套 | 今天只跑 `--baseline full --body g1 --duration 6` | "单次 6 s 冒烟，不是整套基线" |
| `server/` L4 服务单独起 8787 的联调 | 今天没起该进程 | 保留 `server/README.md` 原说法 |
| 跨机（`--l4-url`）与真机 | 无设备 | 维持"未验证" |
| 策略权重来源（playground joystick） | 没重跑训练 | 只声明"加载的权重能走"，不声明训练质量 |

## 3. 设计上就是 TBD / 负结果（不许写成已修复）

| 项 | 事实 |
|---|---|
| `dance/J_Dance0_StepTouch` 越限 | 保留的负结果，候选表里会打 FAIL；**不是 bug 修复了就消失** |
| 接触力 | 显示的是代理量（`contact_force_max=0.00 N` / `ncon` 对数），不是真接触力 |
| WS 延迟 | 面板显示的是**帧龄**，不是 RTT |
| 出生点 IK | 几何上必失败（距离不够），属于已知边界 |
| 抓取 | IK 是"伸手到点"，**不是抓取**；没有夹爪闭合语义 |
| 语义信号 | `L1.charging` 等三处仍 TBD（房间里没有充电桩） |

## 4. 自己复跑（一条命令一条表）

```bash
export TEMP=<repo>/.tmp TMP=<repo>/.tmp
P=server/.venv/Scripts/python.exe
node scripts/check-docs.mjs && node scripts/check-layer-deps.mjs
$P -m sim.main --baseline full --duration 6 --no-render --body g1
$P -m sim_server.selfcheck --seconds 30
$P -m sim_server.selfcheck --walk --seconds 30
$P -m sim_server.selfcheck --mimic
$P -m sim_server.selfcheck --ik
node testbed/scripts/demo-check.mjs
```

## 5. 维护规则

1. 发版前重跑 §4，把新的实测值写回 §1，日期改成当天。
2. 跑不动/环境缺依赖的，从 §1 挪到 §2，并写清为什么——**不许留在 §1 冒充已验证**。
3. §3 只增不减；要删，必须附上"现在为什么不再成立"的证据。
