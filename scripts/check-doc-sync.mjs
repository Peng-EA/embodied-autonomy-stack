#!/usr/bin/env node
/**
 * 文档同步检查（Docs-as-Code 的执行者）
 *
 * 规则来自 AGENTS.md §4.3：**改了某条代码路径，同一个 diff 里必须带上对应文档**。
 * 两份东西必须一起看：
 *   - 人读的表：AGENTS.md §4.3
 *   - 机器读的规则：本文件里的 RULES（`--explain` 会把它们渲染成同一张表）
 *
 * 为什么不用现成工具：这份仓库的路径→文档映射是项目特有的（L1/L2/L3/L4、
 * contracts、evals/baselines……），而且我们要求"至少一处文档"这种任选语义，
 * 自己 60 行比配一个通用工具 + 一层配置简单。
 *
 * 用法：
 *   node scripts/check-doc-sync.mjs                  # 对比 HEAD~1..HEAD
 *   node scripts/check-doc-sync.mjs --base <sha>     # 对比 <sha>..HEAD（CI 用）
 *   node scripts/check-doc-sync.mjs --explain        # 只打印规则表
 *   node scripts/check-doc-sync.mjs --dry            # 只报告，不退出非零
 *
 * 逃生口：提交信息（HEAD 提交的完整 body）里出现
 *   Docs-Sync: skip —— <具体理由>
 * 则降级为警告。理由会原样打印在 CI 日志里，不允许"无关/无需"这种两字理由。
 */

import { execFileSync } from "node:child_process";
import process from "node:process";

