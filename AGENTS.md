# AGENTS.md · 在这个仓库里干活的规矩

> 状态：定稿 · 适用范围：**任何人或 agent 提交代码**（人读这一份就够，不需要额外口头约定）
> 本文不是建议清单。第 4 节的文档同步规则由 CI 强制，违反即红。

## 0. 三条铁律（先看这个，再看别的）

1. **不许编数字。** 没跑过的跑数、没测过的时延，一律写 `TBD` 并注明"谁来定、怎么定"。
   编一个看起来专业的常数，比显眼地空着坏得多。
2. **分母为 0 就打 `N/A`。** 不许用 `0%` 或 `100%` 顶替；报比例必须同时报 n（`2/3` 比 `66.7%` 诚实）。
3. **不许把规则代理写成模型。** `sim/vla/scripted.py` 是规则策略代理，不是 VLA；
   L2 的视觉后端目前吃状态向量、不吃像素。任何结论都必须带这句边界。

## 1. 开工前自检（三连，30 秒）

```bash
node scripts/check-docs.mjs                      # 链接 / 表格 / 排版
cd checks && node validate-contracts.mjs         # schema / 样例 / 基线 / Issue 模板
python -m sim.main --baseline full --duration 6 --no-render   # 主闭环还能跑
```

三条都过再动笔。**本机 `%TEMP%` 可能不可用**（pip/esbuild 会报 "No usable temporary directory"）：
先 `export TEMP=<工作区>/.tmp`。

## 2. 目录地图：改什么该碰哪里

| 你改的 | 顺带必须看/改 |
|---|---|
| `contracts/*.schema.json` | `sim/contracts.py`（镜像）、`checks/`（样例与校验）、ADR |
| `evals/baselines/*.yaml` | `docs/evaluation.md`、`evals/README.md`（历史数据可比性） |
| `sim/config.py` 的阈值 | `docs/architecture.md` 的 TBD 表、`sim/calibration.md` |
| `sim/l1_homeostasis/**` | `docs/architecture.md` L1 段 |
| `sim/l2_reflex/**` | `docs/architecture.md` L2 段、`docs/safety.md` |
| `sim/l3_affect/**` | `docs/architecture.md` L3 段、`docs/evaluation.md` |
| `sim/l4_planning/**` | `docs/architecture.md` L4 段、`docs/interfaces.md` |
| `sim/env/**`（场景/物理） | `sim/README.md`、`docs/architecture.md` |
| `sim/vla/**`、`sim/test_vla_l4.py` | `sim/vla/README.md`；CLI 变更还要 `sim/vla/TESTPLAN.md` |
| `sim/main.py` 的 CLI | `sim/README.md`、根 `README.md` 的快速开始 |
| `.github/workflows/**` | `CONTRIBUTING.md`（流程真的变了吗） |

## 3. 文档怎么写（这份仓库的硬约定）

| 约定 | 具体要求 |
|---|---|
| 状态行 | 每份文档开头必须有 `> 状态：定稿/草案/占位 · 最后更新 YYYY-MM-DD` |
| 一件事一份文档 | 路线图不写进架构文档，基线不写进 README |
| 数字来源 | 实测 → 标场景与 n；未实测 → `TBD` + 谁定；引用 → 标"已核实/待核实" |
| 图 | 必须带源文件（`.mmd`），改图先改源再重出 PNG |
| 链接 | 仓库内用相对路径；不要绝对 URL |
| 负结果 | **必须写**。跑不出来、假设被推翻，都是结果；藏起来等于造假 |
| 语气 | 直接说"没做/不知道/测不出来"，不写"正在积极推进"这类话 |

## 4. 文档同步（Docs-as-Code）· CI 强制

### 4.1 原则

**改代码的人，在同一个提交里更新对应文档。** 文档更新不许留到"下一个 PR""以后补"。
代码审查即文档审查：缺文档的改动不该合并。

### 4.2 触发矩阵（按变更类型分级）

| 级别 | 触发条件 | 必须更新 |
|---|---|---|
| **破坏性** | 改 `contracts/` 字段语义、改 `evals/baselines/*.yaml` 的开关、改场景物理参数 | 所有相关文档 **+ 新 ADR + CHANGELOG** |
| **API 变更** | CLI 参数增删改、函数签名变更、schema 加/改字段 | 对应使用文档（`sim/README.md`、`sim/vla/README.md`、`docs/interfaces.md`）**+ CHANGELOG** |
| **新功能** | 新增模块 / 新场景 / 新指标 | 用户能读到的那份文档（`sim/README.md`、`docs/*.md`）**+ CHANGELOG** |
| **内部重构** | 不改行为、不改接口 | 至少一处受影响文档里的注释级说明（可 `Docs-Sync: skip` 并写理由） |

