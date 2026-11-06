# 双模型探针 · 执行测试方案（交接版）

> 给**执行方**：你不需要读仓库其余部分，只需要按 T1–T8 跑、按第 5 节填表。
> 每个用例都给了：目的、命令、时长、**通过判据**、记录什么。
> 判据不达标不等于"你跑错了"，可能是探到了缺陷——按第 6 节处理，不要改判据去凑。

---

## 0. 跑之前必须知道的四件事

1. **L2 的策略是规则代理，不是 VLA。** 接口是真的（线程/超时/回退/动作块），策略是代打。
   任何结论都必须带这句，否则就是虚假宣传。
2. **`--fast` 不配速，不能用于实时性结论。** 它只测逻辑；实时性（M6）必须用默认的实时模式。
3. **场景是确定性的**（无随机扰动），所以矩阵每格跑 1 次就够；跑 3 次是同一条轨迹复制，
   不能算作 n=3。
4. **本机 `%TEMP%` 可能不可用**（本仓库踩过：pip/esbuild 报 "No usable temporary
   directory"）。所有命令前先 `export TEMP=<工作区>/.tmp`（见 §1）。

---

## 1. 准备（10 分钟）

```bash
# 1) 定位仓库与解释器（Windows 示例；Linux/mac 把 sim/.venv/Scripts 换成 sim/.venv/bin）
cd <repo>/embodied-autonomy-stack
export PY="sim/.venv/Scripts/python.exe"
export TEMP="$(pwd)/.tmp"; mkdir -p "$TEMP"          # 本机 Temp 不可用时的兜底

# 2) 环境自检（三条都要过）
$PY -c "import mujoco, numpy, yaml, requests; print('deps ok', mujoco.__version__)"
$PY -m sim.main --baseline full --duration 6 --no-render | tail -6      # 主闭环应打印"抢占正确率"
$PY -m sim.vla.preflight                                            # L4 端点应"✓ 冒烟"

# 3) 模型预热（第一次推理要 10 s 加载，别让它算进数据）
$PY -m sim.test_vla_l4 --provider ollama --scene stage1 --duration 2 --no-render | head -8
```

**自检通过的标准**

| 检查 | 通过 |
|---|---|
| deps | 打印 `deps ok` |
| 主闭环 | 出现 `抢占正确率` 与 `日志 ...jsonl` |
| preflight | L4 段 `✓ 冒烟 3 次`；若报 `模型不在该端点` → `ollama pull <模型>` |
| 预热 | 首行出现 `L4 预热完成 Ns` |

> 若 preflight 报 L2 的 `moondream` 缺失：**这是预期状态**（L2 默认走 scripted），
> 只有 T6 需要它。不要为了"消掉红叉"去 pull 模型。

---

## 2. 六个必跑用例

### T1 · 基线（真 L4 / 规则 L2 / 无故障）

```bash
$PY -m sim.test_vla_l4 --provider ollama --scene stage1 --duration 25 --no-render \
    --report qa/vla-runs/<date>/t1.json
```
- **时长**：25 s
- **目的**：拿到 M1/M5/M6 的基线；确认"慢推理 + 实时物理"能共存
- **判据**
  - `M6`：`physics_hz_actual ≥ 99`（实时模式）、`step_gap_p99 ≤ 12 ms`、`deadline_miss` 尽量为 0
  - `M5`：`n ≥ 2`（25 s 内至少两次规划），`latency_p50` 落在 **1–8 s**（超出范围说明模型没热或端点不对）
  - `M1`：**允许 0–100% 任意值**，这一项是测量不是验收
  - 任务是否完成**不作为 T1 的判据**（L4 的规划可能把机器人带去别处，那是数据）
- **记录**：M1 的分子/分母、M5 的 p50/p99/n、M6 的 p99/max/miss

### T2 · 过期指令（同一命令，看 M1 的稳定性）

```bash
$PY -m sim.test_vla_l4 --provider ollama --scene stage1 --duration 25 --no-render \
    --l4-period 2 --report qa/vla-runs/<date>/t2.json
```
- **目的**：把 L4 请求加密到每 2 s 一次，让 M1 的分母变大
- **判据**：`M5.n ≥ 6`；M1 给出分母；若 M1 的分母仍是 0，说明结果全被 `discard` 掉——**照实记**
- **记录**：M1（含 n）、每次 `plan_adopted` 的 `stale_m` 分布

### T3 · 抢占一致性（L1 直接落在阈值下）

```bash
$PY -m sim.test_vla_l4 --provider ollama --scene stage1 --duration 20 --no-render \
    --battery-start 24 --l4-period 2 --report qa/vla-runs/<date>/t3.json
```
- **目的**：一开局就触发 `battery_low` → `preempt`，看迟到结果是否被作废
- **判据**：日志里出现 `"event":"preempt"`；`M4.late_results ≥ 1`；
  `M4.late_adopted` 在 `discard` 策略下**必须为 0**
