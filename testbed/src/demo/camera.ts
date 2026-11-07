import * as THREE from 'three';

// 演示台摄像机：锁定 / 解锁两态。手感参数集中在本文件的 CAMERA_TUNING 里，别散落。
//
// 为什么自己写而不是接 OrbitControls：OrbitControls 默认把**左键**给旋转，而演示台的左键
// 要留给页面原有交互；它也没有"锁定后平滑回位 + 输入全失效"这一态。这里只做四件事：
//   · 视角相对平移（WASD / 空格 / Shift）
//   · 沿视线缩放（滚轮，带近远限位）
//   · 右键拖动旋转（左键完全不碰）
//   · 锁定 → 0.4 s 平滑回到默认构图，此后输入全部无效
// 不引新依赖。
//
// 手感原则：平移跟手、缩放/旋转宁慢勿快。平移 0.80 m/s（D4 由 0.20 m/s 提 4×）、
// 滚轮 10%/格、旋转 0.15°/px（后两者 D4 未动）；
// 三个通道都是"目标值 + 指数趋近"，松键/松手后速度衰减到 0，不会一甩就飞。

const DEG = Math.PI / 180;
const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);
/** 角度差折到 [-π, π]：回位插值不许绕远路 */
const shortAngle = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
const easeInOut = (t: number) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

/** 手感参数。改这里等于改手感（验收数字对照的就是这几个） */
export interface CameraTuning {
  /** 平移速度 m/s（验收：按 W 1.2 s 位移 0.60–1.40 m，实测口径见 demo-check --camera） */
  panSpeed: number;
  /** 滚轮每格缩放比例（0.10 = 10%，验收 8–12%） */
  zoomStep: number;
  /** 右键拖动灵敏度（°/px，验收 ~0.15） */
  rotateDegPerPx: number;
  /** 锁定后回默认位的过渡时长（s，验收 0.4） */
  transitionSec: number;
  /** 沿视线缩放的近/远限位（m）：近端 > 机器人半径，别穿进身体 */
  minDist: number;
  maxDist: number;
}

export const CAMERA_TUNING: CameraTuning = {
  panSpeed: 0.8,
  zoomStep: 0.1,
  rotateDegPerPx: 0.15,
  transitionSec: 0.4,
  minDist: 0.8,
  maxDist: 12,
};

/** 指数趋近的时间常数（s）：越小越跟手，越大越"黏"
 *  PAN_TAU 与速度同比：速度 ×4 时 τ 不变 ⇒ 起速时间仍是 ~0.12 s（不会一按就窜），
 *  松键后的滑行时间也还是 ~0.12 s，只有滑行距离随速度按比例放大（≈0.10 m @0.8 m/s）。
 */
const PAN_TAU = 0.12;
const ROT_TAU = 0.06;
const ZOOM_TAU = 0.1;
/** 俯仰限位：别翻到地面以下，也别顶到正上方（lookAt 会退化） */
const PITCH_MIN = -1.15; // ≈ -66°
const PITCH_MAX = 1.35; // ≈ 77°

interface Pose {
  target: THREE.Vector3;
  yaw: number;
  pitch: number;
  dist: number;
}

export interface CameraRigOptions {
  camera: THREE.PerspectiveCamera;
  canvas: HTMLCanvasElement;
  /** 跟随点（stage 每帧平滑后的机器人位置） */
  focus: THREE.Vector3;
  /** 默认机位相对跟随点的偏移（锁定态就是这个构图） */
  defaultOffset: THREE.Vector3;
  tuning?: Partial<CameraTuning>;
  /** 两态切换回调（DOM 由 rig 自己同步，这里只是给外面留的口子） */
  onLockChange?: (locked: boolean) => void;
}

/** 自检探针用的只读快照 */
export interface CameraState {
  locked: boolean;
  transitioning: boolean;
  position: [number, number, number];
  target: [number, number, number];
  forward: [number, number, number];
  /** 相机到目标点的实际距离（滚轮缩放的验收口径） */
  dist: number;
  /** 锁定态"默认机位"的位置（验回位过渡用） */
  defaultPosition: [number, number, number];
  hintVisible: boolean;
  hintText: string;
}

/**
 * 摄像机两态控制器。stage 每帧调一次 update(dt)，它负责写出 camera.position / quaternion。
 * 锁定态（默认）＝跟随机位：target 恒等于 focus，构图恒等于 defaultOffset；
 * 解锁态＝自由机位：target 由平移驱动，yaw/pitch/dist 由右键/滚轮驱动。
 */