### 4.3 路径 → 文档 映射（可执行）

**以脚本为准**：`node scripts/check-doc-sync.mjs --explain` 打印当前生效的规则表。

| 改动的路径 | 至少更新其中一处 |
|---|---|
| `contracts/**` | `docs/interfaces.md` / `docs/adr/**` / `checks/examples/**` |
| `evals/baselines/**`·`evals/metrics.md`·`evals/log-format.md` | `docs/evaluation.md` / `evals/README.md` |
| `sim/config.py` | `docs/architecture.md` / `sim/calibration.md` |
| `sim/l1_homeostasis/**` | `docs/architecture.md` / `docs/glossary.md` |
| `sim/l2_reflex/**` | `docs/architecture.md` / `docs/safety.md` |
| `sim/l3_affect/**` | `docs/architecture.md` / `docs/evaluation.md` |
| `sim/l4_planning/**` | `docs/architecture.md` / `docs/interfaces.md` |
| `sim/env/**` | `sim/README.md` / `docs/architecture.md` |
| `sim/vla/**`·`sim/test_vla_l4.py`·`sim/export_replay.py` | `sim/vla/README.md` / `sim/vla/TESTPLAN.md` |
| `sim/main.py` | `sim/README.md` / `README.md` |
| `.github/workflows/**` | `CONTRIBUTING.md` / `AGENTS.md` |
| `site/**` | `docs/**` / `README.md` / `AGENTS.md`（改同步脚本本身时） |

规则是**"至少一处"**，不是全要：写清楚比写很多重要。

两个例外要说清，免得把逃生口当成走后门：

- `site/scripts/**`（同步脚本本身）不要求改 docs——它由 `docs site` 构建 job 验证（dead link 会直接红）；
  这种情况下把规则本身写进 `AGENTS.md` 即可。
- 仓库根上的 `AGENTS.md` / `CHANGELOG.md` / `README.en.md` **不是站点页面**：站内链接必须指回 GitHub，
  不要指望它们被渲染。

### 4.4 逃生口（必须留痕）

确实不需要改文档（纯 typo、纯注释、格式化），在提交信息里加一行：

```text
Docs-Sync: skip —— 只改了 sim/l2_reflex/layer.py 的注释，行为与接口未变
```

CI 会把它降级为警告并**打印在日志里**，不静默。理由必须具体；"无关"两个字会被当成违规。

### 4.5 CI 怎么拦

`.github/workflows/checks.yml` 的 `doc-sync` job：取本次推送/PR 的 diff，
逐条匹配 4.3 的规则，有改动命中但对应文档没出现在同一个 diff 里 → **失败并打印缺哪份**。

## 5. 提交与分支

```text
<type>: <一句话>

feat / fix / docs / contracts / evals / governance / sim / test / ci
```

- 一个提交只做一件事；文档和代码在**同一个提交**里（4.1 的直接后果）。
- 提交信息里的数字必须能对上日志；对不上就别写。
- 不 force-push 到 `main`；跑 `sim/` 的产物（`logs/`、`.tmp/`）已在 `.gitignore`。

## 6. 禁区（违反即回滚）

| 禁区 | 为什么 |
|---|---|
| 改判据去凑结果（阈值 / `MAX_CHUNK_AGE_MS` / 场景参数） | 那等于改答卷 |
| 把 `--fast` 跑出的数字当实时性结论 | 那个模式不配速 |
| 提交 `.venv/`、`node_modules/`、`logs/`、模型权重 | 体积 + 泄密面 |
| 在公开文档写真实 IP / 凭据 / 个人联系方式 | 见 `SECURITY.md` |
| 声称"已验证"但没跑 | 一次就够毁掉整个仓库的可信度 |

## 7. 相关文件

[CONTRIBUTING.md](CONTRIBUTING.md)（给外部贡献者）· [docs/README.md](docs/README.md)（文档索引与状态约定）·
[docs/adr/](docs/adr/)（决策记录）· [sim/vla/README.md](sim/vla/README.md)（探针的真/假边界）·
[SECURITY.md](SECURITY.md)
