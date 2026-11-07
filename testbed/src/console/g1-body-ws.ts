import type { Body, BodyCommand, BodyState } from './body.ts';
import { threeToMjMat, threeToMjPos } from './g1-mesh.ts';
import type { MeshMeta, MjPose } from './g1-mesh.ts';

// G1 的 WS 版身体：状态来自 Python 权威环（sim_server/ · ws://127.0.0.1:8788/sim）。
// 契约（body.ts 的 BodyCommand / BodyState / Body）一个字不改——本文件只换"状态从哪来"。
//
// 边界清单（P0b 实测出来的，不要当"仿真人形"读）：
// - 服务端没有步态（P2b 才解决）：cmd 里的速度不产生位移，blocked 恒为 true 是"想走走不动"。
// - 服务端没有 /reset：reset() 只能把指令清零，骨盆位置由服务端说了算（界面「复位」对这一档是空操作）。
// - 帧里带 geom.xpos / geom.xmat（72 个 geom，three 系）但**不带网格**：G1 要画出来仍需 wasm 那份
//   meshes / parts，本文件只负责 BodyState。想看网格照旧 ?sim=wasm；几何搬到服务端是 P0c。
// - 断线不伪造数据：degraded=true，state() 返回**最后一帧真实状态**（t 停在原处）；
//   一帧都没到手时 state() 直接抛错——create() 只在真帧到手后才 resolve，所以正常路径不会撞上。

export const DEFAULT_WS_URL = 'ws://127.0.0.1:8788/sim';
/** 首次连接超时：连不上就当降级，别让界面挂在那里等 */
export const CONNECT_TIMEOUT_MS = 1500;
/** 重连退避：1 s 起步，翻倍，封顶 5 s（页面先起、服务端后起是常态） */
export const BACKOFF_MIN_MS = 1000;
export const BACKOFF_MAX_MS = 5000;
/** 服务端每个 tick 推进的仿真秒数：8 步 × 2 ms（sim_server/world.py 的 STEPS_PER_TICK 同值） */
const TICK_DT_SIM = 0.016;
/** 命令帧上限 60 Hz：动画帧跑到 120 fps 时别把 WS 刷爆 */
const SEND_PERIOD_MS = 1000 / 60;
/** 超过这么久没新帧就算 stale（60 Hz 下等于丢 30 帧） */
const STALE_MS = 500;

/** 服务端 state 帧（键见 sim_server/app.py 的 snapshot） */
export interface G1WsFrame {
  tSim: number;
  x: number;
  y: number;
  z: number;
  yaw: number;
  speed: number;
  blocked: boolean;
  joints: Record<string, number>;
  geom?: {
    xpos?: number[];
    xmat?: number[];
    names?: string[];
    /** P0c：每个 geom 的 mesh 资产名（非网格 geom 为 ""），只在首帧给 */
    mesh?: string[];
    /** P0c：mesh 资产名 -> MuJoCo 网格系（pos/quat/scale），只在首帧给 */
    meshMeta?: Record<string, MeshMeta>;
  };
  contacts?: number;
  /** P2b 追加的服务端键：当前模式机（HOLD / POLICY）。不是 body.ts 的契约字段，
   *  客户端只做观测（probe-idle 用它数模式翻转），不参与 BodyState。 */
  mode?: string;
}

export interface G1WsStats {
  /** 收到的 state 帧数 */
  frames: number;
  /** 按 tSim 步进推算出的丢失帧数（服务端 1 格队列丢旧帧 + 断线期间未收） */
  dropped: number;
  /** 重连尝试次数 */
  reconnects: number;
  /** RTT 采样点数（浏览器原生 WebSocket 不能发 ping → 0） */
  rttSamples: number;
  /** RTT p95，毫秒；无采样 = null（不是 0） */
  rttP95Ms: number | null;
}

export interface G1WsOptions {
  /** 运行期断线超过 fallbackAfterMs 仍未恢复时回调一次（界面据此打 sys 事件） */
  onFallback?: (reason: string) => void;
  connectTimeoutMs?: number;
  fallbackAfterMs?: number;
  /** 注入 WebSocket 实现：浏览器默认 globalThis.WebSocket；node 探针传 ws（原生实现发不了 ping） */
  WebSocketCtor?: unknown;
  /** 自动 RTT 采样间隔（ms，0 = 关；默认 250） */
  rttEveryMs?: number;
}

/** 最近秩分位数（n 小的时候比插值好核对） */
export function percentile(xs: number[], p: number): number {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(p * s.length))];
}

