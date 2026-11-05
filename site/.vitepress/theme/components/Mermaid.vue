<script setup>
import { onMounted, ref } from 'vue'

const props = defineProps({
  code: { type: String, required: true },
})

const box = ref(null)
const failed = ref('')
let seq = 0

onMounted(async () => {
  try {
    const mermaid = (await import('mermaid')).default
    mermaid.initialize({
      startOnLoad: false,
      theme: 'base',
      darkMode: true,
      securityLevel: 'strict',
      fontFamily:
        "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, 'Microsoft YaHei', monospace",
      themeVariables: {
        background: '#0a0f11',
        primaryColor: '#0d1315',
        primaryTextColor: '#dbe5e9',
        primaryBorderColor: '#2a3a40',
        secondaryColor: '#101a1d',
        tertiaryColor: '#0a0f11',
        lineColor: '#3d4f56',
        textColor: '#dbe5e9',
        clusterBkg: 'rgba(255,255,255,0.012)',
        clusterBorder: '#2a3a40',
        edgeLabelBackground: '#080b0c',
        fontSize: '14px',
      },
      flowchart: {
        curve: 'basis',
        htmlLabels: false,
        useMaxWidth: false,
        padding: 30,
        nodeSpacing: 52,
        rankSpacing: 64,
      },
    })

    const id = `ea-mmd-${++seq}-${Math.random().toString(36).slice(2, 8)}`
    const { svg } = await mermaid.render(id, props.code)
    if (box.value) box.value.innerHTML = svg
  } catch (err) {
    failed.value = String(err && err.message ? err.message : err)
    // 图挂了不能白屏：把源码留给懂的人
    console.error('[ea] mermaid render failed', err)
  }
})
</script>

<template>
  <div class="ea-mermaid" role="img" aria-label="四层神经系统架构图">
    <div ref="box" />
    <pre v-if="failed" class="ea-mermaid-fallback">{{ code }}</pre>
  </div>
</template>

<style>
.ea-mermaid-fallback {
  text-align: left;
  font-size: 11.5px;
  color: var(--ea-dim);
  overflow-x: auto;
}
</style>
