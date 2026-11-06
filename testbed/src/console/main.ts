import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { MMDLoader } from '../vendor/MMDLoader.js';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import './style.css';
import { Vitals } from './vitals.ts';
import { Planner, STALE_AGE_MS } from './plan.ts';
import { Affect } from './affect.ts';

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

// PBR 角色靠环境光照亮：只给方向光会把浅蓝渲染成墨蓝
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.45;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;

scene.add(new THREE.HemisphereLight(0xffffff, 0xdcd2c6, 0.5));
const sun = new THREE.DirectionalLight(0xfff6ea, 1.15);
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
box(8, 3.2, 0.12, 0xe9dfd3, 0, 1.6, -4);        // 后墙
box(0.12, 3.2, 8, 0xe4d8d0, -4, 1.6, 0);        // 左墙
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

// 程序化占位：模型加载成功后整组移除（保留作离线兜底）
const proxyParts = [...char.children];
let usingModel = false;
const morphs: { dict: Record<string, number>; infl: number[] }[] = [];

// 骨骼微动作：MMD 骨骼名是日文，按关键词抓几根做呼吸/环视/抬手
const boneRig: { b: THREE.Bone; rest: THREE.Quaternion; kind: string }[] = [];

// Mixamo 标准骨名（这套模型是 mixamorig:*），用来做"不掰折关节"的整身动作
const HUMAN: Record<string, THREE.Bone> = {};
const HUMAN_KEYS = ['Hips','Spine','Spine1','Spine2','Neck','Head','LeftArm','RightArm','LeftForeArm','RightForeArm','LeftUpLeg','RightUpLeg','LeftLeg','RightLeg','LeftFoot','RightFoot'];
const restQ = new Map<THREE.Bone, THREE.Quaternion>();

function indexHumanoid(model: any) {
  model.traverse((o: any) => {
    if (!o.isBone) return;
    const n = String(o.name).replace('mixamorig', '').replace(/^:/, '');
    if (HUMAN_KEYS.includes(n)) HUMAN[n] = o;
  });
}

/** 只绕单轴小角度旋转，rest * delta：这是"摆"，不是"拧" */
function poseBone(name: string, ex: number, ey = 0, ez = 0) {
  const b = HUMAN[name];
  if (!b) return;
  let rest = restQ.get(b);
  if (!rest) { rest = b.quaternion.clone(); restQ.set(b, rest); }
  const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(ex, ey, ez));
  b.quaternion.copy(rest).multiply(q);
}

function installModel(gltf: any, label: string) {
  proxyParts.forEach((o) => char.remove(o));
  const model = gltf.scene;
  const box = new THREE.Box3().setFromObject(model);
  const h = box.max.y - box.min.y;
  const s = 1.55 / h; // 缩放到 1.55 m 高
  model.scale.setScalar(s);
  const b2 = new THREE.Box3().setFromObject(model);
  model.position.y -= b2.min.y;
  model.traverse((o: any) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
      if (o.material) {
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        for (const m of mats) {
          if (m.map) m.map.anisotropy = 4;
          if ('roughness' in m) m.roughness = Math.min(0.9, m.roughness ?? 0.8);
        }
      }
      if (o.morphTargetDictionary && o.morphTargetInfluences) {
        morphs.push({ dict: o.morphTargetDictionary, infl: o.morphTargetInfluences });
      }
    }
  });
  indexHumanoid(model);
  char.add(model);
  usingModel = true;
  setAction('待机 · ' + label);
}

// 先试 MMD 原生格式（PMX 直接读，蒙皮/材质由 MMDLoader 处理，比转 glTF 稳）
const gltfLoader = new GLTFLoader();

function installMMD(mesh: any, label: string) {
  proxyParts.forEach((o) => char.remove(o));
  // MMD 模型约 20 单位高；用固定比例（按包围盒算会被光翼撑大，人缩成一小坨）
  mesh.rotation.y = 0; // 这套 MMD 默认朝 +Z（朝镜头）
  mesh.scale.setScalar(0.078);
  const b2 = new THREE.Box3().setFromObject(mesh);
  mesh.position.y -= b2.min.y;
  mesh.frustumCulled = false;
  mesh.traverse((o: any) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
  });
  for (const b of (mesh.skeleton?.bones ?? []) as any[]) {
    const n = String(b.name);
    const kind = n.includes('頭') ? 'head'
      : n === '首' ? 'neck'
      : n === '上半身' || n === '上半身2' ? 'spine'
      : n === '右腕' ? 'armR'
      : n === '左腕' ? 'armL'
      : n === '右ひじ' ? 'elbowR'
      : n === '左ひじ' ? 'elbowL'
      : '';
    if (kind) boneRig.push({ b, rest: b.quaternion.clone(), kind });
  }
  char.add(mesh);
  usingModel = true;
  setAction('待机 · ' + label);
}