- **记录**：M4 的分子/分母；日志里 `preempt` 与 `semantic_plan_result` 的时间差（ms）

### T4 · 对照：不丢弃会怎样（量化残留）

```bash
$PY -m sim.test_vla_l4 --provider ollama --scene stage1 --duration 20 --no-render \
    --battery-start 24 --l4-period 2 --l4-on-preempt adopt \
    --report qa/vla-runs/<date>/t4.json
```
- **判据**：`M4.late_adopted` **> 0**（若仍为 0，说明迟到结果根本没发生，记"本次未测到"，
  不许写"丢弃策略有效"）
- **记录**：T3 与 T4 的 M4 并排；这是同一现象在两种策略下的差值

### T5 · 操作场景（臂 + 夹爪 + 易碎杯）

```bash
$PY -m sim.test_vla_l4 --provider ollama --scene manip --duration 35 --no-render \
    --report qa/vla-runs/<date>/t5.json
```
- **判据**
  - `M2_l2_closed_loop.success == true`（杯子被抬起 > 0.13 m）
  - `M6.cup_force_max_n` 落在 **1.0–5.0 N**（< 1 N 说明没真夹住；> 5 N 说明已经打碎）
  - `M6.collision_ticks == 0`
- **记录**：杯子峰值力、抓取成功时刻、M1 的分子/分母

### T6 · 断网（端点消失）

```bash
$PY -m sim.test_vla_l4 --provider fake --scene stage1 --duration 12 \
    --fault remote-down --cut-at 3 --l4-period 1.2 --no-render \
    --report qa/vla-runs/<date>/t6.json
```
- **目的**：复现"连接被拒"，验证 L2 接管、物理不停
- **判据**
  - `M5.connection_errors ≥ 1`（本次实测是 7/10）
  - `M5.timeouts` 允许为 0（拒连与超时是两种故障，别互相顶替）
  - `M6.physics_hz_actual ≥ 95`（端点死了物理不许跟着死）
  - `M6.collision_ticks` 不因故障上升
- **记录**：拒连数、物理频率、L2 后端构成

---

## 3. T7 · M2 矩阵（延迟 × 量化）

```bash
# stage1：阴性对照（预期全 1/1，见 §7 已知局限）
$PY -m sim.test_vla_l4 --provider fake --scene stage1 --sweep --fast

# manip：真正的待测（抓取时机依赖反馈，延迟才可能咬到）
$PY -m sim.test_vla_l4 --provider fake --scene manip --sweep --fast
```

- **时长**：stage1 约 1–2 min；manip 约 3–5 min
- **判据**：不做"要下降多少"的预设。**只要求如实记录曲线形状**：
  - 若全 1/1 → 写"本场景对该轴无分辨力"，并注明 §7 的两条原因
  - 若有格子掉到 0 → 记录从哪一档开始掉（那才是本轮最有价值的数）
- **禁止**：为了让曲线"好看"去改 `MAX_CHUNK_AGE_MS` 或阈值——那是改判据

---

## 4. T8 · 双机专项（有 3050Ti 笔记本才跑）

```powershell
# 笔记本侧（管理员）
setx OLLAMA_HOST 0.0.0.0:11434          # 改完重启 ollama 进程
ollama pull moondream                   # 约 1.7 GB，4 GB 显存够
New-NetFirewallRule -DisplayName "ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
ipconfig | findstr IPv4                 # 记 192.168.x.x（别用 192.168.160.x 虚拟网卡）
```

```bash
# 主机侧：先预检，再跑
$PY -m sim.vla.preflight --l4-url http://192.168.x.x:11434
$PY -m sim.test_vla_l4 --provider ollama --scene stage1 --duration 25 --no-render \
    --l4-url http://192.168.x.x:11434 --l4-model <笔记本上的模型> \
    --report qa/vla-runs/<date>/t8.json

# 真断网（可选，需要人在笔记本上停服务）：跑到一半 Ctrl+C 关掉 ollama，
# 或另开窗口停止服务，观察 M5.connection_errors 是否出现
```

- **判据**：`M5.latency_p50` 比 T1 高（跨机有网络往返）；`connection_errors` 在停服务后 > 0
- **记录**：两机的模型名、RTT、p50/p99 与 T1 的对比

---

## 5. 记录模板（直接填这张表交付）

| 用例 | 命令要点 | 关键指标 | 结果 | 与参照值的差异 | 判断 |
|---|---|---|---|---|---|
| T1 | ollama stage1 25s | M5.n / p50 / M1 / hz / p99 | | | |
| T2 | +`--l4-period 2` | M1 分子/分母 | | | |
| T3 | +`--battery-start 24` | M4.late_results / late_adopted | | | |
| T4 | +`--l4-on-preempt adopt` | M4.late_adopted | | | |
| T5 | manip 35s | success / cup_force / collisions | | | |
| T6 | fault cut-at 3 | conn / hz / collisions | | | |
| T7a | stage1 sweep | 矩阵形状 | | | |
| T7b | manip sweep | 矩阵形状 | | | |
| T8 | 跨机 | p50/p99 / conn | | | |

