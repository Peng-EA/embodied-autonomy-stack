# 双模型闭环探针（L2 动作块 + L4 语义规划）

> 状态：可用（v0.1 探针） · 最后更新 2026-11-06
> 入口：`python -m sim.test_vla_l4`

这个目录回答一个具体问题：**把"慢的语义模型 + 快的动作策略"放进同一个闭环，
MuJoCo 的实时物理会先咬到哪一处？**

它不是在演示"两个模型都能跑"。它是在**数**四种代价：过期指令被采纳的比例、
延迟把闭环成功率打掉多少、字段合法但物理做不到的比例、抢占之后旧结果回灌的比例。

---

## 一、先划清"真的"和"代打"的边界

| 部件 | 状态 | 说明 |
|---|---|---|
| MuJoCo 物理环 100 Hz | **真的** | `mj_step` 只在主线程调用；实测 100.5 Hz、单步间隔 p99 10.26 ms |
| L4 推理 | **真的** | 本机 Ollama `qwen2.5:7b`（AMD RX 6750 GRE，Vulkan 全量 offload）；结构化输出受 JSON Schema 约束；实测 p50 2.85 s / p99 5.49 s |
| L2 的**接口** | **真的** | `VLABackend.get_action(obs) -> ActionChunk`，线程隔离、20 Hz 播放、硬超时、超时回退 |
| L2 的**策略** | **代打** | 默认 `ScriptedBackend`（规则 + PD），**不是 VLA**。报告里一律称"规则策略代理" |
| L2 的**视觉后端** | **实现了但未启用** | `OllamaActBackend` 走 `/api/generate`，本机无视觉模型；**输入端是状态向量不是像素**（离屏渲染在 Windows 要额外 GL 上下文，属单独一件事） |
| SmolVLA | **未安装** | 无 torch/lerobot；适配点已由 `VLABackend` 固定，下一轮只需加 `smolvla.py` |

> 一句话：**物理与 L4 是真的，L2 的策略是代理。** 任何引用这里数据的文档都必须带上这句。

---

## 二、跑法

```bash
# 离线自检（不需要 Ollama、不需要模型，CI 用这条）
python -m sim.test_vla_l4 --provider fake --scene stage1 --duration 12 --fast --no-render

# 本机真模型（L4=qwen2.5:7b，L2=规则代理）
python -m sim.test_vla_l4 --provider ollama --scene stage1 --duration 25 --no-render

# 操作场景（臂 + 夹爪 + 易碎杯）
python -m sim.test_vla_l4 --provider ollama --scene manip --duration 35 --no-render

# 断网：第 3 秒把 L4 端点点到已关闭端口（复现"连接被拒"）
python -m sim.test_vla_l4 --provider fake --fault remote-down --cut-at 3 \
    --l4-period 1.2 --duration 12 --no-render

# M2 矩阵：注入延迟 × 动作量化
python -m sim.test_vla_l4 --provider fake --scene stage1 --sweep --fast
```

关键参数：`--l4-url / --l4-model / --l4-timeout-ms / --l4-num-gpu`、
`--l2 scripted|ollama-vlm / --l2-url / --vlm-timeout-ms`、
`--l2-latency-ms / --l2-quantize-ms`、`--l4-on-preempt discard|adopt`、
`--stale-policy adopt|discard`、`--intruder sweep|park`、`--l4-period`。

---

## 三、双机 runbook（笔记本 = RTX 3050Ti 4 GB）

```powershell
# ---- 笔记本侧（管理员 PowerShell 跑防火墙那条）
setx OLLAMA_HOST 0.0.0.0:11434          # 只监听本机的话，主机连不进来
# 重启 ollama 服务/进程后：
ollama pull moondream                   # L2 视觉：1.8B，约 1.7 GB，4 GB 显存够用
ollama pull qwen3:4b                    # 可选：把 L4 也搬过去
New-NetFirewallRule -DisplayName "ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
ipconfig | findstr IPv4                 # 记下 192.168.x.x（别用 192.168.160.x 那种虚拟网卡）

# ---- 主机侧
python -m sim.vla.preflight --l4-url http://192.168.x.x:11434
python -m sim.test_vla_l4 --provider ollama --scene stage1 --duration 25 \
    --l4-url http://192.168.x.x:11434 --l4-model qwen3:4b
```