// 加载顺序：DeepSeek 娘（本地）→ 银狼（本地）→ 小鲸鲸（仓库内 CC BY）→ 占位
gltfLoader.load(
  'models/deepseek.glb',
  (gltf) => installModel(gltf, 'DeepSeek 娘'),
  undefined,
  () => {
    new MMDLoader().load(
      'models/silverwolf/silverwolf.pmx',
      (mesh: any) => installMMD(mesh, '银狼'),
      undefined,
      () => {
        gltfLoader.load(
          'models/whale-child.glb',
          (gltf) => installModel(gltf, '小鲸鲸'),
          undefined,
          (err) => console.warn('模型都没加载成功，继续用占位角色', err),
        );
      },
    );
  },
);

// ---------------------------------------------------------------- L2：障碍与视线
const OBSTACLES = [
  { cx: 0.45, cz: -0.25, hx: 0.34, hz: 0.26, name: '矮桌' },
  { cx: -0.45, cz: 0.62, hx: 0.18, hz: 0.18, name: '边几' },
];
for (const o of OBSTACLES) {
  box(o.hx * 2, 0.32, o.hz * 2, 0xe7dccd, o.cx, 0.16, o.cz);
}

function blockedAt(x: number, z: number): string | null {
  const r = 0.26; // 角色半径
  for (const o of OBSTACLES) {
    if (Math.abs(x - o.cx) < o.hx + r && Math.abs(z - o.cz) < o.hz + r) return o.name;
  }
  return null;
}

// 视线：指针位置 → 头部偏航/俯仰（L2 的"追随"，<100 ms 级别）
const look = { yaw: 0, pitch: 0 };
window.addEventListener('pointermove', (e) => {
  const nx = (e.clientX / window.innerWidth) * 2 - 1;
  const ny = (e.clientY / window.innerHeight) * 2 - 1;
  look.yaw = -nx * 0.55;
  look.pitch = -ny * 0.28;
});

const reflex = { until: 0, vx: 0, vz: 0, cooldown: 0, count: 0 };

