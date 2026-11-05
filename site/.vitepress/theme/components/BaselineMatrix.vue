<script setup>
const GH = 'https://github.com/Peng-EA/embodied-autonomy-stack'

// 数据源自 docs/evaluation.md 的四条基线；此处是"预期方向"，不是实测值。
const baselines = [
  { id: 'B0', name: 'LLM 中心决策', note: '对照组 · 现状' },
  { id: 'B0′', name: '行为树基线', note: '对照组 · 工业界' },
  { id: 'B1', name: '无情绪调制', note: '检验情绪' },
  { id: 'B2', name: '需求生成·不抢占', note: '检验抢占' },
]

const rows = [
  {
    metric: 'LLM 断线存活',
    hint: '断线后计时',
    cells: [
      { t: '0', k: 'bad' },
      { t: '高', k: 'good' },
      { t: '同 B0′', k: 'muted' },
      { t: '同 B0′', k: 'muted' },
    ],
  },
  {
    metric: '需求驱动行为占比',
    hint: '分子 = L1 触发且非指令',
    cells: [
      { t: '0', k: 'bad' },
      { t: '0', k: 'bad' },
      { t: '0', k: 'bad' },
      { t: '生成但不抢占', k: 'warn' },
    ],
  },
  {
    metric: '情绪调制可观测性',
    hint: '配对检验行为参数',
    cells: [
      { t: '—', k: 'na' },
      { t: '—', k: 'na' },
      { t: '无显著差异', k: 'bad' },
      { t: '同 B1', k: 'muted' },
    ],
  },
  {
    metric: '异常自恢复率',
    hint: '注入失败后无人工干预',
    cells: [
      { t: '低', k: 'bad' },
      { t: '中', k: 'warn' },
      { t: '同 B0′', k: 'muted' },
      { t: '同 B0′', k: 'muted' },
    ],
  },
  {
    metric: '抢占正确率',
    hint: '事后判定"该抢"',
    cells: [
      { t: '—', k: 'na' },
      { t: '—', k: 'na' },
      { t: '—', k: 'na' },
      { t: '无抢占（对照）', k: 'warn' },
    ],
  },
  {
    metric: '发育曲线',
    hint: '长期成功率 / 耗时趋势',
    cells: [
      { t: '平坦', k: 'muted' },
      { t: '平坦', k: 'muted' },
      { t: '平坦', k: 'muted' },
      { t: '平坦', k: 'muted' },
    ],
  },
]

const toneClass = (k) =>
  ({
    bad: 'ea-cell ea-cell--bad',
    good: 'ea-cell ea-cell--good',
    warn: 'ea-cell ea-cell--warn',
    na: 'ea-cell ea-cell--na',
    muted: 'ea-cell',
  })[k] || 'ea-cell'
</script>

<template>
  <div class="ea-matrix-wrap">
    <table class="ea-matrix">
      <thead>
        <tr>
          <th>指标 / Metric</th>
          <th v-for="b in baselines" :key="b.id">
            <b>{{ b.id }}</b>{{ b.name }}<br /><span class="ea-cell--na">{{ b.note }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.metric">
          <td>
            {{ r.metric }}<br /><small class="ea-cell--na">{{ r.hint }}</small>
          </td>
          <td v-for="(c, i) in r.cells" :key="i" :class="toneClass(c.k)">{{ c.t }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="ea-legend">
    <span><i style="background: #ff4d5e"></i>预期变差 / 被移除</span>
    <span><i style="background: #35d07f"></i>预期变好</span>
    <span><i style="background: #ffb454"></i>需人工判定</span>
    <span><i style="background: #4d5b62"></i>与对照相同</span>
    <a :href="`${GH}/blob/main/docs/evaluation.md`" target="_blank" rel="noreferrer">
      测量方法全文 →
    </a>
  </div>
</template>