export class G1BodyWs implements Body {
  readonly kind = 'mujoco-humanoid' as const;
  readonly label = 'G1 · sim_server (WS)';
  jointNames: string[] = [];

  /** 没有可用数据（未连上 / 断线 / 帧停了）时为 true。它不替数据说话——它是"别信这帧"的旗标。 */
  degraded = true;
  onFallback: ((reason: string) => void) | undefined;

  private readonly url: string;
  private readonly ctor: unknown;
  private readonly connectTimeoutMs: number;
  private readonly fallbackAfterMs: number;
  private readonly rttEveryMs: number;

  private ws: any = null;
  private opened = false;
  private closed = false;
  private gotFirstFrame = false;
  private skipGapOnce = false;

  private lastFrame: G1WsFrame | null = null;
  /** 首帧的 geom->mesh 映射（P0c；没收到 = 空，调用方据此退回占位角色） */
  private geomMeshNames: string[] = [];
  private meshMetaMap: Record<string, MeshMeta> | null = null;
  /** pose() 的缓存：同一帧只转一次（three -> mj 是 80 个 geom 的矩阵乘） */
  private poseCache: MjPose | null = null;
  private poseCacheFrame = -1;
  private lastFrameAt = 0;
  private lastSentAt = -1e9;
  private degradedSince = 0;
  private fellBack = false;
  private backoff = BACKOFF_MIN_MS;

  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private rttTimer: ReturnType<typeof setInterval> | null = null;
  private readonly rtt: number[] = [];

  private readonly counter = { frames: 0, dropped: 0, reconnects: 0, bad: 0 };
  private firstWaiter: { resolve: () => void; reject: (e: Error) => void } | null = null;

  private constructor(url: string, opts: G1WsOptions) {
    this.url = url;
    this.ctor = opts.WebSocketCtor ?? (globalThis as any).WebSocket;
    this.connectTimeoutMs = opts.connectTimeoutMs ?? CONNECT_TIMEOUT_MS;
    this.fallbackAfterMs = opts.fallbackAfterMs ?? 10000;
    this.rttEveryMs = opts.rttEveryMs ?? 250;
    this.onFallback = opts.onFallback;
    this.degradedSince = performance.now();
  }

  /** 只在一帧真实数据到手后才 resolve；连不上就 reject（调用方据此降级，而不是拿到一个空壳） */
  static async create(url: string = DEFAULT_WS_URL, opts: G1WsOptions = {}): Promise<G1BodyWs> {
    const body = new G1BodyWs(url, opts);
    await body.awaitFirstFrame();
    return body;
  }

  // ---------------------------------------------------------------- Body 契约

  /** 服务端没有 /reset：这里只能把指令清零。位置复位要重启 sim_server。 */
  reset(_x: number, _z: number): void {
    this.lastSentAt = -1e9;
    this.send({ vx: 0, vz: 0, yaw: 0 });
  }

  /** 每帧调用：指令按 60 Hz 上限发给服务端，返回**最近一帧真实状态**（不等下一帧，不插值） */
  step(_dt: number, cmd: BodyCommand): BodyState {
    this.refreshDegraded();
    const now = performance.now();
    if (now - this.lastSentAt >= SEND_PERIOD_MS) {
      this.lastSentAt = now;
      this.send(cmd);
    }
    return this.state();
  }

  state(): BodyState {
    const f = this.lastFrame;
    if (!f) throw new Error('g1-body-ws: 还没有收到任何真实帧（degraded：绝不伪造 BodyState）');
    return {
      t: f.tSim,
      x: f.x,
      y: f.y,
      z: f.z,
      yaw: f.yaw,
      speed: f.speed,
      blocked: f.blocked,
      joints: f.joints,
    };
  }

  // ---------------------------------------------------------------- 诊断

  stats(): G1WsStats {
    return {
      frames: this.counter.frames,
      dropped: this.counter.dropped,
      reconnects: this.counter.reconnects,
      rttSamples: this.rtt.length,
      rttP95Ms: this.rtt.length ? percentile(this.rtt, 0.95) : null,
    };
  }

  /** 最后一帧的服务端时间戳（没帧 = null） */
  lastTsim(): number | null {
    return this.lastFrame ? this.lastFrame.tSim : null;
  }

  /** 原始状态帧（探针/调试用；没有帧 = null） */
  frame(): G1WsFrame | null {
    return this.lastFrame;
  }

  /** 首帧给的 geom -> mesh 资产名（非网格 geom 为 ""）；没收到 = [] */
  geomMesh(): readonly string[] {
    return this.geomMeshNames;
  }

