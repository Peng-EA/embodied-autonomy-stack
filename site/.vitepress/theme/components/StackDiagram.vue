<script setup>
// 为什么首页主图不交给 mermaid 自动布局：
// 拓扑是 4 层竖列 + 2 条跨层回边（抢占 / 硬中断）+ 1 个外部节点。
// mermaid 在这个拓扑上要么把回边绕成圈、要么按 canvas 测宽截断 CJK 标签
// （实测："homeostatic_broadca…"、"10–100 ⯑"）。手排 SVG 一次到位，
// 而且每个层框本身就是 <a>，可点击直达契约。
// mermaid 仍保留给文档内页（完整 7 信号图），见 theme/components/Mermaid.vue。
const GH = 'https://github.com/Peng-EA/embodied-autonomy-stack'

const layers = [
  {
    tag: 'L4',
    name: '规划协调层',
    ts: '1–10 s+',
    l1: '目标排序 · 任务分解 · 下发执行',
    l2: 'LLM 参与：调用外部异步服务',
    color: '#35d07f',
    href: `${GH}/blob/main/contracts/signals.schema.json`,
  },
  {
    tag: 'L3',
    name: '情感 / 动机层',
    ts: '0.1–1 s',
    l1: '连续情绪向量（效价 · 唤醒 · 支配）→ 调制 L2 参数',
    l2: '生成目标候选与优先级 · 只调制，不越权',
    color: '#4fc3d9',
    href: `${GH}/blob/main/contracts/affect_vector.schema.json`,
  },
  {
    tag: 'L2',
    name: '反射层',
    ts: '10–100 ms',
    l1: '避障 · 平衡 · 力控 · 接触修正',
    l2: '硬否决权：任何状态触碰阈值一律截断',
    color: '#ffb454',
    href: `${GH}/blob/main/contracts/signals.schema.json`,
  },
  {
    tag: 'L1',
    name: '内感受 / 稳态层',
    ts: '10–100 ms',
    l1: '电量 · 温度 · 关节应力 · 任务进度',
    l2: '设定点偏离检测 · 全局广播 · 最高抢占权',
    color: '#ff4d5e',
    href: `${GH}/blob/main/contracts/homeostatic.schema.json`,
  },
]

const Y0 = 22
const H = 96
const GAP = 34
const yOf = (i) => Y0 + i * (H + GAP)
</script>

