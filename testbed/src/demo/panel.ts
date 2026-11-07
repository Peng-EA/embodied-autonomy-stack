// 面板：只碰 DOM，不碰 three / WebSocket。控件在 demo.html 里是静态标签（便于直接读、也便于
// 验收时用 $$eval 数），这里负责绑定、读数与状态回写。
//
// 一条路径原则：预设/动作按钮改滑杆时，也走**原生 input 事件**（setSlider → dispatch('input')），
// 于是"拖动"和"按钮改值"落到同一个 handler 上，不会出现两套「谁生效」的分支。

export type SliderKey = 'vx' | 'vz' | 'yaw';
export type ToggleKey = 'autonomy' | 'rays' | 'contacts' | 'chart';
export type ActKey = 'stand' | 'walk' | 'stop' | 'mimic' | 'ik' | 'reset';
export type PresetKey = 'straight' | 'diagonal' | 'spin' | 'zero';
export type StatusKey = 'mode' | 'speed' | 'y' | 't' | 'age' | 'fps' | 'drop' | 'ncon';

export interface PanelHandlers {
  /** 滑杆值变了（拖动、或按钮/预设代为改值——都走原生 input 事件） */
  onSlider(key: SliderKey, value: number): void;
  onAct(key: ActKey): void;
  onPreset(key: PresetKey): void;
  onToggle(key: ToggleKey, on: boolean): void;
}

const $ = <T extends HTMLElement = HTMLElement>(sel: string): T => {
  const el = document.querySelector<T>(sel);
  if (!el) throw new Error('demo.html 缺元素：' + sel);
  return el;
};

/** 滑杆读数的小数位：与 step 对齐（0.01 → 2 位） */
const DIGITS: Record<SliderKey, number> = { vx: 2, vz: 2, yaw: 2 };

export class Panel {
  private readonly sliders = new Map<SliderKey, HTMLInputElement>();
  private readonly outs = new Map<SliderKey, HTMLElement>();
  private readonly toggles = new Map<ToggleKey, HTMLInputElement>();
  private readonly logEl = $('#log') as unknown as HTMLOListElement;
  /** 事件折叠区里的小计数（默认收起，不开也能看出有没有新事件） */
  private readonly logCountEl = $('#log-count');
  private readonly chartCard = $('#telemetry-card');
  private readonly scrollEl = $('#panel-scroll');
  private readonly scrollBody = $('#panel-body');
  private readonly scrollRo: ResizeObserver | null;
  private logCount = 0;
  private readonly noteEl = $('#stage-note');
  private readonly gateEl = $('#gate-hint');
  private readonly nconEl = $('#legend-ncon');
  private readonly clearEl = $('#legend-clear');
  readonly chartHost = $('#chart');

  constructor(private readonly handlers: PanelHandlers) {
    for (const key of ['vx', 'vz', 'yaw'] as SliderKey[]) {
      const input = $<HTMLInputElement>('input[data-slider="' + key + '"]');
      this.sliders.set(key, input);
      this.outs.set(key, $<HTMLElement>('[data-out="' + key + '"]'));
      input.addEventListener('input', () => {
        this.renderOut(key);
        this.handlers.onSlider(key, Number(input.value));
      });
    }

    document.querySelectorAll<HTMLButtonElement>('button[data-act]').forEach((btn) => {
      btn.addEventListener('click', () => this.handlers.onAct(btn.dataset.act as ActKey));
    });
    document.querySelectorAll<HTMLButtonElement>('button[data-preset]').forEach((btn) => {
      btn.addEventListener('click', () => this.handlers.onPreset(btn.dataset.preset as PresetKey));
    });

    for (const key of ['autonomy', 'rays', 'contacts', 'chart'] as ToggleKey[]) {
      const input = $<HTMLInputElement>('input[data-toggle="' + key + '"]');
      this.toggles.set(key, input);
      input.addEventListener('change', () => this.handlers.onToggle(key, input.checked));
    }

    // 内容高度 / 视口变化时，只切换"可滚"这个状态位（底部渐隐用），不改任何尺寸
    this.scrollRo =
      typeof ResizeObserver === 'function' ? new ResizeObserver(() => this.syncScrollHint()) : null;
    this.scrollRo?.observe(this.scrollEl);
    for (const child of [...this.scrollEl.children]) this.scrollRo?.observe(child);
    this.syncScrollHint();
  }