  /** 首帧给的网格系（pos/quat/scale）；没收到 = null（此时画不出网格） */
  meshMeta(): Record<string, MeshMeta> | null {
    return this.meshMetaMap;
  }

  /**
   * 最近一帧的位姿，**翻回 MuJoCo 系**给 G1View.sync 用（z 上）。
   *
   * 服务端发的是 three 系（y 上），而 G1View 的 container 已经绕 x 转了 -90°、顶点也是
   * MuJoCo 网格系烘焙的，所以这里必须翻回去一次；否则整机躺着。没有帧 = null。
   */
  pose(): MjPose | null {
    const f = this.lastFrame;
    const geom = f?.geom;
    if (!geom?.xpos?.length || !geom.xmat?.length) return null;
    const n = Math.floor(geom.xpos.length / 3);
    if (this.poseCache && this.poseCacheFrame === this.counter.frames && this.poseCache.ngeom === n) {
      return this.poseCache;
    }
    const xpos = new Float64Array(n * 3);
    const xmat = new Float64Array(n * 9);
    for (let g = 0; g < n; g++) {
      const p = g * 3;
      const [mx, my, mz] = threeToMjPos(geom.xpos[p], geom.xpos[p + 1], geom.xpos[p + 2]);
      xpos[p] = mx;
      xpos[p + 1] = my;
      xpos[p + 2] = mz;
      const r = g * 9;
      const m = threeToMjMat(geom.xmat.slice(r, r + 9));
      xmat.set(m, r);
    }
    this.poseCache = { geomXpos: xpos, geomXmat: xmat, ngeom: n };
    this.poseCacheFrame = this.counter.frames;
    return this.poseCache;
  }

  close(): void {
    this.closed = true;
    this.clearTimers();
    const ws = this.ws;
    this.ws = null;
    this.opened = false;
    this.degraded = true;
    try {
      ws?.close();
    } catch {
      /* 已经断了 */
    }
    if (this.firstWaiter) {
      const w = this.firstWaiter;
      this.firstWaiter = null;
      w.reject(new Error('g1-body-ws: 已关闭，未收到任何帧'));
    }
  }

  // ---------------------------------------------------------------- 连接

  private awaitFirstFrame(): Promise<void> {
    if (this.lastFrame) return Promise.resolve();
    return new Promise<void>((resolve, reject) => {
      this.firstWaiter = { resolve, reject };
      this.open();
    });
  }

  private open(): void {
    if (this.closed) return;
    const WS: any = this.ctor;
    if (typeof WS !== 'function') {
      const err = new Error('运行环境没有 WebSocket 实现（node 需 ≥22，或给 WebSocketCtor）');
      this.failFirst(err);
      return;
    }

    let ws: any;
    try {
      ws = new WS(this.url);
    } catch (e) {
      this.onSocketFailure(e as Error);
      return;
    }
    this.ws = ws;
    this.skipGapOnce = true;

    this.connectTimer = setTimeout(() => {
      this.connectTimer = null;
      if (this.opened && this.ws === ws) return;
      try {
        ws.close();
      } catch {
        /* 无所谓 */
      }
      this.onSocketFailure(new Error('首次连接超时 ' + this.connectTimeoutMs + ' ms（' + this.url + '）'));
    }, this.connectTimeoutMs);

    const guard = (fn: () => void) => () => {
      if (this.ws !== ws) return;
      fn();
    };

    ws.addEventListener('open', guard(() => this.handleOpen()));
    ws.addEventListener('message', (ev: any) => {
      if (this.ws !== ws) return;
      this.handleMessage(ev?.data);
    });
    ws.addEventListener('close', guard(() => this.onSocketFailure(new Error('连接被关闭'))));
    ws.addEventListener('error', guard(() => this.onSocketFailure(new Error('连接出错（服务端没起？）'))));
  }

  private handleOpen(): void {
    this.opened = true;
    this.backoff = BACKOFF_MIN_MS;
    this.degradedSince = 0;
    this.fellBack = false;
    if (this.connectTimer) {
      clearTimeout(this.connectTimer);
      this.connectTimer = null;
    }
    if (this.rttEveryMs > 0 && !this.rttTimer) {
      this.rttTimer = setInterval(() => this.sampleRtt(), this.rttEveryMs);
    }
  }

