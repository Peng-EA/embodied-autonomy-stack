<script setup>
const GH = 'https://github.com/Peng-EA/embodied-autonomy-stack'

// 主图见 StackDiagram.vue（手排 SVG）。这里只放"层 → 契约"的索引行，
// 因为图里已经有信息，不再重复堆一段 mermaid。
const layers = [
  {
    tag: 'L1',
    name: '内感受 / 稳态',
    ts: '10–100 ms',
    io: 'homeostatic_broadcast · 全局广播 · 最高抢占权',
    href: `${GH}/blob/main/contracts/homeostatic.schema.json`,
  },
  {
    tag: 'L2',
    name: '反射',
    ts: '10–100 ms',
    io: 'reflex_override · outcome_event · 绝对否决权',
    href: `${GH}/blob/main/contracts/signals.schema.json`,
  },
  {
    tag: 'L3',
    name: '情感 / 动机',
    ts: '0.1–1 s',
    io: 'affect_vector · goal_candidate · 只调制不越权',
    href: `${GH}/blob/main/contracts/affect_vector.schema.json`,
  },
  {
    tag: 'L4',
    name: '规划协调',
    ts: '1–10 s+',
    io: 'semantic_plan_request / result · 异步可插拔',
    href: `${GH}/blob/main/contracts/signals.schema.json`,
  },
]
</script>

<template>
  <StackDiagram />

  <div class="ea-layers">
    <a
      v-for="l in layers"
      :key="l.tag"
      class="ea-layer"
      :href="l.href"
      target="_blank"
      rel="noreferrer"
    >
      <span class="ea-layer-tag">{{ l.tag }}</span>
      <span class="ea-layer-name">{{ l.name }}</span>
      <span class="ea-layer-ts">{{ l.ts }}</span>
      <span class="ea-layer-io">{{ l.io }}</span>
      <span class="ea-layer-go">↗</span>
    </a>
  </div>
</template>