export class DemoCameraRig {
  private readonly camera: THREE.PerspectiveCamera;
  private readonly canvas: HTMLCanvasElement;
  private readonly focus: THREE.Vector3;
  private readonly defaultOffset: THREE.Vector3;
  private readonly tune: CameraTuning;
  private readonly onLockChange?: (locked: boolean) => void;

  private locked = true;

  /** 目标值（输入直接写这里）与平滑后的当前值 */
  private readonly target = new THREE.Vector3();
  private yaw = 0;
  private pitch = 0;
  private dist = 1;
  private curYaw = 0;
  private curPitch = 0;
  private curDist = 1;

  private readonly panVel = new THREE.Vector3();
  private readonly keys = new Set<string>();
  private dragging = false;
  private pointerId = -1;
  private lastX = 0;
  private lastY = 0;
  private trans: { t: number; from: Pose } | null = null;

  /** 默认机位的球坐标：由 defaultOffset 现算，不抄一份常数 */
  private readonly defYaw: number;
  private readonly defPitch: number;
  private readonly defDist: number;

  private readonly scratch = new THREE.Vector3();
  private readonly btn: HTMLButtonElement | null;
  private readonly hint: HTMLElement | null;

  constructor(opts: CameraRigOptions) {
    this.camera = opts.camera;
    this.canvas = opts.canvas;
    this.focus = opts.focus;
    this.defaultOffset = opts.defaultOffset;
    this.tune = { ...CAMERA_TUNING, ...(opts.tuning ?? {}) };
    this.onLockChange = opts.onLockChange;

    const off = this.defaultOffset;
    const len = Math.max(1e-6, off.length());
    this.defDist = clamp(len, this.tune.minDist, this.tune.maxDist);
    this.defPitch = clamp(Math.asin(clamp(off.y / len, -1, 1)), PITCH_MIN, PITCH_MAX);
    this.defYaw = Math.atan2(off.x, off.z);

    this.target.copy(this.focus);
    this.yaw = this.curYaw = this.defYaw;
    this.pitch = this.curPitch = this.defPitch;
    this.dist = this.curDist = this.defDist;
    this.applyPose(this.target, this.curYaw, this.curPitch, this.curDist);

    this.btn = document.querySelector<HTMLButtonElement>('#cam-lock');
    this.hint = document.querySelector<HTMLElement>('#cam-hint');
    this.wire();
    this.syncDom();
  }

  get isLocked(): boolean {
    return this.locked;
  }

  toggle(): void {
    this.setLocked(!this.locked);
  }

  setLocked(next: boolean): void {
    if (next === this.locked) return;
    this.locked = next;
    this.keys.clear();
    this.panVel.set(0, 0, 0);
    this.endDrag();

    if (next) {
      // 自由 → 锁定：记下此刻位姿，0.4 s 内平滑回到默认构图（不许瞬移）
      this.trans = {
        t: 0,
        from: { target: this.target.clone(), yaw: this.curYaw, pitch: this.curPitch, dist: this.curDist },
      };
    } else {
      // 锁定 → 自由：状态对齐"当前看到的这一帧"，从这儿继续，别跳
      this.trans = null;
      this.target.copy(this.focus);
      const rel = this.camera.position.clone().sub(this.target);
      const d = Math.max(1e-4, rel.length());
      this.dist = this.curDist = clamp(d, this.tune.minDist, this.tune.maxDist);
      this.pitch = this.curPitch = clamp(Math.asin(clamp(rel.y / d, -1, 1)), PITCH_MIN, PITCH_MAX);
      this.yaw = this.curYaw = Math.atan2(rel.x, rel.z);
    }
    this.syncDom();
    this.onLockChange?.(this.locked);
  }

  /** 立刻回到默认机位（截图/关键帧用；锁定态本身就是这个位姿） */
  snapDefault(): void {
    this.target.copy(this.focus);
    this.yaw = this.curYaw = this.defYaw;
    this.pitch = this.curPitch = this.defPitch;
    this.dist = this.curDist = this.defDist;
    this.applyPose(this.target, this.curYaw, this.curPitch, this.curDist);
  }