`preflight` 会告诉你：端点通不通、模型在不在、三次冒烟的 RTT、`/api/ps` 里的显存占用。
**先跑它再跑实验**——跨机时 90% 的"模型很慢"其实是模型根本没加载或防火墙在丢包。

---

## 四、六个场景（都对同一个物理环）

| 场景 | 怎么触发 | 主要观测 |
|---|---|---|
| **A 延迟税** | 正常跑，L4 真推理 2.8–5.5 s | M1：结果到达时世界已变 |
| **B 跨层失配** | L4 给出墙内/界外目标；或"向前推进"被 L2 拦 | M3 分项 + `reflex_override` |
| **C L1 抢占一致性** | `--battery-start 24`（直接落在阈值下） | M4：迟到结果是否被作废 |
| **D 视觉后端降级** | `--l2 ollama-vlm --vlm-timeout-ms 400` | `l2_fallback` 计数、底盘是否失控 |
| **E 单机资源争抢** | `--l4-num-gpu 0` 强制 CPU 推理 | M6：物理频率与超期次数 |
| **F 双机断网** | `--fault remote-down --cut-at N`（或真去停笔记本的服务） | M5：拒连/超时计数 |

---

## 五、本机实测（AMD RX 6750 GRE + Ryzen 5 7500F，2026-11-06）

| 项 | 值 | 场景 |
|---|---|---|
| L4 延迟 | p50 **2.85 s**，p99 **5.49 s**（7 次） | stage1, 25 s |
| L4 延迟 | p50 **3.18 s**，p99 **5.59 s**（7 次） | manip, 35 s |
| **M1 过期采纳率** | **4/5 = 80%** | stage1, 25 s |
| **M1 过期采纳率** | 2/6 = 33% | manip（抓取成功那次） |
| M3 语义不可行 | 有 1 次真实样本：LLM 给出的目标点落在墙内 | 另一次 stage1 运行 |
| M4 抢占后残留 | 0/1（`discard` 策略下迟到结果全部作废） | stage1 |
| M6 物理 | 实际 **100.5 Hz**，单步间隔 p50 10.01 / p99 10.26 / max 10.5 ms，超期 **0** | stage1 |
| M6 物理 | 实际 **100.4 Hz**，抓取成功，杯子最大接触力 **4.26 N**（碎裂线 5 N） | manip |
| M5 断网 | 10 次请求里 **7 次拒连**，物理环照常跑，L2 未失控 | fault, 12 s |

**这些数字只对本机、本网、这套参数成立**；n 很小（M1 的分母是 2–5），当方向性证据看。

---

## 六、这个探针自己踩过的坑（留给下一个人）

1. **臂是 group 2、被夹的杯子是 group 3**：射线不做 geom group 过滤的话，L2 会"看见"
   自己的手臂（间隙 0.08 m 恒报警），底盘被自己的手一路顶到场外。
2. **被夹的物体不能进障碍集**：杯子在 group 3 之前，底盘在站位点 0.34 m 处
   反复振荡（clr 0.21→0.44→0.21），因为"要靠近的目标"被当成了障碍。
3. **夹爪闭合轴是局部 y 不是 x**：pitch 绕 y 转，局部 x 会被转成"朝下"，
   手指于是从上往下扎进杯子（实测指压到 -0.038 直接穿透）。
4. **抬臂不能等杯子升起来**：那是鸡生蛋；先闭合 0.5 s，再按 tick 抬臂。
5. **单跳绕墙 = 直撞墙**：中转点必须由"到没到过"推进，否则到了第一跳就原地停住。

---

## 七、还没做的

- **像素输入**：`OllamaActBackend` 现在是"状态向量 → 动作块"，接了 `images` 才是视觉。
- **SmolVLA**：需要 torch + lerobot + 权重，本机没有；接口已就绪。
- **真正的加速后端对比**（PyTorch vs ONNX/量化）：现在用"注入延迟 + 动作量化"近似，
  能看到"更快但更糙会不会把闭环打坏"，但那条曲线的形状不能替代真实后端测量。
