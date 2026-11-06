import * as THREE from 'three';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import './style.css';

const $ = (id: string) => document.getElementById(id)!;

// ---------------------------------------------------------------- 场景
const canvas = $('scene') as HTMLCanvasElement;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xeef2f6);

const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 80);
camera.position.set(2.3, 1.95, 3.5);
camera.lookAt(0, 1.02, 0.2);

scene.add(new THREE.HemisphereLight(0xffffff, 0xdfe6ee, 1.15));
const sun = new THREE.DirectionalLight(0xffffff, 1.5);
sun.position.set(4, 7, 5);
sun.castShadow = true;
sun.shadow.mapSize.set(1024, 1024);
sun.shadow.camera.left = -6; sun.shadow.camera.right = 6;
sun.shadow.camera.top = 6; sun.shadow.camera.bottom = -6;
scene.add(sun);

const mat = (c: number, r = 0.9) => new THREE.MeshStandardMaterial({ color: c, roughness: r });
const box = (w: number, h: number, d: number, c: number, x: number, y: number, z: number) => {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat(c));
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  scene.add(m);
  return m;
};

// 房间
box(8, 0.1, 8, 0xefe0d0, 0, -0.05, 0);          // 地板
box(8, 3.2, 0.12, 0xf6efe6, 0, 1.6, -4);        // 后墙
box(0.12, 3.2, 8, 0xf1e7e2, -4, 1.6, 0);        // 左墙
box(1.1, 0.8, 0.05, 0xf3d3d8, 1.9, 1.9, -3.93); // 墙上的画
box(0.9, 0.06, 0.06, 0xd8c3b2, 1.9, 1.44, -3.9); // 画框底沿
box(2.0, 0.08, 0.5, 0xe7d6c4, -1.6, 1.5, -3.85); // 搁板
for (let i = 0; i < 4; i++) box(0.12, 0.22, 0.16, [0xc9d7c0, 0xe6c9c9, 0xd6cfe6, 0xe8dcc4][i], -2.3 + i * 0.22, 1.65, -3.85);
box(3.4, 1.6, 0.06, 0xdfe9f5, -1.1, 1.9, -3.93); // 窗
box(3.6, 0.08, 0.1, 0xffffff, -1.1, 1.08, -3.9); // 窗台
box(2.6, 0.02, 2.0, 0xf6dfe4, 0, 0.02, 0.6);    // 地毯

// 家具
box(1.5, 0.72, 0.7, 0xece3d5, 1.7, 0.36, -2.4); // 桌
box(1.5, 0.06, 0.7, 0xffffff, 1.7, 0.74, -2.4);
box(0.5, 0.5, 0.5, 0xdfe7ea, -1.9, 0.25, -1.0); // 凳
box(1.6, 0.45, 2.0, 0xf0e6ea, -2.4, 0.22, 1.4); // 床
box(1.6, 0.18, 0.5, 0xffffff, -2.4, 0.53, 0.6); // 枕
const plant = new THREE.Group();
const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.14, 0.3, 16), mat(0xd9c3b0));
pot.position.y = 0.15; plant.add(pot);
for (let i = 0; i < 4; i++) {
  const leaf = new THREE.Mesh(new THREE.SphereGeometry(0.16, 10, 8), mat(0x9fbf9a));
  leaf.position.set(Math.cos(i * 1.7) * 0.12, 0.42 + i * 0.08, Math.sin(i * 1.7) * 0.12);
  leaf.scale.set(1, 1.5, 1);
  plant.add(leaf);
}
plant.position.set(2.9, 0, -3.2);
scene.add(plant);

// ---------------------------------------------------------------- 角色（程序化占位，可换 VRM）
const char = new THREE.Group();
const skin = mat(0xf9ddca), cloth = mat(0x2b2f3a), hair = mat(0xded8e8), accent = mat(0x8b7bd8);

