<script setup>
const GH = 'https://github.com/Peng-EA/embodied-autonomy-stack'
</script>

<template>
  <section class="ea-hero">
    <div class="ea-kicker">
      <span class="ea-dot" aria-hidden="true"></span>
      EA_STACK / RFC-0001 / <span class="ea-dim">DESIGN PHASE · NO RUNNABLE CODE</span>
    </div>

    <h1 class="ea-title">
      Embodied<br />Autonomy Stack<span class="ea-cursor" aria-hidden="true">▌</span>
    </h1>

    <p class="ea-sub">
      给 LLM 划边界，给具身智能补上稳态、反射、情感调制、需求生成与执行修正。
    </p>

    <div class="ea-badges">
      <span class="ea-badge ea-badge--green">v0.1 Design RFC</span>
      <span class="ea-badge">License: Apache-2.0</span>
      <span class="ea-badge ea-badge--muted">4 layers · 4 baselines</span>
    </div>

    <div class="ea-actions">
      <a class="ea-btn ea-btn--primary" :href="GH">
        Read RFC on GitHub <span class="ea-arrow">↗</span>
      </a>
      <a class="ea-btn" :href="GH + '/discussions'">
        Discussions <span class="ea-arrow">↗</span>
      </a>
    </div>

    <!-- 横向信息流：层（时间尺度）· 去掉了原来孤立的 LAYERS 4 单元格 -->
    <dl class="ea-spec">
      <div>
        <dt>L1 / L2</dt>
        <dd>10–100 ms</dd>
        <span>稳态 · 反射</span>
      </div>
      <div>
        <dt>L3</dt>
        <dd>0.1–1 s</dd>
        <span>情感 / 动机</span>
      </div>
      <div>
        <dt>L4</dt>
        <dd>1–10 s+</dd>
        <span>规划协调</span>
      </div>
      <div>
        <dt>Baselines</dt>
        <dd>B0 · B0′ · B1 · B2</dd>
        <span>四条对照基线</span>
      </div>
    </dl>
  </section>
</template>