  private onSocketFailure(err: Error): void {
    this.ws = null;
    this.opened = false;
    this.degraded = true;
    // 断线时钟只记一次：每次失败重连都重置的话，onFallback 永远攒不满 fallbackAfterMs
    if (!this.degradedSince) this.degradedSince = performance.now();
    if (this.rttTimer) {
      clearInterval(this.rttTimer);
      this.rttTimer = null;
    }
    if (this.closed) return;
    if (!this.gotFirstFrame) {
      this.failFirst(err); // create 阶段：直接失败，让调用方降级
      return;
    }
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer) return;
    const delay = this.backoff;
    this.backoff = Math.min(BACKOFF_MAX_MS, this.backoff * 2);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.counter.reconnects++;
      this.open();
    }, delay);
  }

  private failFirst(err: Error): void {
    this.clearTimers();
    const w = this.firstWaiter;
    this.firstWaiter = null;
    if (w) w.reject(err);
    this.closed = true;
    const ws = this.ws;
    this.ws = null;
    try {
      ws?.close();
    } catch {
      /* 已经断了 */
    }
  }

  private clearTimers(): void {
    if (this.connectTimer) clearTimeout(this.connectTimer);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.rttTimer) clearInterval(this.rttTimer);
    this.connectTimer = null;
    this.reconnectTimer = null;
    this.rttTimer = null;
  }

  // ---------------------------------------------------------------- 收发

  private handleMessage(data: unknown): void {
    let f: G1WsFrame;
    try {
      f = JSON.parse(typeof data === 'string' ? data : String(data));
    } catch {
      this.counter.bad++;
      return;
    }
    if (!f || typeof f.tSim !== 'number' || typeof f.joints !== 'object' || f.joints === null) {
      this.counter.bad++;
      return;
    }

    const prev = this.lastFrame;
    if (prev && !this.skipGapOnce) {
      const gap = Math.round((f.tSim - prev.tSim) / TICK_DT_SIM) - 1;
      if (gap > 0) this.counter.dropped += gap;
    }
    this.skipGapOnce = false;

    if (!this.jointNames.length) this.jointNames = Object.keys(f.joints);
    // P0c：网格资产只在首帧给一次（35 个 STL 的网格系 + 每个 geom 对应哪块网格）
    if (f.geom?.mesh?.length && !this.geomMeshNames.length) this.geomMeshNames = f.geom.mesh;
    if (f.geom?.meshMeta && !this.meshMetaMap) this.meshMetaMap = f.geom.meshMeta;
    this.lastFrame = f;
    this.lastFrameAt = performance.now();
    this.counter.frames++;
    this.gotFirstFrame = true;
    this.degraded = false;
    this.degradedSince = 0;

    if (this.firstWaiter) {
      const w = this.firstWaiter;
      this.firstWaiter = null;
      w.resolve();
    }
  }

  private send(cmd: BodyCommand): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== 1) return;
    try {
      ws.send(
        JSON.stringify({
          t: Date.now(),
          cmd: { vx: cmd.vx, vz: cmd.vz, yaw: cmd.yaw },
          jointTargets: null,
        }),
      );
    } catch {
      /* 正在断：close 事件会接手重连，这里不重复处理 */
    }
  }

  /** 协议层 ping/pong RTT：浏览器原生 WebSocket 不暴露 ping → 静默跳过（stats.rttSamples=0） */
  private sampleRtt(): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== 1) return;
    if (typeof ws.ping !== 'function' || typeof ws.once !== 'function') return;
    const t0 = performance.now();
    let done = false;
    const onPong = () => {
      if (done) return;
      done = true;
      this.rtt.push(performance.now() - t0);
      if (this.rtt.length > 4000) this.rtt.shift();
    };
    try {
      ws.once('pong', onPong);
      ws.ping();
    } catch {
      done = true;
      return;
    }
    setTimeout(() => {
      if (done) return;
      done = true;
      try {
        ws.off?.('pong', onPong);
      } catch {
        /* 连接没了 */
      }
    }, 1000);
  }

  // ---------------------------------------------------------------- 降级判定

  private refreshDegraded(): void {
    const open = !!this.ws && this.ws.readyState === 1;
    const fresh = performance.now() - this.lastFrameAt < STALE_MS;
    const was = this.degraded;
    this.degraded = !(open && fresh);
    if (this.degraded && !was && this.degradedSince === 0) this.degradedSince = performance.now();
    if (
      this.degraded &&
      !this.fellBack &&
      this.degradedSince > 0 &&
      performance.now() - this.degradedSince > this.fallbackAfterMs
    ) {
      this.fellBack = true;
      const s = Math.round(this.fallbackAfterMs / 1000);
      this.onFallback?.('WS 断线 ' + s + ' s 未恢复（' + this.url + '）');
    }
  }
}
