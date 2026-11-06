<script setup>
/**
 * 仿真回放：把一次真实运行录下来的状态带放成"俯视画面 + 时间轴 + 数据条"。
 *
 * 三条硬规矩（与站点其他图不一样的地方）：
 * 1. 画面上的每一个点都来自 `frames`（真的跑出来的），不是示意图；
 * 2. 指标条里的数字直接读 JSON 里的 `metrics`，**页面不手写任何一个数**；
 * 3. `marks` 是人为写的结论，所以它必须能点、能跳到那一帧——"结论↔画面"要能互相指认。
 *
 * 边界（页面正文里也会写）：回放不是实时仿真，拖时间轴不会触发新推理。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  src: { type: String, required: true },
  marks: { type: Array, default: () => [] },
  height: { type: Number, default: 360 },
})

const data = ref(null)
const err = ref('')
const idx = ref(0)
const playing = ref(false)
const speed = ref(1)
let raf = 0
let acc = 0
let lastTs = 0

onMounted(async () => {
  try {
    const res = await fetch(props.src)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    data.value = await res.json()
  } catch (e) {
    err.value = `回放数据加载失败：${e.message}（路径 ${props.src}）`
  }
})

onBeforeUnmount(() => cancelAnimationFrame(raf))

// ---------------------------------------------------------------- 时间
const frames = computed(() => data.value?.frames ?? [])
const tMax = computed(() => (frames.value.length ? frames.value[frames.value.length - 1].t : 0))
const frame = computed(() => frames.value[Math.min(idx.value, Math.max(0, frames.value.length - 1))] ?? null)

const loop = (ts) => {
  if (!playing.value) return
  if (!lastTs) lastTs = ts
  acc += (ts - lastTs) * speed.value
  lastTs = ts
  if (acc > 20) {                       // 50 Hz 采样：每 20 ms 走一帧
    const step = Math.floor(acc / 20)
    acc -= step * 20
    const next = idx.value + step
    if (next >= frames.value.length - 1) {
      idx.value = frames.value.length - 1
      playing.value = false
      return
    }
    idx.value = next
  }
  raf = requestAnimationFrame(loop)
}

watch(playing, (on) => {
  cancelAnimationFrame(raf)
  lastTs = 0
  acc = 0
  if (on && frames.value.length) raf = requestAnimationFrame(loop)
})

const toggle = () => {
  if (!playing.value && idx.value >= frames.value.length - 1) idx.value = 0
  playing.value = !playing.value
}
const seekT = (ms) => {
  playing.value = false
  const i = frames.value.findIndex((f) => f.t >= ms)
  idx.value = i < 0 ? frames.value.length - 1 : i
}

// ---------------------------------------------------------------- 坐标
const b = computed(() => data.value?.bounds ?? { x: [-2.5, 2.5], y: [-2.5, 2.5] })
const sc = computed(() => data.value?.scene ?? {})
const vb = computed(() => {
  const [x0, x1] = b.value.x
  const [y0, y1] = b.value.y
  const pad = 0.25
  return `${x0 - pad} ${-(y1 + pad)} ${x1 - x0 + pad * 2} ${y1 - y0 + pad * 2}`
})
// 世界坐标 → SVG（y 轴翻转：世界里 +y 朝上，SVG 里朝下）
const X = (v) => v
const Y = (v) => -v

const trail = computed(() => {
  const pts = []
  const n = frames.value.length
  const from = Math.max(0, idx.value - 240)   // 最近 4.8 s 的轨迹
  for (let i = from; i <= Math.min(idx.value, n - 1); i += 3) {
    const f = frames.value[i]
    pts.push(`${X(f.p[0])},${Y(f.p[1])}`)
  }
  if (frame.value) pts.push(`${X(frame.value.p[0])},${Y(frame.value.p[1])}`)
  return pts.join(' ')
})

// 当前时刻之前 2 s 内发生的事件（HUD 显示"刚刚发生了什么"）
const recent = computed(() => {
  if (!frame.value) return []
  return (data.value.events ?? [])
    .filter((e) => e.t <= frame.value.t && frame.value.t - e.t < 2000 && e.k !== 'need')
    .slice(-3)
    .reverse()
})

const curNeed = computed(() => {
  if (!frame.value) return null
  const needs = (data.value.events ?? []).filter((e) => e.k === 'need' && e.t <= frame.value.t)
  return needs.length ? needs[needs.length - 1] : null
})

// ---------------------------------------------------------------- 数据条
const strip = (key, lo, hi) => {
  const n = frames.value.length
  if (!n) return ''
  const pts = []
  for (let i = 0; i < n; i += 2) {
    const v = frames.value[i][key]
    if (typeof v !== 'number') continue
    const x = (i / (n - 1)) * 100
    const y = 100 - ((v - lo) / (hi - lo)) * 100
    pts.push(`${x.toFixed(2)},${Math.max(0, Math.min(100, y)).toFixed(2)}`)
  }
  return pts.join(' ')
}
const clearLine = computed(() => strip('c', 0, 2.0))
const battLine = computed(() => strip('b', 0, 35))
const forceLine = computed(() => (sc.value.type === 'manip' ? strip('f', 0, 6) : ''))

const evTone = { adopt: 'ok', plan_result: 'info', preempt: 'warn', reflex: 'warn', fault: 'bad', goal: 'ok', cup_lifted: 'ok' }
const evLabel = {
  need: 'L1 需求', plan_request: 'L4 请求', plan_result: 'L4 结果', adopt: '采纳',
  discard: '丢弃', reflex: 'L2 否决', preempt: '抢占', fault: '端点故障',
  goal: '到桩', cup_lifted: '抓取成功',
}

const metrics = computed(() => data.value?.metrics ?? null)
const fmt = (v, d = 2) => (v === null || v === undefined ? 'N/A' : Number(v).toFixed(d))
const pctOrNA = (v) => (v === null || v === undefined ? 'N/A' : `${(v * 100).toFixed(0)}%`)
</script>

<template>
  <div class="ea-replay">
    <p v-if="err" class="ea-replay__err">{{ err }}</p>
    <template v-else-if="!data">
      <p class="ea-replay__loading">正在加载回放带…</p>
    </template>

    <template v-else>
      <div class="ea-replay__meta">
        <span class="tag">{{ data.meta.scenario }}</span>
        <span class="tag">{{ data.meta.provider }} · {{ data.meta.l4_model }}</span>
        <span class="tag">L2 {{ data.meta.l2_backend }}</span>
        <span class="tag">{{ (data.meta.duration_s || 0).toFixed(0) }} s</span>
        <span class="tag dim">录制 {{ data.meta.recorded_at }}</span>
      </div>

      <div class="ea-replay__stage" :style="{ height: height + 'px' }">
        <svg :viewBox="vb" preserveAspectRatio="xMidYMid meet" role="img"
             aria-label="MuJoCo 仿真俯视回放">
          <!-- 地面 -->
          <rect :x="b.x[0] - 2" :y="Y(b.y[1]) - 2" :width="b.x[1] - b.x[0] + 4"
                :height="b.y[1] - b.y[0] + 4" class="floor" />

          <!-- 墙 -->
          <rect v-if="sc.wall" :x="sc.wall.x[0]" :y="Y(sc.wall.y + sc.wall.half_t)"
                :width="sc.wall.x[1] - sc.wall.x[0]" :height="sc.wall.half_t * 2" class="wall" />

          <!-- 充电桩停靠区 -->
          <g v-if="sc.charger">
            <circle :cx="sc.charger.x" :cy="Y(sc.charger.y)" :r="sc.charger.reach" class="charger" />
            <circle :cx="sc.charger.x" :cy="Y(sc.charger.y)" r="0.06" class="charger-core" />
          </g>

          <!-- 动态干扰体（mocap）：y 固定，只画 x 方向 -->
          <rect v-if="sc.intruder && frame.i !== undefined"
                :x="frame.i - sc.intruder.half" :y="Y(sc.intruder.y + sc.intruder.half)"
                :width="sc.intruder.half * 2" :height="sc.intruder.half * 2.2" class="intruder" />

          <!-- 杯子（操作场景） -->
          <g v-if="frame.cup">
            <circle :cx="frame.cup[0]" :cy="Y(frame.cup[1])" :r="sc.cup.r" class="cup"
                    :class="{ broken: frame.f > sc.cup_break_n - 0.2 }" />
            <text :x="frame.cup[0] + 0.07" :y="Y(frame.cup[1]) - 0.05" class="lbl">
              杯 {{ fmt(frame.f, 1) }} N
            </text>
          </g>

          <!-- 末端/夹取点 -->
          <g v-if="frame.ee">
            <line :x1="frame.ee[0] - 0.05" :y1="Y(frame.ee[1])" :x2="frame.ee[0] + 0.05"
                  :y2="Y(frame.ee[1])" class="ee" />
            <line :x1="frame.ee[0]" :y1="Y(frame.ee[1]) - 0.05" :x2="frame.ee[0]"
                  :y2="Y(frame.ee[1]) + 0.05" class="ee" />
          </g>

          <!-- 轨迹 -->
          <polyline :points="trail" class="trail" />

          <!-- 机器人本体 -->
          <rect :x="frame.p[0] - sc.robot.half_x" :y="Y(frame.p[1] + sc.robot.half_y)"
                :width="sc.robot.half_x * 2" :height="sc.robot.half_y * 2" class="robot"
                :class="{ danger: frame.c < 0.22 }" />
        </svg>

        <div class="ea-replay__hud">
          <div><b>{{ (frame.t / 1000).toFixed(2) }}</b> s</div>
          <div>间隙 {{ fmt(frame.c, 2) }} m</div>
          <div>电量 {{ fmt(frame.b, 1) }}%</div>
          <div>速度 {{ fmt(frame.v, 2) }} m/s</div>
          <div v-if="frame.f !== undefined" :class="{ hot: frame.f > sc.cup_break_n - 0.2 }">
            杯力 {{ fmt(frame.f, 2) }} N
          </div>
        </div>

        <div class="ea-replay__recent">
          <span v-for="(e, i) in recent" :key="i" class="ev" :class="evTone[e.k] || 'info'">
            {{ evLabel[e.k] || e.k }}
          </span>
          <span v-if="curNeed" class="ev need">需求 {{ curNeed.cause }}</span>
        </div>
      </div>

      <!-- 控制条 -->
      <div class="ea-replay__bar">
        <button class="btn" @click="toggle">{{ playing ? '暂停' : '播放' }}</button>
        <button class="btn ghost" @click="speed = speed === 1 ? 2 : speed === 2 ? 0.5 : 1">
          {{ speed }}×
        </button>
        <input class="range" type="range" min="0" :max="frames.length - 1" step="1"
               :value="idx" @input="playing = false; idx = Number($event.target.value)" />
        <span class="clock">{{ (frame.t / 1000).toFixed(2) }} / {{ (tMax / 1000).toFixed(1) }} s</span>
        <span class="frame">{{ idx }}/{{ frames.length - 1 }}</span>
      </div>

      <!-- 数据条：与时间轴同宽、同一时间基准 -->
      <div class="ea-replay__strip">
        <div class="row">
          <span class="k">间隙</span>
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" class="spark">
            <polyline :points="clearLine" class="line clear" />
            <line x1="0" :y1="100 - (0.22 / 2.0) * 100" x2="100" :y2="100 - (0.22 / 2.0) * 100"
                  class="thr" />
          </svg>
        </div>
        <div class="row">
          <span class="k">电量</span>
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" class="spark">
            <polyline :points="battLine" class="line batt" />
          </svg>
        </div>
        <div v-if="forceLine" class="row">
          <span class="k">杯力</span>
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" class="spark">
            <polyline :points="forceLine" class="line force" />
            <line x1="0" :y1="100 - (5 / 6) * 100" x2="100" :y2="100 - (5 / 6) * 100" class="thr break" />
          </svg>
        </div>

        <!-- 事件刻度 + 结论标记 -->
        <div class="ticks">
          <span v-for="(e, i) in data.events" :key="'e' + i" class="tick" :class="evTone[e.k] || 'info'"
                :style="{ left: (e.t / (tMax || 1)) * 100 + '%' }" :title="evLabel[e.k] || e.k" />
          <button v-for="(m, i) in marks" :key="'m' + i" class="mark" :class="m.tone || 'warn'"
                  :style="{ left: (m.t / (tMax || 1)) * 100 + '%' }" :title="m.label"
                  @click="seekT(m.t)">M{{ i + 1 }}</button>
        </div>
      </div>

      <!-- 结论标记：点一下跳到那一帧 -->
      <ol v-if="marks.length" class="ea-replay__marks">
        <li v-for="(m, i) in marks" :key="'mk' + i">
          <button class="jump" @click="seekT(m.t)">M{{ i + 1 }}</button>
          <span class="mk-t">{{ (m.t / 1000).toFixed(1) }}s</span>
          <span class="mk-l">{{ m.label }}</span>
        </li>
      </ol>

      <!-- 指标：直接读 JSON 里的 metrics，页面不手写任何数字 -->
      <table v-if="metrics" class="ea-replay__metrics">
        <tbody>
          <tr>
            <th>M1 过期采纳</th>
            <td :class="{ bad: metrics.M1_stale_adoption.rate === null }">
              {{ pctOrNA(metrics.M1_stale_adoption.rate) }}
              <span class="dim">= {{ metrics.M1_stale_adoption.adopted_stale }}/{{
                metrics.M1_stale_adoption.adopted }}</span>
            </td>
            <th>M4 抢占残留</th>
            <td>{{ pctOrNA(metrics.M4_preempt_residual.rate) }}
              <span class="dim">= {{ metrics.M4_preempt_residual.late_adopted }}/{{
                metrics.M4_preempt_residual.late_results }}</span>
            </td>
          </tr>
          <tr>
            <th>M5 L4 延迟</th>
            <td>p50 {{ fmt(metrics.M5_transport.latency_p50_ms, 0) }} ms ·
              p99 {{ fmt(metrics.M5_transport.latency_p99_ms, 0) }} ms
              <span class="dim">n={{ metrics.M5_transport.n }}</span>
            </td>
            <th>M5 故障</th>
            <td>拒连 {{ metrics.M5_transport.connection_errors }} ·
              超时 {{ metrics.M5_transport.timeouts }}
            </td>
          </tr>
          <tr>
            <th>M6 物理</th>
            <td>p99 {{ fmt(metrics.M6_physics.step_gap_p99_ms) }} ms ·
              max {{ fmt(metrics.M6_physics.step_gap_max_ms) }} ms ·
              超期 {{ metrics.M6_physics.deadline_miss }}
              <span class="dim">均值 {{ fmt(metrics.M6_physics.physics_hz_actual, 1) }} Hz
                （会因补跑偏高）</span>
            </td>
            <th>M2/M6 安全</th>
            <td>碰撞 tick {{ metrics.M6_physics.collision_ticks }} ·
              反射 {{ metrics.M6_physics.reflex_overrides }}
              <span v-if="metrics.M6_physics.cup_force_max_n">
                · 杯力 {{ fmt(metrics.M6_physics.cup_force_max_n) }} N</span>
            </td>
          </tr>
        </tbody>
      </table>

      <p class="ea-replay__note">
        回放是<strong>录制件</strong>：每一帧都来自一次真实运行（
        {{ data.meta.commit }}），指标条读的是同一次运行的
        <code>metrics</code>；但拖动时间轴不会触发新的推理，也不会改变结论。
      </p>
    </template>
  </div>
</template>

<style scoped>
.ea-replay { margin: 22px 0 8px; }
.ea-replay__err { color: var(--ea-red); font-size: 13px; }
.ea-replay__loading { color: var(--ea-muted); font-size: 13px; }

.ea-replay__meta { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.ea-replay__meta .tag {
  font: 11px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
  padding: 2px 8px; border: 1px solid var(--ea-line); border-radius: 3px;
  color: var(--ea-muted); background: var(--ea-panel);
}
.ea-replay__meta .tag.dim { color: var(--ea-dim); }

.ea-replay__stage {
  position: relative; border: 1px solid var(--ea-line); border-radius: 6px;
  background: var(--ea-bg-soft); overflow: hidden;
}
.ea-replay__stage svg { width: 100%; height: 100%; display: block; }

.floor { fill: #0a1013; }
.wall { fill: #8a4a1d; opacity: .85; }
.charger { fill: rgba(56, 132, 255, .10); stroke: rgba(56, 132, 255, .45); stroke-dasharray: 3 3; }
.charger-core { fill: rgba(56, 132, 255, .55); }
.intruder { fill: rgba(255, 77, 94, .30); stroke: var(--ea-red); stroke-width: .012; }
.cup { fill: rgba(120, 220, 255, .55); stroke: #7ad8ff; stroke-width: .01; }
.cup.broken { fill: rgba(150, 150, 150, .4); stroke: #888; }
.trail { fill: none; stroke: rgba(53, 208, 127, .35); stroke-width: .02; stroke-linejoin: round; }
.robot { fill: rgba(53, 208, 127, .30); stroke: var(--ea-green); stroke-width: .015; }
.robot.danger { fill: rgba(255, 77, 94, .28); stroke: var(--ea-red); }
.ee { stroke: #ffd166; stroke-width: .015; }
.lbl { font: 9px ui-monospace, monospace; fill: var(--ea-muted); }

.ea-replay__hud {
  position: absolute; top: 8px; left: 10px; display: flex; gap: 12px;
  font: 11px ui-monospace, monospace; color: var(--ea-muted);
  background: rgba(8, 11, 12, .72); padding: 4px 8px; border-radius: 4px;
  border: 1px solid var(--ea-line);
}
.ea-replay__hud b { color: var(--ea-text); }
.ea-replay__hud .hot { color: var(--ea-red); }

.ea-replay__recent {
  position: absolute; bottom: 8px; left: 10px; display: flex; gap: 6px; flex-wrap: wrap;
  max-width: 92%;
}
.ev {
  font: 10px/1.6 ui-monospace, monospace; padding: 1px 6px; border-radius: 3px;
  border: 1px solid var(--ea-line); background: rgba(8, 11, 12, .8); color: var(--ea-muted);
}
.ev.ok { border-color: rgba(53, 208, 127, .5); color: var(--ea-green); }
.ev.warn { border-color: rgba(255, 183, 77, .5); color: #ffb74d; }
.ev.bad { border-color: rgba(255, 77, 94, .5); color: var(--ea-red); }
.ev.need { border-color: rgba(120, 200, 255, .5); color: #7ac8ff; }

.ea-replay__bar {
  display: flex; align-items: center; gap: 8px; margin-top: 8px;
  font: 11px ui-monospace, monospace; color: var(--ea-muted);
}
.btn {
  font: 11px ui-monospace, monospace; padding: 3px 10px; cursor: pointer;
  background: var(--ea-panel); color: var(--ea-text); border: 1px solid var(--ea-line);
  border-radius: 3px;
}
.btn:hover { border-color: var(--ea-line-strong); }
.btn.ghost { color: var(--ea-muted); }
.range { flex: 1; accent-color: var(--ea-green); }
.clock { color: var(--ea-text); }
.frame { color: var(--ea-dim); }

.ea-replay__strip { margin-top: 8px; }
.row { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.row .k { width: 28px; font: 10px ui-monospace, monospace; color: var(--ea-dim); text-align: right; }
.spark {
  flex: 1; height: 34px; border: 1px solid var(--ea-line); border-radius: 3px;
  background: var(--ea-panel);
}
.spark .line { fill: none; stroke-width: 1.2; vector-effect: non-scaling-stroke; }
.spark .line.clear { stroke: #7ac8ff; }
.spark .line.batt { stroke: var(--ea-green); }
.spark .line.force { stroke: #ffd166; }
.spark .thr { stroke: rgba(255, 183, 77, .55); stroke-width: 1; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; }
.spark .thr.break { stroke: rgba(255, 77, 94, .7); }

.ticks { position: relative; height: 16px; margin: 2px 36px 0 36px; }
.tick {
  position: absolute; top: 3px; width: 2px; height: 10px; margin-left: -1px;
  background: var(--ea-dim); opacity: .6;
}
.tick.ok { background: var(--ea-green); }
.tick.warn { background: #ffb74d; }
.tick.bad { background: var(--ea-red); }
.tick.info { background: #7ac8ff; }
.mark {
  position: absolute; top: -2px; transform: translateX(-50%);
  font: 9px ui-monospace, monospace; padding: 0 4px; cursor: pointer;
  background: #ffb74d; color: #0b1013; border: 0; border-radius: 2px;
}
.mark.bad { background: var(--ea-red); color: #fff; }
.mark.ok { background: var(--ea-green); color: #06100b; }

.ea-replay__marks { margin: 8px 0 0; padding-left: 0; list-style: none; }
.ea-replay__marks li {
  display: flex; gap: 8px; align-items: baseline; font-size: 12.5px;
  color: var(--ea-muted); padding: 3px 0; border-bottom: 1px dashed var(--ea-line);
}
.jump {
  font: 10px ui-monospace, monospace; padding: 0 5px; cursor: pointer;
  background: transparent; color: #ffb74d; border: 1px solid rgba(255, 183, 77, .5);
  border-radius: 2px;
}
.mk-t { font: 11px ui-monospace, monospace; color: var(--ea-dim); min-width: 44px; }
.mk-l { color: var(--ea-text); }

.ea-replay__note {
  margin-top: 10px; font-size: 12px; color: var(--ea-dim); line-height: 1.7;
}

.ea-replay__metrics {
  width: 100%; margin: 10px 0 0; border-collapse: collapse;
  font: 11.5px ui-monospace, monospace;
}
.ea-replay__metrics th {
  text-align: left; font-weight: normal; color: var(--ea-dim);
  padding: 5px 8px 5px 0; border-bottom: 1px solid var(--ea-line); white-space: nowrap;
  background: transparent;
}
.ea-replay__metrics td {
  padding: 5px 14px 5px 0; border-bottom: 1px solid var(--ea-line);
  color: var(--ea-text); white-space: nowrap;
}
.ea-replay__metrics td.bad { color: var(--ea-dim); }
.ea-replay__metrics .dim { color: var(--ea-dim); }
</style>