<template>
  <div class="ea-diagram">
    <svg
      class="ea-svg"
      viewBox="0 0 1160 552"
      role="img"
      aria-label="四层神经系统架构：L1 内感受/稳态、L2 反射、L3 情感/动机、L4 规划协调，以及外部 LLM 服务"
    >
      <defs>
        <marker
          id="ea-arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#4a5c63" />
        </marker>
      </defs>

      <!-- ============ 四个层框（可点击） ============ -->
      <a
        v-for="(l, i) in layers"
        :key="l.tag"
        :href="l.href"
        target="_blank"
        rel="noreferrer"
        class="ea-node"
      >
        <title>{{ l.tag }} {{ l.name }} · {{ l.ts }} → 打开该层契约</title>
        <rect
          class="ea-node-bg"
          x="20"
          :y="yOf(i)"
          width="620"
          :height="H"
          rx="8"
          :fill="l.color"
          fill-opacity="0.05"
          :stroke="l.color"
          stroke-opacity="0.45"
          stroke-width="1.2"
        />
        <rect
          x="20"
          :y="yOf(i)"
          width="92"
          :height="H"
          rx="8"
          :fill="l.color"
          fill-opacity="0.12"
        />
        <rect x="104" :y="yOf(i)" width="8" :height="H" :fill="l.color" fill-opacity="0.12" />
        <text
          x="66"
          :y="yOf(i) + H / 2 + 9"
          text-anchor="middle"
          :fill="l.color"
          font-size="27"
          font-weight="700"
        >
          {{ l.tag }}
        </text>
        <text x="132" :y="yOf(i) + 34" class="ea-t-title">
          {{ l.name }}<tspan class="ea-t-dim">　·　</tspan
          ><tspan :fill="l.color" class="ea-t-ts">{{ l.ts }}</tspan>
        </text>
        <text x="132" :y="yOf(i) + 63" class="ea-t-body">{{ l.l1 }}</text>
        <text x="132" :y="yOf(i) + 84" class="ea-t-dim">{{ l.l2 }}</text>
      </a>

      <!-- ============ 层间短箭头（画在空隙里） ============ -->
      <!-- L3 → L4 : goal_candidate -->
      <g class="ea-edge">
        <line
          :x1="556"
          :y1="yOf(1) - 4"
          :x2="556"
          :y2="yOf(0) + H + 4"
          stroke="#4a5c63"
          stroke-width="1.2"
          marker-end="url(#ea-arrow)"
        />
        <text
          x="544"
          :y="(yOf(0) + H + yOf(1)) / 2 + 4"
          text-anchor="end"
          class="ea-t-edge"
        >
          goal_candidate
        </text>
      </g>

      <!-- L2 → L3 : outcome_event -->
      <g class="ea-edge">
        <line
          :x1="556"
          :y1="yOf(2) - 4"
          :x2="556"
          :y2="yOf(1) + H + 4"
          stroke="#4a5c63"
          stroke-width="1.2"
          marker-end="url(#ea-arrow)"
        />
        <text
          x="544"
          :y="(yOf(1) + H + yOf(2)) / 2 + 4"
          text-anchor="end"
          class="ea-t-edge"
        >
          outcome_event
        </text>
      </g>

      <!-- L3 → L2 : affect_vector（向下，调制参数） -->
      <g class="ea-edge">
        <line
          :x1="574"
          :y1="yOf(1) + H + 4"
          :x2="574"
          :y2="yOf(2) - 4"
          stroke="#4a5c63"
          stroke-width="1.2"
          stroke-dasharray="4 3"
          marker-end="url(#ea-arrow)"
        />
        <text x="586" :y="(yOf(1) + H + yOf(2)) / 2 + 4" class="ea-t-edge">
          affect_vector
        </text>
      </g>

      <!-- L2 → L1 : outcome_event -->
      <g class="ea-edge">
        <line
          :x1="556"
          :y1="yOf(3) - 4"
          :x2="556"
          :y2="yOf(2) + H + 4"
          stroke="#4a5c63"
          stroke-width="1.2"
          marker-end="url(#ea-arrow)"
        />
        <text
          x="544"
          :y="(yOf(2) + H + yOf(3)) / 2 + 4"
          text-anchor="end"
          class="ea-t-edge"
        >
          outcome_event
        </text>
      </g>

      <!-- ============ 跨层回边（右侧导轨） ============ -->
      <!-- 抢占：L1 → L4 -->
      <g class="ea-edge">
        <line
          x1="700"
          :y1="yOf(3) - 2"
          x2="700"
          :y2="yOf(0) + H + 6"
          stroke="#ff4d5e"
          stroke-opacity="0.65"
          stroke-width="1.4"
          marker-end="url(#ea-arrow)"
        />
        <text
          transform="translate(688, 290) rotate(-90)"
          text-anchor="middle"
          class="ea-t-edge"
          fill="#ff8b96"
        >
          homeostatic_broadcast
        </text>
      </g>

      <!-- 硬中断：L2 → L4 -->
      <g class="ea-edge">
        <line
          x1="746"
          :y1="yOf(2) + H - 2"
          x2="746"
          :y2="yOf(0) + H + 6"
          stroke="#ffb454"
          stroke-opacity="0.6"
          stroke-width="1.4"
          stroke-dasharray="5 3"
          marker-end="url(#ea-arrow)"
        />
        <text
          transform="translate(734, 246) rotate(-90)"
          text-anchor="middle"
          class="ea-t-edge"
          fill="#ffc978"
        >
          reflex_override
        </text>
      </g>

      <!-- ============ 外部 LLM ============ -->
      <a :href="`${GH}/blob/main/docs/architecture.md`" target="_blank" rel="noreferrer" class="ea-node">
        <title>LLM 是外部异步服务，不是 L4 本身 → 打开架构文档</title>
        <rect
          class="ea-node-bg"
          x="810"
          :y="yOf(0)"
          width="330"
          :height="H"
          rx="8"
          fill="#7b8b93"
          fill-opacity="0.04"
          stroke="#4a5c63"
          stroke-width="1.2"
          stroke-dasharray="6 4"
        />
        <text x="836" :y="yOf(0) + 34" class="ea-t-title">LLM Semantic Service</text>
        <text x="836" :y="yOf(0) + 63" class="ea-t-body">外部服务 · 异步 · 可插拔</text>
        <text x="836" :y="yOf(0) + 84" class="ea-t-dim">断线 → L4 降级为本地策略</text>
      </a>

      <!-- L4 ↔ LLM -->
      <g class="ea-edge">
        <line
          x1="644"
          :y1="yOf(0) + 44"
          x2="802"
          :y2="yOf(0) + 44"
          stroke="#4a5c63"
          stroke-width="1.2"
          marker-end="url(#ea-arrow)"
        />
        <text x="716" :y="yOf(0) + 34" text-anchor="middle" class="ea-t-edge">
          semantic_plan_request
        </text>
        <line
          x1="802"
          :y1="yOf(0) + 78"
          x2="644"
          :y2="yOf(0) + 78"
          stroke="#4a5c63"
          stroke-width="1.2"
          marker-end="url(#ea-arrow)"
        />
        <text x="716" :y="yOf(0) + 98" text-anchor="middle" class="ea-t-edge">
          semantic_plan_result
        </text>
      </g>
    </svg>

    <div class="ea-diagram-hint">
      点击任一层打开其 JSON Schema 契约 · 时间尺度即抢占优先级：L1 &gt; L2 &gt; L3 &gt; L4 · 窄屏可横向滑动
    </div>
  </div>
</template>

<style>
.ea-diagram {
  margin: 26px 0 6px;
  padding: 18px 10px 12px;
  background: var(--ea-bg-soft);
  border: 1px solid var(--ea-line);
  border-radius: var(--ea-radius);
  overflow-x: auto;
}
.ea-svg {
  width: 100%;
  max-width: 100%;
  height: auto;
  display: block;
}
@media (max-width: 940px) {
  .ea-svg { min-width: 860px; }
}
.ea-node {
  cursor: pointer;
  text-decoration: none !important;
}
.ea-node .ea-node-bg {
  transition: fill-opacity 0.16s ease, stroke-opacity 0.16s ease;
}
.ea-node:hover .ea-node-bg {
  fill-opacity: 0.1;
  stroke-opacity: 0.9;
}
.ea-t-title {
  fill: #eef4f6;
  font-family: var(--ea-mono);
  font-size: 17.5px;
  font-weight: 500;
}
.ea-t-ts {
  font-size: 12.5px;
  font-weight: 500;
}
.ea-t-body {
  fill: #b3bec5;
  font-family: var(--ea-mono);
  font-size: 12.5px;
}
.ea-t-dim {
  fill: #8b98a4;
  font-family: var(--ea-mono);
  font-size: 12px;
}
.ea-t-edge {
  fill: #5c6b73;
  font-family: var(--ea-mono);
  font-size: 11px;
  letter-spacing: 0.02em;
}
.ea-diagram-hint {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--ea-line);
  font-size: 11px;
  color: var(--ea-dim);
  letter-spacing: 0.02em;
}
</style>