**参照值（本机 AMD RX 6750 GRE + Ryzen 5 7500F，2026-11-06，只作方向参照，不是目标）**

| 项 | 参照 |
|---|---|
| L4 延迟 | p50 2.85 s / p99 5.49 s（7 次） |
| M1 | 4/5 = 80%（stage1）、2/6 = 33%（manip） |
| M6（GPU 推理） | 100.5 Hz、p99 10.26 ms、超期 0 |
| M6（`--l4-num-gpu 0` 争抢） | p99 12.35 ms、max 39.2 ms、10/2500 超期 |
| T5 抓取 | 杯子抬到 0.175 m，峰值接触力 4.26 N |
| T6 | 10 次请求 7 次拒连，物理照常 |

---

## 6. 失败处置（先分类，再动手）

| 症状 | 最可能原因 | 先验证 | 处置 |
|---|---|---|---|
| `preflight` 冒烟超时 | 端点没起 / 防火墙 | `curl http://<IP>:11434/api/tags` | 修端点；跨机看 §4 |
| 报 `模型不在该端点` | 没 pull | `ollama list` | `ollama pull <模型>` |
| 报 `No usable temporary directory` | 本机 Temp 权限坏了 | `echo $TEMP` | `export TEMP=<工作区>/.tmp` |
| 首次推理 10 s+ | 模型冷加载 | 看首行"预热完成" | 已自动预热；若没有，`--no-warmup` 别开 |
| `M5.n == 0` | 25 s 内没触发需求 | 日志里有没有 `semantic_plan_request` | 加 `--l4-period 2` 再跑 |
| `M5.n > 0` 但 M1 分母 0 | 结果都被作废/被拒 | 看 `transport` 与 `invalidated` | 记"未测到"，别改策略凑 |
| `physics_hz_actual` 明显 > 100 | 用了 `--fast` 或超期补跑 | 看启动横幅有无"未配速" | 实时性结论必须用实时模式 |
| T5 抓不到（`cup_force` 恒 0） | 底盘没到站位 / 夹爪没闭合 | `--trace` 看 `chunk` 与 `pos` | 记现象 + 附 trace 前 200 行；**不要**手改 scene |
| 数字明显异常（如 Hz 上万） | 混用了 `--fast` | 看横幅 | 重跑实时模式 |

**什么时候停下来问，而不是自己修**：改了 `contracts/`、`evals/baselines/`、
`sim/env/scene*.xml` 才能继续的时候。这三处一改，历史数据就不可比了。

---

## 7. 红线与已知局限（必须跟着结论一起交）

**红线**

1. 不许把 `scripted` 写进结论里叫 "VLA"。
2. 不许编数字；跑之前不要先把预期填进表里。
3. 分母为 0 一律写 `N/A`，不许用 0% 或 100% 代替。
4. 不许改判据（阈值、`MAX_CHUNK_AGE_MS`、场景参数）去让结果变好看。

**已知局限（本轮就是这样，不是执行方的问题）**

| 局限 | 影响 | 备注 |
|---|---|---|
| 动作块变旧后**沿用最后一条指令**（`test_vla_l4.py` 里 `chunk_row` 不清零） | 速度指令是平滑量，旧值仍近似正确 → **延迟轴在本场景几乎不咬人**（实测 1200 ms 仍成功） | 这是 M2 无分辨力的主因；真正的伤害要在"必须随状态快速改变"的任务里才出现 |
| L2 无像素输入 | 视觉后端（`ollama-act`）吃的是状态向量 | 接 `images` 才是 VLA |
| SmolVLA 未安装 | 没有真 VLA 基线 | `VLABackend` 已留适配点 |
| 场景确定性 | n=1/格合法 | 想要 n>1 得先加随机扰动（属于改场景，来问） |

---

## 8. 交付物清单

1. `qa/vla-runs/<date>/t1..t8.json`（每个用例的 `--report` 输出）
2. `logs/vla/r*/…jsonl` 与同名 `.meta.json`（每集一个目录，**不要删**）
3. 第 5 节那张填好的表（Markdown 或 Excel 都行）
4. 一段 ≤ 300 字的"看到了什么"，必须包含：
   - 哪些指标分母为 0（= 没测到）
   - 有没有出现与参照值方向相反的结果
   - 有没有触发第 6 节需要停下来说的情况

**验收标准**：T1–T6 全部跑完且表格无空格；T7 至少 stage1 那条跑完；
所有结论都带 n 与"规则代理"标注。**不要求数字漂亮，只要求可复核。**