  /** 可滚区真的超出时给一条底部渐隐，让"还能往下滚"看得出来（不是内容被裁） */
  syncScrollHint(): void {
    this.scrollBody.classList.toggle('scrollable', this.scrollEl.scrollHeight > this.scrollEl.clientHeight + 1);
  }

  // ---------------------------------------------------------------- 滑杆

  private renderOut(key: SliderKey): void {
    const input = this.sliders.get(key)!;
    const out = this.outs.get(key)!;
    out.textContent = Number(input.value).toFixed(DIGITS[key]);
  }

  /** 读当前三个滑杆（机体前向 / 侧向 / 转向角速度） */
  values(): { vx: number; vz: number; yaw: number } {
    return {
      vx: Number(this.sliders.get('vx')!.value),
      vz: Number(this.sliders.get('vz')!.value),
      yaw: Number(this.sliders.get('yaw')!.value),
    };
  }

  sliderEl(key: SliderKey): HTMLInputElement {
    return this.sliders.get(key)!;
  }

  toggleEl(key: ToggleKey): HTMLInputElement {
    return this.toggles.get(key)!;
  }

  toggleOn(key: ToggleKey): boolean {
    return this.toggles.get(key)!.checked;
  }

  /** 改值并把 input 事件打出去：读数与业务 handler 一起走同一条路 */
  setSlider(key: SliderKey, value: number): void {
    const input = this.sliders.get(key)!;
    const min = Number(input.min);
    const max = Number(input.max);
    input.value = String(Math.min(max, Math.max(min, value)));
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  zeroSliders(): void {
    // 一次性置零：避免三次 input 事件里读到"中间态"的混合指令
    const entries = [...this.sliders.entries()];
    for (const [key, input] of entries) {
      input.value = '0';
      this.renderOut(key);
    }
    this.handlers.onSlider('vx', 0);
  }

  // ---------------------------------------------------------------- 状态

  status(key: StatusKey, text: string): void {
    const el = document.getElementById('st-' + key);
    if (el) {
      el.textContent = text;
      if (key === 'mode') el.dataset.mode = text;
    }
  }

  gate(text: string, warn = false): void {
    this.gateEl.textContent = text;
    this.gateEl.classList.toggle('warn', warn);
  }

  legend(ncon: number | null, clearMin: number | null = null): void {
    this.nconEl.textContent = ncon === null ? 'ncon —' : 'ncon ' + ncon;
    this.clearEl.textContent =
      clearMin === null || !Number.isFinite(clearMin) ? 'clear — m' : 'clear ' + clearMin.toFixed(2) + ' m';
  }

  pill(which: 'ws' | 'endpoint', text: string, state: 'on' | 'off' | 'idle' = 'idle'): void {
    const el = document.getElementById(which === 'ws' ? 'ws-pill' : 'endpoint-pill');
    if (!el) return;
    el.textContent = text;
    el.classList.remove('on', 'off');
    if (state === 'on') el.classList.add('on');
    if (state === 'off') el.classList.add('off');
  }

  /** 舞台左上角的提示条；传 null 收起 */
  note(text: string | null, err = false): void {
    if (text === null) {
      this.noteEl.classList.add('hide');
      return;
    }
    this.noteEl.classList.remove('hide');
    this.noteEl.textContent = text;
    this.noteEl.classList.toggle('err', err);
  }

  /** 曲线的开关只管"遥测卡片"本身：卡片收起来，其余分区不受影响（布局断言按卡片量） */
  setChartVisible(on: boolean): void {
    this.chartCard.classList.toggle('hide', !on);
  }

  // ---------------------------------------------------------------- 事件流

  /** 事件流只留最近 40 条，省得越滚越长 */
  log(text: string, cls: 'hot' | 'warn' | '' = ''): void {
    const li = document.createElement('li');
    if (cls) li.className = cls;
    const b = document.createElement('b');
    const d = new Date();
    b.textContent = d.toTimeString().slice(0, 8);
    li.append(b, document.createTextNode(text));
    this.logEl.prepend(li);
    while (this.logEl.children.length > 40) this.logEl.lastElementChild?.remove();
    this.logCount++;
    this.logCountEl.textContent = String(this.logCount);
  }
}