// ---------------------------------------------------------------- 规则
// paths: 命中即触发（正则）；need: 这些路径里**至少一个**要同时出现在 diff 里
// need 以 "/" 结尾表示目录前缀
const RULES = [
  {
    id: "contracts",
    paths: [/^contracts\//],
    need: ["docs/interfaces.md", "docs/adr/", "checks/examples/"],
    why: "契约是层间唯一事实源，改它必须让读契约的人看到",
  },
  {
    id: "evals",
    paths: [/^evals\/(baselines\/|metrics\.md|log-format\.md)/],
    need: ["docs/evaluation.md", "evals/README.md"],
    why: "基线/指标/日志格式一变，历史数据的可比性就变",
  },
  {
    id: "sim-config",
    paths: [/^sim\/config\.py$/],
    need: ["docs/architecture.md", "sim/calibration.md"],
    why: "阈值与常数属于 TBD 表，不能只改代码不改来历",
  },
  {
    id: "sim-l1",
    paths: [/^sim\/l1_homeostasis\//],
    need: ["docs/architecture.md", "docs/glossary.md"],
    why: "L1 的职责与术语写在架构与术语表里",
  },
  {
    id: "sim-l2",
    paths: [/^sim\/l2_reflex\//],
    need: ["docs/architecture.md", "docs/safety.md"],
    why: "反射层和安全边界是同一件事的两面",
  },
  {
    id: "sim-l3",
    paths: [/^sim\/l3_affect\//],
    need: ["docs/architecture.md", "docs/evaluation.md"],
    why: "情绪调制是否有效属于评价问题",
  },
  {
    id: "sim-l4",
    paths: [/^sim\/l4_planning\//],
    need: ["docs/architecture.md", "docs/interfaces.md"],
    why: "L4 与 LLM 的边界写在架构与契约里",
  },
  {
    id: "sim-env",
    paths: [/^sim\/env\//],
    need: ["sim/README.md", "sim/vla/README.md", "docs/architecture.md"],
    why: "场景与物理参数决定实验能复现到什么程度",
  },
  {
    id: "sim-vla",
    paths: [/^sim\/vla\//, /^sim\/test_vla_l4\.py$/, /^sim\/export_replay\.py$/],
    need: ["sim/vla/README.md", "sim/vla/TESTPLAN.md"],
    why: "探针的\"真的/代打的\"边界与用例都在这两份里",
  },
  {
    id: "sim-main",
    paths: [/^sim\/main\.py$/],
    need: ["sim/README.md", "README.md"],
    why: "主入口的 CLI 是别人复现你的唯一路径",
  },
  {
    id: "ci",
    paths: [/^\.github\/workflows\//],
    need: ["CONTRIBUTING.md", "AGENTS.md"],
    why: "流程变了，参与指南与 agent 规矩要跟着变",
  },
  {
    id: "site",
    paths: [/^site\//],
    need: ["docs/", "README.md", "AGENTS.md"],
    why: "站点只是 docs 的渲染产物，正文必须落在 docs/（改脚本本身可更新 AGENTS.md）",
  },
];

const SKIP_MARK = /^\s*Docs-Sync:\s*skip\s*[——:-]?\s*(.+)$/im;
const SKIP_USELESS = /^(无关|无需|n\/a|na|none|no|skip|略|同上)\.?$/i;

// ---------------------------------------------------------------- git
function git(args, opts = {}) {
  return execFileSync("git", args, { encoding: "utf8", ...opts }).trim();
}

function refExists(ref) {
  try {
    git(["rev-parse", "--verify", "--quiet", `${ref}^{commit}`]);
    return true;
  } catch {
    return false;
  }
}

function changedFiles(base, head) {
  const out = git(["diff", "--name-only", "--diff-filter=ACMR", `${base}..${head}`]);
  return out ? out.split("\n").filter(Boolean) : [];
}

function headBody(head) {
  try {
    return git(["log", "-1", "--format=%B", head]);
  } catch {
    return "";
  }
}

const matchNeed = (file, need) =>
  need.endsWith("/") ? file.startsWith(need) : file === need;

// ---------------------------------------------------------------- 主流程
function main() {
  const argv = process.argv.slice(2);
  const arg = (name, dflt = null) => {
    const i = argv.indexOf(name);
    return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt;
  };
  const explain = argv.includes("--explain");
  const dry = argv.includes("--dry");

  if (explain) {
    console.log("| 改动的路径 | 至少更新其中一处 | 为什么 |");
    console.log("|---|---|---|");
    for (const r of RULES) {
      const p = r.paths.map((x) => "`" + x.source.replace(/^\^/, "").replace(/\$$/, "").replace(/\\/g, "") + "`").join(" · ");
      console.log(`| ${p} | ${r.need.map((n) => "`" + n + "`").join(" / ")} | ${r.why} |`);
    }
    return 0;
  }

  const head = arg("--head", "HEAD");
  let base = arg("--base", process.env.DOC_SYNC_BASE || "HEAD~1");

  // 首次推送时 github.event.before 是全 0，或浅克隆里没有这个 ref：退化成 HEAD~1
  if (!refExists(base)) {
    console.log(`[doc-sync] base "${base}" 不存在（首次推送/浅克隆），退化为 HEAD~1`);
    base = "HEAD~1";
  }
  if (!refExists(base)) {
    console.log("[doc-sync] 只有一个提交，跳过检查（首次提交不存在\"改了代码没改文档\"）");
    return 0;
  }

  const files = changedFiles(base, head);
  const scope = `${base.slice(0, 7)}..${head}`;
  console.log(`[doc-sync] 范围 ${scope}，变更 ${files.length} 个文件`);
  if (files.length === 0) {
    console.log("[doc-sync] 空 diff，通过");
    return 0;
  }

  // 逃生口
  const body = headBody(head);
  const skip = body.match(SKIP_MARK);
  let skipReason = "";
  if (skip) {
    const reason = skip[1].trim().replace(/^[—–\-:：\s]+/, "");
    if (SKIP_USELESS.test(reason) || reason.length < 6) {
      console.log(`[doc-sync] ✗ Docs-Sync: skip 的理由不具体（"${reason}"）。`);
      console.log('           要写清"为什么不需要改文档"，例：只改了注释，行为与接口未变。');
      skipReason = "invalid";
    } else {
      skipReason = reason;
      console.log(`[doc-sync] ⚠ 已声明跳过：${reason}`);
    }
  }

  const violations = [];
  for (const rule of RULES) {
    const hit = files.filter((f) => rule.paths.some((p) => p.test(f)));
    if (hit.length === 0) continue;
    const ok = files.some((f) => rule.need.some((n) => matchNeed(f, n)));
    if (!ok) violations.push({ rule, hit });
  }

  if (violations.length === 0) {
    console.log("[doc-sync] ✓ 代码与文档配对正常");
    return 0;
  }

  console.log("");
  for (const v of violations) {
    console.log(`[doc-sync] ✗ ${v.rule.id}：改了 ${v.hit.join(", ")}`);
    console.log(`           但本次 diff 里没有 ${v.rule.need.join(" / ")} 中的任何一个`);
    console.log(`           为什么：${v.rule.why}`);
  }
  console.log("");
  console.log(`共 ${violations.length} 组缺文档。修法（任选）：`);
  console.log("  1) 在同一提交里补对应文档（AGENTS.md §4.2 有分级：破坏性要 ADR + CHANGELOG）");
  console.log("  2) 确实不需要文档 → 提交信息加一行：Docs-Sync: skip —— <具体理由>");

  if (skipReason === "invalid") return 1;
  if (skipReason) {
    console.log("[doc-sync] ⚠ 因声明 skip 而降级为通过（理由已打印，供 review 判断）");
    return 0;
  }
  return dry ? 0 : 1;
}

process.exit(main());