// ---------------------------------------------------------------- 动作状态机
type Mode = 'idle' | 'walk' | 'sit' | 'wave';
let mode: Mode = 'idle';
let target: THREE.Vector3 | null = null;
let waveUntil = 0;
const spots: Record<string, [number, number]> = {
  桌边: [1.05, -1.15],
  床边: [-1.25, 0.95],
  窗边: [-0.95, -1.95],
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
  ['01', 'L1 · 内感受', '饿、渴、体力、体温按 100 Hz 积分'],
  ['02', 'L2 · 反射', '视线追随、指令打断，< 100 ms'],
  ['03', 'L3 · 调制', '情绪只改参数，不改安全判定'],
  ['04', 'L4 · 规划', '异步意图；过期作废或降级'],
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
function pushLog(who: 'me' | 'bot' | 'sys', text: string) {
  const li = document.createElement('li');
  li.className = who;
  li.textContent = text;
  log.appendChild(li);
  log.scrollTop = log.scrollHeight;
}

function handle(text: string) {
  pushLog('me', text);
  // L2：用户指令优先——正在走的计划立刻作废
  if (mode === 'walk' || target) {
    target = null;
    pushLog('sys', 'L2 打断 · 用户指令优先');
    lastPlanAt = elapsed;
  }
  affect.onCommand();
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

const vitals = new Vitals();
const affect = new Affect();
const planner = new Planner((import.meta as any).env?.VITE_PLAN_URL ?? 'http://127.0.0.1:8787');

// 演示种子：?vitals=thirsty 直接把口渴拉到 0.82，省得等 20 秒（仅影响初值）
if (new URLSearchParams(location.search).get('vitals') === 'thirsty') {
  vitals.thirst = 0.82;
  vitals.hunger = 0.35;
}

let phase = 0;
function sample() {
  phase += 0.12;
  const push = (c: { u: uPlot; data: number[][] }, v: number) => {
    c.data[0].push(phase);
    c.data[1].push(v);
    if (c.data[0].length > 90) { c.data[0].shift(); c.data[1].shift(); }
    c.u.setData(c.data);
  };
  // 三条曲线都是真值：L1 需求压力、L1 体力、L4 计划年龄
  push(c1, vitals.pressure());
  push(c2, affect.fear);
  push(c3, Math.min(4000, planner.lastAgeMs));

  setGauge('g1', vitals.pressure());
  setGauge('g2', Math.min(1, planner.lastAgeMs / 4000));
  $('g2v').textContent = String(planner.lastAgeMs);

  $('m-idle').textContent = (Math.abs(char.position.y) * 1000).toFixed(1) + ' mm';
  $('m-sem').textContent = planner.latencyMs ? planner.latencyMs + ' ms' : '— ms';
  $('m-ctl').textContent = '10.0 ms';

  setBar('v-hunger', 'v-hunger-n', vitals.hunger, (v) => Math.round(v * 100) + '%');
  setBar('v-thirst', 'v-thirst-n', vitals.thirst, (v) => Math.round(v * 100) + '%');
  setBar('v-energy', 'v-energy-n', vitals.energy, (v) => Math.round(v * 100) + '%', true);
  setBar(
    'v-temp',
    'v-temp-n',
    Math.min(1, Math.max(0, (vitals.temperature - 36) / 2)),
    () => vitals.temperature.toFixed(1) + '°',
  );

  const pct = (v: number) => Math.round(v * 100) + '%';
  setBar('a-fear', 'a-fear-n', affect.fear, pct);
  setBar('a-curiosity', 'a-curiosity-n', affect.curiosity, pct, true);
  setBar('a-fatigue', 'a-fatigue-n', affect.fatigue, pct);
  setBar('a-comfort', 'a-comfort-n', affect.comfort(vitals.pressure()), pct, true);
}

function setBar(barId: string, numId: string, v: number, fmt: (v: number) => string, invert = false) {
  const bar = $(barId) as HTMLElement;
  bar.style.width = Math.round(Math.min(1, Math.max(0, v)) * 100) + '%';
  const danger = invert ? v < 0.25 : v > 0.8;
  const warn = invert ? v < 0.45 : v > 0.6;
  bar.className = danger ? 'bad' : warn ? 'warn' : '';
  $(numId).textContent = fmt(v);
}

// L4：需求压力过线才问服务；结果过期就作废重来，不拿旧计划开车。
let planning = false;
let lastPlanAt = -99;
setInterval(async () => {
  if (paused || planning) return;
  if (!usingModel) return;
  if (vitals.pressure() < 0.78) return;
  if (elapsed - lastPlanAt < 9) return;

  planning = true;
  lastPlanAt = elapsed;
  const simMs = Math.round(elapsed * 1000);
  const out = await planner.request(vitals, simMs);
  planning = false;

  $('l4-src').textContent = out.provider + ' · ' + out.latencyMs + ' ms';

  if (out.stale) {
    pushLog('sys', '计划过期 age=' + out.ageMs + 'ms（阈值 ' + STALE_AGE_MS + '）→ 作废，重新规划');
    setAction('计划过期 · 重新规划');
    lastPlanAt = elapsed - 5.5;
    return;
  }
  if (out.intent.kind === 'goto' && out.intent.target) {
    const d = Math.hypot(char.position.x - out.intent.target.x, char.position.z - out.intent.target.z);
    if (d < 0.4) return; // 已经在该做的事旁边，别原地反复下令
    target = new THREE.Vector3(out.intent.target.x, 0, out.intent.target.z);
    mode = 'walk';
    pushLog('sys', 'L4 采纳 · ' + out.provider + ' · age=' + out.ageMs + 'ms · ' + (out.intent.why || ''));
    say(out.intent.say || '我过去看看。');
    setAction('行走 · L4 意图');
  }
}, 700);

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
  vitals.reset();
  affect.reset();
  planner.lastAgeMs = 0;
  planner.latencyMs = 0;
  $('l4-src').textContent = 'server 未连接';
};

const clock = new THREE.Clock();
let elapsed = 0;

const camOffset = new THREE.Vector3(2.3, 1.95, 3.5);
const camTarget = new THREE.Vector3(0, 1.02, 0.2);

function animate() {
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(), 0.05);
  if (!paused) {
    elapsed += dt;
    vitals.tick(dt, mode === 'walk', mode === 'sit');
    affect.tick(dt, mode === 'walk');
    // 到达对应地点就开始"解决需求"：渴→水杯，饿→冰箱，累→床
    const px = char.position.x;
    const pz = char.position.z;
    const near = (x: number, z: number, r = 0.75) => Math.hypot(px - x, pz - z) < r;
    if (near(-1.25, 0.95)) vitals.thirst = Math.max(0, vitals.thirst - 0.25 * dt);
    if (near(1.05, -1.15)) vitals.hunger = Math.max(0, vitals.hunger - 0.2 * dt);
    if (near(-0.95, -1.95)) vitals.energy = Math.min(1, vitals.energy + 0.08 * dt);
  }

  // 待机呼吸
  const bob = Math.sin(elapsed * 1.6) * (usingModel ? 0.025 : 0.012);
  char.position.y = bob;
  if (!usingModel) {
    head.rotation.z = Math.sin(elapsed * 0.9) * 0.03;
    hairCap.rotation.z = head.rotation.z;
  } else if (Object.keys(HUMAN).length) {
    // Mixamo 骨架：只做"摆动"（单轴小角度），不给力矩、不做物理驱动
    const ph = elapsed * 7.6;
    const walking = mode === 'walk';
    poseBone('LeftUpLeg', walking ? Math.sin(ph) * 0.55 : 0);
    poseBone('RightUpLeg', walking ? -Math.sin(ph) * 0.55 : 0);
    poseBone('LeftLeg', walking ? Math.max(0, -Math.sin(ph)) * 0.75 : 0);
    poseBone('RightLeg', walking ? Math.max(0, Math.sin(ph)) * 0.75 : 0);
    poseBone('LeftArm', walking ? -Math.sin(ph) * 0.32 : Math.sin(elapsed * 1.1) * 0.03);
    if (mode === 'wave') {
      poseBone('RightArm', 0, 0, -0.95);
      poseBone('RightForeArm', 0, 0, -0.45 + Math.sin(elapsed * 9) * 0.2);
    } else {
      poseBone('RightArm', walking ? Math.sin(ph) * 0.32 : Math.sin(elapsed * 1.1 + 1) * 0.03);
      poseBone('RightForeArm', 0);
    }
    poseBone('Spine', Math.sin(elapsed * 1.25) * 0.018);
    // 视线：指针 → 头/颈；强度由 L3 的好奇/恐惧调制
    const gain = affect.gazeGain();
    poseBone('Neck', look.pitch * 0.35 * gain, look.yaw * 0.35 * gain);
    poseBone('Head', Math.sin(elapsed * 0.8) * 0.02 + look.pitch * gain, look.yaw * gain);

    for (const m of morphs) {
      const set = (k: string, v: number) => {
        const i = m.dict[k];
        if (i !== undefined) m.infl[i] = v;
      };
      set('Breath', 0.4 + 0.4 * Math.sin(elapsed * 1.1));
    }
  } else {
    // 骨骼：呼吸/环视/抬手（MMD 模型有 632 根骨，抓其中几根就够）
    const dq = new THREE.Quaternion();
    const de = new THREE.Euler();
    for (const r of boneRig) {
      let ax = 0, ay = 0, az = 0;
      // MMD 骨骼空间与 three 的欧拉不同，角度给得很小，宁可看不出来也别把人掰折
      if (r.kind === 'head') { ay = Math.sin(elapsed * 0.45) * 0.10; }
      else if (r.kind === 'neck') { ay = Math.sin(elapsed * 0.45) * 0.04; }
      else if (r.kind === 'spine') { ax = Math.sin(elapsed * 1.25) * 0.01; }
      else if (r.kind === 'armR') { az = mode === 'wave' ? -0.85 + Math.sin(elapsed * 9) * 0.3 : Math.sin(elapsed * 1.2) * 0.02; }
      else if (r.kind === 'armL') { az = Math.sin(elapsed * 1.2 + 1) * 0.02; }
      else if (r.kind === 'elbowR') { az = mode === 'wave' ? -0.5 : 0; }
      de.set(ax, ay, az);
      dq.setFromEuler(de);
      r.b.quaternion.copy(r.rest).multiply(dq);
    }

    // 形态键：呼吸常开，头部缓慢环视，挥手时抬右臂
    for (const m of morphs) {
      const set = (k: string, v: number) => {
        const i = m.dict[k];
        if (i !== undefined) m.infl[i] = v;
      };
      set('Breath', 0.45 + 0.45 * Math.sin(elapsed * 1.15));
      set('Head_Turn', 0.5 + 0.5 * Math.sin(elapsed * 0.42));
      set('Head_Nod', 0.5 + 0.5 * Math.sin(elapsed * 0.9 + 1.4));
      const waveAmt = mode === 'wave' ? 0.9 : 0.08 + 0.08 * Math.sin(elapsed * 1.3);
      set('Arm_R', waveAmt);
      set('Arm_L', 0.06 + 0.06 * Math.sin(elapsed * 1.3 + 1));
    }
  }

  if (elapsed < reflex.until) {
    // L2 后退：直接施加速度，覆盖规划层
    char.position.x += reflex.vx * dt;
    char.position.z += reflex.vz * dt;
  }

  if (mode === 'walk' && target && elapsed >= reflex.until) {
    const dir = target.clone().sub(char.position);
    dir.y = 0;
    const d = dir.length();
    if (d < 0.08) {
      mode = 'idle';
      target = null;
      setAction('待机');
    } else {
      dir.normalize();
      const speed = 1.15 * affect.speedScale();
      const nx = char.position.x + dir.x * speed * dt;
      const nz = char.position.z + dir.z * speed * dt;
      const hit = blockedAt(nx, nz);
      if (hit && elapsed > reflex.cooldown) {
        // L2 硬中断：后退，不看情绪（fear 只影响之后的步速）
        reflex.cooldown = elapsed + 1.2;
        reflex.until = elapsed + 0.45;
        reflex.vx = -dir.x * 1.1;
        reflex.vz = -dir.z * 1.1;
        reflex.count++;
        target = null;
        mode = 'idle';
        affect.onReflex();
        pushLog('sys', 'L2 反射 · 遇障后退（' + hit + '）');
        setAction('L2 反射 · 后退');
        return;
      }
      char.position.x = nx;
      char.position.z = nz;
      char.rotation.y = Math.atan2(dir.x, dir.z);
      if (usingModel) {
        // 没绑骨：整身做"游动"式倾斜 + 轻微侧摆
        char.rotation.z = Math.sin(elapsed * 4) * 0.06;
        char.rotation.x = -0.06;
      } else {
        const swing = Math.sin(elapsed * 9) * 0.5;
        legs[0].rotation.x = swing;
        legs[1].rotation.x = -swing;
        arms[0].rotation.x = -swing * 0.6;
        arms[1].rotation.x = swing * 0.6;
      }
    }
  } else {
    if (!usingModel) {
      legs.forEach((l) => (l.rotation.x *= 0.8));
      arms.forEach((a) => (a.rotation.x *= 0.8));
    } else {
      char.rotation.z *= 0.92;
      char.rotation.x *= 0.92;
    }
  }

  if (mode === 'wave') {
    if (!usingModel) {
      arms[1].rotation.z = -1.9 + Math.sin(elapsed * 14) * 0.35;
    } else {
      char.rotation.y += Math.sin(elapsed * 10) * 0.02;
    }
    if (performance.now() > waveUntil) {
      mode = 'idle';
      if (!usingModel) arms[1].rotation.z = -0.12;
      setAction('待机');
    }
  }
  if (mode === 'sit') {
    char.position.y = -0.18;
    if (!usingModel) {
      legs[0].rotation.x = -1.3;
      legs[1].rotation.x = -1.3;
    }
  }

  // 相机跟随：角色走到哪，构图不变（否则一走动就出画）
  camTarget.lerp(new THREE.Vector3(char.position.x, 1.02, char.position.z), 0.06);
  const want = camTarget.clone().add(camOffset);
  camera.position.lerp(want, 0.06);
  camera.lookAt(camTarget);

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

// 演示/截图用：?auto=walk 会在模型加载后自己走向桌边
const auto = new URLSearchParams(location.search).get('auto');
if (auto) {
  setTimeout(() => {
    if (auto === 'walk') go('桌边');
    else if (auto === 'wave') wave();
  }, 4000);
}
