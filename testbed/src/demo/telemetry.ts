import uPlot, { type AlignedData } from 'uplot';
import 'uplot/dist/uPlot.min.css';

// 遥测曲线：speed（服务端真值）对上行的 |cmd| 模长。两条线，12 s 滚动，没有坐标轴/网格 ——
// 面板里只当"刚才到底动了没有"的眼睛，不是跑分图。
//
// 布局契约（D1b）：曲线卡片是独立定高卡片，高度 120px（要求 110–130），默认完整可见，
// 不靠滚动。所以这里高度写死，宽度只跟着宿主走；宿主尺寸变了用 ResizeObserver 跟一次，
// 不依赖"窗口 resize 一定触发"（面板折叠/展开时不会触发 window.resize）。
//
// 依赖是 testbed 已有的 uplot（package.json），不引新包。

const WINDOW_S = 12;
const SAMPLE_HZ = 20;
const MAX_POINTS = WINDOW_S * SAMPLE_HZ;
/** 图表高度（px）：卡片内可用高度，与 style.css 的 .chart { height: 120px } 对齐 */
export const CHART_H = 120;

export class Telemetry {
  private readonly u: uPlot;
  /** 三行：x / speed / |cmd|。uPlot 要 AlignedData，内部按 number[][] 增删更省事，只在 setData 处转一次 */
  private readonly rows: number[][] = [[], [], []];
  private t = 0;
  private sinceSample = 0;
  private lastW = 0;
  private readonly ro: ResizeObserver | null;

  constructor(private readonly host: HTMLElement) {
    this.u = new uPlot(
      {
        width: this.hostWidth(),
        height: CHART_H,
        padding: [10, 8, 10, 8],
        cursor: { show: false },
        legend: { show: false },
        scales: {
          x: { time: false },
          // 下端留一点余量：0 线压着画布底边会看起来像"没画"
          y: { range: (_u, _min, max) => [-0.05, Math.max(0.8, Number.isFinite(max) ? max * 1.15 : 0.8)] },
        },
        axes: [{ show: false }, { show: false }],
        series: [
          {},
          // points 默认会画出来（uPlot 在点数少时看着像一串圆点），这里明确关掉
          { stroke: '#7c9cff', width: 1.3, fill: 'rgba(124,156,255,0.12)', points: { show: false } },
          { stroke: '#d99a5b', width: 1, dash: [4, 3], points: { show: false } },
        ],
      },
      this.rows as unknown as AlignedData,
      host,
    );
    this.lastW = this.hostWidth();
    this.ro =
      typeof ResizeObserver === 'function'
        ? new ResizeObserver(() => this.resize())
        : null;
    this.ro?.observe(host);
  }

  /** 宿主当前可用宽度；未布局（0）时给一个能画的下限，等 ResizeObserver 再纠正 */
  private hostWidth(): number {
    return Math.max(200, Math.round(this.host.clientWidth || 0));
  }

  /** 每帧调用；内部按 20 Hz 采样，不跟着 rAF 的 120 fps 走 */
  tick(dt: number, speed: number, cmdMag: number): void {
    this.t += dt;
    this.sinceSample += dt;
    if (this.sinceSample < 1 / SAMPLE_HZ) return;
    this.sinceSample = 0;

    const [x, sp, cm] = this.rows;
    x.push(this.t);
    sp.push(Number.isFinite(speed) ? speed : 0);
    cm.push(Number.isFinite(cmdMag) ? cmdMag : 0);
    // 定长滚动：只留最近 MAX_POINTS 点
    while (x.length > MAX_POINTS) {
      x.shift();
      sp.shift();
      cm.shift();
    }
    this.u.setData(this.rows as unknown as AlignedData);
  }

  /** 宽度跟着宿主走；高度恒为 CHART_H。宿主被隐藏（clientWidth=0）时不收缩。 */
  resize(): void {
    const w = this.host.clientWidth;
    if (!w) return;
    if (Math.round(w) === this.lastW) return;
    this.lastW = Math.round(w);
    this.u.setSize({ width: Math.max(200, Math.round(w)), height: CHART_H });
  }
}