  /** 每帧推进一步。dt 是**墙钟**（main.ts 的 rawDt，已截到 250 ms），不是夹到 50 ms 的物理 dt */
  update(dt: number): void {
    const d = clamp(Number.isFinite(dt) ? dt : 0, 0, 0.25);

    if (this.locked) {
      this.panVel.set(0, 0, 0);
      if (this.trans) {
        this.trans.t = Math.min(1, this.trans.t + d / Math.max(1e-3, this.tune.transitionSec));
        const k = easeInOut(this.trans.t);
        const from = this.trans.from;
        this.applyPose(
          this.scratch.copy(from.target).lerp(this.focus, k),
          from.yaw + shortAngle(this.defYaw - from.yaw) * k,
          from.pitch + (this.defPitch - from.pitch) * k,
          from.dist + (this.defDist - from.dist) * k,
        );
        if (this.trans.t >= 1) {
          this.trans = null;
          this.snapDefault();
        }
      } else {
        this.snapDefault();
      }
      return;
    }

    // ---- 平移：按当前视角方向（水平前向 = 相机视线在地面的投影），不是世界轴
    const want = this.scratch.set(0, 0, 0);
    const f = (this.keys.has('W') ? 1 : 0) - (this.keys.has('S') ? 1 : 0);
    const s = (this.keys.has('D') ? 1 : 0) - (this.keys.has('A') ? 1 : 0);
    const u = (this.keys.has('SPACE') ? 1 : 0) - (this.keys.has('SHIFT') ? 1 : 0);
    if (f || s || u) {
      const sy = Math.sin(this.curYaw);
      const cy = Math.cos(this.curYaw);
      // 相机在 target 的 +offset 侧，视线方向 = −offset ⇒ 水平前向 = (−sinYaw, 0, −cosYaw)，右向 = (cosYaw, 0, −sinYaw)
      want.set(-sy * f + cy * s, u, -cy * f - sy * s);
      if (want.lengthSq() > 1) want.normalize(); // 斜按不加速
      want.multiplyScalar(this.tune.panSpeed);
    }
    this.panVel.lerp(want, 1 - Math.exp(-d / PAN_TAU));
    if (this.panVel.lengthSq() < 1e-8) this.panVel.set(0, 0, 0);
    this.target.addScaledVector(this.panVel, d);

    // ---- 旋转 / 缩放：目标值 → 当前值 指数趋近（松手后只趋近，不再有惯性，甩不飞）
    const ar = 1 - Math.exp(-d / ROT_TAU);
    this.curYaw += shortAngle(this.yaw - this.curYaw) * ar;
    this.curPitch += (this.pitch - this.curPitch) * ar;
    const az = 1 - Math.exp(-d / ZOOM_TAU);
    this.curDist += (this.dist - this.curDist) * az;

    this.applyPose(this.target, this.curYaw, this.curPitch, this.curDist);
  }

  /** 只读快照：demo-check --camera 的判据全取这里，不读内部私有字段 */
  state(): CameraState {
    const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(this.camera.quaternion);
    const def = this.defaultPositionVec(new THREE.Vector3());
    return {
      locked: this.locked,
      transitioning: !!this.trans,
      position: [this.camera.position.x, this.camera.position.y, this.camera.position.z],
      target: [this.target.x, this.target.y, this.target.z],
      forward: [fwd.x, fwd.y, fwd.z],
      dist: this.camera.position.distanceTo(this.target),
      defaultPosition: [def.x, def.y, def.z],
      hintVisible: this.hint ? getComputedStyle(this.hint).display !== 'none' : false,
      hintText: this.hint ? (this.hint.textContent || '').trim() : '',
    };
  }

  // ---------------------------------------------------------------- 内部
  private defaultPositionVec(out: THREE.Vector3): THREE.Vector3 {
    const cp = Math.cos(this.defPitch);
    return out.set(
      this.focus.x + Math.sin(this.defYaw) * cp * this.defDist,
      this.focus.y + Math.sin(this.defPitch) * this.defDist,
      this.focus.z + Math.cos(this.defYaw) * cp * this.defDist,
    );
  }

  private applyPose(target: THREE.Vector3, yaw: number, pitch: number, dist: number): void {
    const cp = Math.cos(pitch);
    this.camera.position.set(
      target.x + Math.sin(yaw) * cp * dist,
      target.y + Math.sin(pitch) * dist,
      target.z + Math.cos(yaw) * cp * dist,
    );
    this.camera.lookAt(target);
  }

  /** 按钮/提示的显隐与提示语（样式与面板同色系，见 style.css） */
  private syncDom(): void {
    const locked = this.locked;
    const btn = this.btn;
    if (btn) {
      btn.dataset.state = locked ? 'locked' : 'free';
      btn.setAttribute('aria-pressed', String(!locked));
      const tip = locked
        ? '摄像机已锁定 · 点击解锁：WASD 平移 / 空格·Shift 升降 / 滚轮缩放 / 右键拖动旋转'
        : '摄像机自由 · 点击锁定：0.4 s 平滑回到默认视角';
      btn.dataset.tip = tip;
      btn.title = tip;
      btn.setAttribute('aria-label', tip);
    }
    this.hint?.classList.toggle('on', !locked);
  }

  private endDrag(): void {
    if (this.dragging && this.pointerId >= 0) {
      try {
        this.canvas.releasePointerCapture(this.pointerId);
      } catch {
        /* 指针已经没了：忽略 */
      }
    }
    this.dragging = false;
    this.pointerId = -1;
  }