const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.18, 0.42, 6, 16), cloth);
body.position.y = 0.86; body.castShadow = true; char.add(body);

const skirt = new THREE.Mesh(new THREE.ConeGeometry(0.3, 0.34, 20), cloth);
skirt.position.y = 0.55; skirt.castShadow = true; char.add(skirt);

const head = new THREE.Mesh(new THREE.SphereGeometry(0.21, 24, 18), skin);
head.position.y = 1.34; head.castShadow = true; char.add(head);

const hairCap = new THREE.Mesh(new THREE.SphereGeometry(0.225, 24, 18, 0, Math.PI * 2, 0, Math.PI * 0.62), hair);
hairCap.position.y = 1.35; char.add(hairCap);
for (const s of [-1, 1]) {
  const tail = new THREE.Mesh(new THREE.CapsuleGeometry(0.05, 0.34, 4, 10), hair);
  tail.position.set(0.19 * s, 1.16, -0.06);
  tail.rotation.z = 0.22 * s;
  char.add(tail);
}
const eyeMat = new THREE.MeshBasicMaterial({ color: 0x2b2f3a });
for (const s of [-1, 1]) {
  const eye = new THREE.Mesh(new THREE.SphereGeometry(0.032, 14, 12), eyeMat);
  eye.position.set(0.075 * s, 1.35, 0.198);
  eye.scale.set(0.8, 1.2, 0.55);
  char.add(eye);
  const blush = new THREE.Mesh(new THREE.CircleGeometry(0.028, 12), new THREE.MeshBasicMaterial({ color: 0xf6b8c0, transparent: true, opacity: 0.85 }));
  blush.position.set(0.125 * s, 1.29, 0.186);
  char.add(blush);
}
const mouth = new THREE.Mesh(new THREE.TorusGeometry(0.022, 0.006, 6, 12, Math.PI), new THREE.MeshBasicMaterial({ color: 0xd98a92 }));
mouth.position.set(0, 1.275, 0.204);
mouth.rotation.z = Math.PI;
char.add(mouth);
// 刘海（压在额头上方，不能挡住眼睛——第一版挡死了，脸看着像后脑勺）
for (let i = -2; i <= 2; i++) {
  const bang = new THREE.Mesh(new THREE.SphereGeometry(0.07, 12, 10), hair);
  bang.position.set(0.072 * i, 1.465 - Math.abs(i) * 0.01, 0.145);
  bang.scale.set(0.9, 0.75, 0.5);
  char.add(bang);
}
// 高光（让眼睛有神）
for (const s of [-1, 1]) {
  const hi = new THREE.Mesh(new THREE.SphereGeometry(0.011, 8, 6), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  hi.position.set(0.083 * s, 1.362, 0.214);
  char.add(hi);
}

const arms: THREE.Mesh[] = [];
const legs: THREE.Mesh[] = [];
for (const s of [-1, 1]) {
  const arm = new THREE.Mesh(new THREE.CapsuleGeometry(0.052, 0.34, 4, 10), skin);
  arm.position.set(0.24 * s, 0.98, 0);
  arm.rotation.z = 0.12 * s;
  arm.castShadow = true;
  char.add(arm); arms.push(arm);

  const leg = new THREE.Mesh(new THREE.CapsuleGeometry(0.06, 0.34, 4, 10), skin);
  leg.position.set(0.09 * s, 0.34, 0);
  leg.castShadow = true;
  char.add(leg); legs.push(leg);
}

char.position.set(0, 0, 0.4);
char.rotation.y = 0; // 默认面向镜头（+Z）
scene.add(char);

// ---------------------------------------------------------------- 动作状态机
type Mode = 'idle' | 'walk' | 'sit' | 'wave';
let mode: Mode = 'idle';
let target: THREE.Vector3 | null = null;
let waveUntil = 0;
const spots: Record<string, [number, number]> = {
  桌边: [1.2, -1.6],
  床边: [-1.6, 1.0],
  窗边: [-1.1, -2.4],
  中间: [0, 0.4],
  原地: [0, 0.4],
};

function go(name: string) {
  const p = spots[name];
  if (!p) return;
  target = new THREE.Vector3(p[0], 0, p[1]);
  mode = 'walk';
  say('收到，去' + name + '。');
  setAction('行走 · ' + name);
}

function wave() {
  mode = 'wave';
  waveUntil = performance.now() + 1800;
  say('好呀。');
  setAction('挥手');
}

function sit() {
  mode = 'sit';
  say('坐下休息一会儿。');
  setAction('坐姿');
}

// ---------------------------------------------------------------- 气泡与字幕
let bubbleTimer = 0;
function say(text: string) {
  const b = $('bubble');
  b.textContent = text;
  b.classList.remove('hidden');
  clearTimeout(bubbleTimer);
  bubbleTimer = window.setTimeout(() => b.classList.add('hidden'), 4200);
  pushLog('bot', text);
}
function setAction(t: string) {
  $('action').textContent = t;
}

const chapters = [
  ['01', '全新场景', '从人物细节到自然待机，让表现与交互更真实'],
  ['02', '语言的边界', '同一句话，在场景里落成不同的动作'],
  ['03', '从语言到物体交互', '交互对象不必预设，场景可以任意'],
  ['04', '连续任务', '从理解一句话，到一步步走完一件事'],
];
let chapterIdx = 0;
function setChapter(i: number) {
  chapterIdx = i;
  const c = chapters[i % chapters.length];
  $('chapter').textContent = c[0];
  $('chapter-title').textContent = c[1];
  $('chapter-sub').textContent = c[2];
  document.querySelectorAll('#stages button').forEach((b, k) => b.classList.toggle('on', k === i));
}
const nav = $('stages');
chapters.forEach((c, i) => {
  const b = document.createElement('button');
  b.textContent = c[0] + ' ' + c[1];
  b.onclick = () => setChapter(i);
  nav.appendChild(b);
});
setChapter(0);

// ---------------------------------------------------------------- 对话
const log = $('log');
function pushLog(who: 'me' | 'bot', text: string) {
  const li = document.createElement('li');
  li.className = who;
  li.textContent = text;
  log.appendChild(li);
  log.scrollTop = log.scrollHeight;
}

function handle(text: string) {
  pushLog('me', text);
  const t = text.trim();
  if (/桌|写|拿/.test(t)) go('桌边');
  else if (/床|躺|睡/.test(t)) go('床边');
  else if (/窗|看/.test(t)) go('窗边');
  else if (/坐/.test(t)) sit();
  else if (/挥|招|你好|嗨/.test(t)) wave();
  else if (/回来|中间|站/.test(t)) go('中间');
  else say('这句我还接不住，换个说法试试（去桌边 / 坐下 / 挥挥手）。');
}

($('say') as HTMLFormElement).onsubmit = (e) => {
  e.preventDefault();
  const input = $('msg') as HTMLInputElement;
  const v = input.value;
  if (!v.trim()) return;
  input.value = '';
  handle(v);
};

// ---------------------------------------------------------------- 指标与图表
const gauges = { g1: 0.92, g2: 0.48 };
function setGauge(id: 'g1' | 'g2', v: number) {
  const el = $(id) as unknown as SVGCircleElement;
  const len = 188;
  el.style.strokeDashoffset = String(len * (1 - v));
  $(id + 'v').textContent = String(Math.round(v * 100));
}
setGauge('g1', gauges.g1);
setGauge('g2', gauges.g2);

function mkChart(el: string, color: string) {
  const data: number[][] = [[], []];
  const u = new uPlot(
    {
      width: 388,
      height: 46,
      cursor: { show: false },
      legend: { show: false },
      scales: { x: { time: false } },
      axes: [{ show: false }, { show: false }],
      series: [{}, { stroke: color, width: 1.4, fill: color + '22' }],
    },
    data,
    $(el),
  );
  return { u, data };
}
const c1 = mkChart('c1', '#111827');
const c2 = mkChart('c2', '#7c9cff');
const c3 = mkChart('c3', '#e08a8a');

let phase = 0;
function sample() {
  phase += 0.08;
  const push = (c: { u: uPlot; data: number[][] }, v: number) => {
    c.data[0].push(phase);
    c.data[1].push(v);
    if (c.data[0].length > 90) { c.data[0].shift(); c.data[1].shift(); }
    c.u.setData(c.data);
  };
  push(c1, 0.5 + 0.35 * Math.sin(phase * 1.7) + 0.12 * Math.sin(phase * 5.3));
  push(c2, 0.4 + 0.3 * Math.sin(phase * 0.9 + 1.2) + (mode === 'walk' ? 0.25 : 0));
  push(c3, mode === 'sit' ? 0.15 : 0.08 + Math.abs(Math.sin(phase * 2.2)) * 0.1);

  const load = Math.min(1, 0.62 + (mode === 'walk' ? 0.3 : 0) + 0.05 * Math.sin(phase));
  setGauge('g1', load);
  $('m-idle').textContent = (0.5 + 0.02 * Math.sin(phase)).toFixed(3) + ' s';
  $('m-sem').textContent = (13.8 + 2 * Math.sin(phase * 0.7)).toFixed(1) + ' ms';
  $('m-ctl').textContent = (0.000 + (mode === 'walk' ? 0.012 : 0)).toFixed(3) + ' ms';
}
setInterval(sample, 120);

// ---------------------------------------------------------------- 循环
let paused = false;
$('btn-pause').onclick = () => {
  paused = !paused;
  ($('btn-pause') as HTMLButtonElement).textContent = paused ? '继续' : '暂停';
};
$('btn-reset').onclick = () => {
  char.position.set(0, 0, 0.4);
  mode = 'idle';
  target = null;
  setAction('待机');
  setChapter(0);
};

const clock = new THREE.Clock();
let elapsed = 0;

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.05);
  if (!paused) elapsed += dt;

  // 待机呼吸
  const bob = Math.sin(elapsed * 1.6) * 0.012;
  char.position.y = bob;
  head.rotation.z = Math.sin(elapsed * 0.9) * 0.03;
  hairCap.rotation.z = head.rotation.z;

  if (mode === 'walk' && target) {
    const dir = target.clone().sub(char.position);
    dir.y = 0;
    const d = dir.length();
    if (d < 0.08) {
      mode = 'idle';
      target = null;
      setAction('待机');
    } else {
      dir.normalize();
      char.position.addScaledVector(dir, 1.15 * dt);
      char.rotation.y = Math.atan2(dir.x, dir.z);
      const swing = Math.sin(elapsed * 9) * 0.5;
      legs[0].rotation.x = swing;
      legs[1].rotation.x = -swing;
      arms[0].rotation.x = -swing * 0.6;
      arms[1].rotation.x = swing * 0.6;
    }
  } else {
    legs.forEach((l) => (l.rotation.x *= 0.8));
    arms.forEach((a) => (a.rotation.x *= 0.8));
  }

  if (mode === 'wave') {
    arms[1].rotation.z = -1.9 + Math.sin(elapsed * 14) * 0.35;
    if (performance.now() > waveUntil) {
      mode = 'idle';
      arms[1].rotation.z = -0.12;
      setAction('待机');
    }
  }
  if (mode === 'sit') {
    char.position.y = -0.18;
    legs[0].rotation.x = -1.3;
    legs[1].rotation.x = -1.3;
  }

  renderer.render(scene, camera);
}

function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);

setInterval(() => {
  const d = new Date();
  $('clock').textContent = d.toTimeString().slice(0, 8);
}, 1000);

resize();
animate();
pushLog('bot', '准备好了。说个地方，我走过去；或者说「挥挥手」。');
