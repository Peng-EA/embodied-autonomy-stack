import * as THREE from 'three';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import './style.css';
import { SCENARIOS } from '../core/scenario.ts';
import { BASELINES } from '../core/baselines.ts';

const $ = (id: string) => document.getElementById(id)!;

// ---------------- 场景（Three.js） ----------------
const canvas = $('scene') as HTMLCanvasElement;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x080b0c);
scene.fog = new THREE.Fog(0x080b0c, 9, 16);

const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 60);
camera.position.set(3.4, 4.6, -4.6);
camera.lookAt(0, 0, 0.4);

scene.add(new THREE.HemisphereLight(0x9fb3c8, 0x0b0f12, 0.9));
const dir = new THREE.DirectionalLight(0xffffff, 1.6);
dir.position.set(4, 8, -4);
scene.add(dir);

const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(14, 14),
  new THREE.MeshStandardMaterial({ color: 0x0f1418, roughness: 0.95 }),
);
ground.rotation.x = -Math.PI / 2;
scene.add(ground);

const grid = new THREE.GridHelper(14, 28, 0x1f2937, 0x141b21);
scene.add(grid);

const wallMat = new THREE.MeshStandardMaterial({ color: 0x374151, roughness: 0.8 });
const wall = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.4, 0.12), wallMat);
wall.position.set(0, 0.2, 0);
scene.add(wall);

const robot = new THREE.Mesh(
  new THREE.BoxGeometry(0.36, 0.24, 0.5),
  new THREE.MeshStandardMaterial({ color: 0x4ade80, emissive: 0x0a2814 }),
);
robot.position.set(0, 0.121, -1.6);
scene.add(robot);

const dock = new THREE.Mesh(
  new THREE.CylinderGeometry(0.34, 0.38, 0.08, 24),
  new THREE.MeshStandardMaterial({ color: 0x60a5fa, emissive: 0x0b1f3a }),
);
dock.position.set(0, 0.04, 1.6);
scene.add(dock);

let scenarioId = 'stage2-moving';
function applyScenario(id: string) {
  scenarioId = id;
  const sc = SCENARIOS[id];
  wall.scale.set((sc.wall.hx * 2) / 1.8, 1, (sc.wall.hz * 2) / 0.12);
  wall.position.set(sc.wall.cx, 0.2, sc.wall.cz);
  robot.position.set(sc.start.x, 0.121, sc.start.z);
}
applyScenario(scenarioId);

function resize() {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);

// ---------------- 图表（uPlot） ----------------
const MAXP = 400;
const chartData: number[][] = [[], [], [], []];
const chart = new uPlot(
  {
    width: 348,
    height: 148,
    cursor: { show: false },
    legend: { show: true },
    scales: { x: { time: false } },
    axes: [
      { stroke: '#6b7280', grid: { stroke: '#1f2937' }, ticks: { show: false } },
      { stroke: '#6b7280', grid: { stroke: '#1f2937' }, size: 44 },
    ],
    series: [
      { label: 't' },
      { label: '电量%', stroke: '#4ade80', width: 1.5 },
      { label: '间隙m', stroke: '#fbbf24', width: 1.5 },
      { label: '速度', stroke: '#60a5fa', width: 1.5 },
    ],
  },
  chartData,
  $('chart'),
);

// ---------------- Worker ----------------
const worker = new Worker(new URL('../worker/sim.worker.ts', import.meta.url), { type: 'module' });
let running = false;

worker.onmessage = (ev: MessageEvent) => {
  const m = ev.data;
  if (m.type === 'frame') {
    robot.position.set(m.x, 0.121, m.z);
    dock.position.set(m.dock.x, 0.04, m.dock.z);
    robot.material.color.setHex(m.degraded ? 0xfbbf24 : 0x4ade80);
    $('rt').textContent = m.t.toFixed(2) + ' s';
    $('rb').textContent = m.battery.toFixed(1) + ' %';
    $('rc').textContent = m.clearance.toFixed(2) + ' m';
    $('rv').textContent = m.speed.toFixed(2) + ' m/s';
    $('rs').textContent = String(m.stale);
    $('rd').textContent = m.degraded ? '是' : '否';

    chartData[0].push(m.t);
    chartData[1].push(m.battery);
    chartData[2].push(Math.min(m.clearance, 3));
    chartData[3].push(m.speed);
    while (chartData[0].length > MAXP) {
      chartData.forEach((a) => a.shift());
    }
    chart.setData(chartData);

    if (m.events?.length) {
      const ul = $('eventlist');
      for (const e of m.events) {
        const li = document.createElement('li');
        li.innerHTML = '<b>' + e.t.toFixed(2) + 's</b> ' + e.layer + ' ' + e.event;
        ul.prepend(li);
      }
      while (ul.childElementCount > 14) ul.lastElementChild!.remove();
    }
  } else if (m.type === 'done') {
    running = false;
    ($('run') as HTMLButtonElement).textContent = '重跑';
  }
};

function start() {
  chartData.forEach((a) => (a.length = 0));
  $('eventlist').innerHTML = '';
  applyScenario(scenarioId);
  worker.postMessage({
    type: 'start',
    opts: {
      scenario: scenarioId,
      baseline: ($('baseline') as HTMLSelectElement).value,
      latencyS: Number(($('latency') as HTMLSelectElement).value),
      seed: 7,
    },
  });
  running = true;
  ($('run') as HTMLButtonElement).textContent = '重跑';
}

// ---------------- 控件 ----------------
const selScenario = $('scenario') as HTMLSelectElement;
for (const [id, sc] of Object.entries(SCENARIOS)) {
  selScenario.add(new Option(sc.label, id));
}
selScenario.value = scenarioId;
selScenario.onchange = () => {
  applyScenario(selScenario.value);
  if (running) start();
};

const selBaseline = $('baseline') as HTMLSelectElement;
for (const [id, b] of Object.entries(BASELINES)) {
  if (id === 'b0-legacy') continue;
  selBaseline.add(new Option(id + ' · ' + b.label, id));
}
selBaseline.value = 'full';

const selLatency = $('latency') as HTMLSelectElement;
for (const v of [0.1, 0.35, 0.7, 1.2, 2.0]) selLatency.add(new Option(v + ' s', String(v)));
selLatency.value = '0.35';

($('run') as HTMLButtonElement).onclick = start;
($('reset') as HTMLButtonElement).onclick = () => {
  worker.postMessage({ type: 'reset' });
  running = false;
  chartData.forEach((a) => (a.length = 0));
  chart.setData(chartData);
  $('eventlist').innerHTML = '';
  applyScenario(scenarioId);
};

// ---------------- 渲染循环 ----------------
function animate() {
  requestAnimationFrame(animate);
  renderer.render(scene, camera);
}
resize();
animate();