  private wiringDone = false;

  private wire(): void {
    if (this.wiringDone) return;
    this.wiringDone = true;

    this.btn?.addEventListener('click', (e) => {
      e.preventDefault();
      // 点完就失焦：否则接下来按空格会再次"点击"这个按钮，把切换当成摄像机输入
      (e.currentTarget as HTMLButtonElement).blur();
      this.toggle();
    });

    this.canvas.addEventListener('wheel', this.onWheel, { passive: false });
    this.canvas.addEventListener('pointerdown', this.onPointerDown);
    this.canvas.addEventListener('pointermove', this.onPointerMove);
    this.canvas.addEventListener('pointerup', this.onPointerUp);
    this.canvas.addEventListener('pointercancel', this.onPointerUp);
    this.canvas.addEventListener('contextmenu', this.onContextMenu);
    window.addEventListener('keydown', this.onKeyDown);
    window.addEventListener('keyup', this.onKeyUp);
    window.addEventListener('blur', this.onBlur);
  }

  // 箭头函数属性：绑好 this，解绑也不费事
  private readonly onWheel = (e: WheelEvent): void => {
    if (this.locked) return; // 锁定态滚轮无效（也不 preventDefault）
    e.preventDefault();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1; // 行/页 → 像素
    const notches = clamp((e.deltaY * unit) / -100, -3, 3); // 一格 = 100px
    this.dist = clamp(this.dist * Math.pow(1 - this.tune.zoomStep, notches), this.tune.minDist, this.tune.maxDist);
  };

  private readonly onPointerDown = (e: PointerEvent): void => {
    if (e.button !== 2 || this.locked || this.dragging) return; // 左键/中键不抢
    e.preventDefault();
    this.dragging = true;
    this.pointerId = e.pointerId;
    this.lastX = e.clientX;
    this.lastY = e.clientY;
    try {
      this.canvas.setPointerCapture(e.pointerId);
    } catch {
      /* 捕获失败也能靠 window 上的 move 兜底 */
    }
  };

  private readonly onPointerMove = (e: PointerEvent): void => {
    if (!this.dragging || e.pointerId !== this.pointerId) return;
    const dx = e.clientX - this.lastX;
    const dy = e.clientY - this.lastY;
    this.lastX = e.clientX;
    this.lastY = e.clientY;
    const k = this.tune.rotateDegPerPx * DEG;
    this.yaw -= dx * k;
    this.pitch = clamp(this.pitch - dy * k, PITCH_MIN, PITCH_MAX);
  };

  private readonly onPointerUp = (e: PointerEvent): void => {
    if (!this.dragging || e.pointerId !== this.pointerId) return;
    this.endDrag();
  };

  private readonly onContextMenu = (e: Event): void => {
    // 右键是旋转键：3D 视口里不弹系统菜单（与锁定态无关，两个态行为一致）
    e.preventDefault();
  };

  private readonly onKeyDown = (e: KeyboardEvent): void => {
    if (this.locked) return; // 锁定：一个输入都不接
    if (isTypingTarget(e.target)) return;
    const k = keyOf(e);
    if (!k) return;
    e.preventDefault(); // 空格别滚页面
    this.keys.add(k);
  };

  private readonly onKeyUp = (e: KeyboardEvent): void => {
    const k = keyOf(e);
    if (k) this.keys.delete(k);
  };

  private readonly onBlur = (): void => {
    // 切窗口时 keyup 收不到：不清会一直按着飞出去
    this.keys.clear();
    this.panVel.set(0, 0, 0);
    this.endDrag();
  };
}

/** 焦点在输入控件里时别抢键盘（面板的滑杆/开关） */
function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select' || tag === 'button') return true;
  return el.isContentEditable === true;
}

/** 物理键位 → 内部键名（用 code 认键，布局无关；老环境退回 key） */
function keyOf(e: KeyboardEvent): string | null {
  switch (e.code) {
    case 'KeyW':
      return 'W';
    case 'KeyA':
      return 'A';
    case 'KeyS':
      return 'S';
    case 'KeyD':
      return 'D';
    case 'Space':
      return 'SPACE';
    case 'ShiftLeft':
    case 'ShiftRight':
      return 'SHIFT';
    default:
      break;
  }
  const k = (e.key || '').toLowerCase();
  if (k === 'w') return 'W';
  if (k === 'a') return 'A';
  if (k === 's') return 'S';
  if (k === 'd') return 'D';
  if (k === ' ' || k === 'spacebar') return 'SPACE';
  if (k === 'shift') return 'SHIFT';
  return null;
}
