// 离散动作通道（mimic / IK）。
//
// 为什么要单独一条 WS：body.ts 的契约只到 BodyCommand（速度向量），G1BodyWs 也只发这个。
// 服务端另外两个入口是**离散请求**——{"mimic":"play"}、{"ik":"play"}（见 sim_server/app.py 的
// handle()）——它们不属于 BodyCommand。为了不往契约文件里塞东西、也不动探针共用的
// g1-body-ws.ts，这里开一条独立连接只发这两类消息；速度通道仍然走 CmdGate → G1BodyWs。
//
// 服务端是单实例权威环，多一条连接不改变任何物理状态；/health 的 clients 会 +1。

export type ActuatorState = 'idle' | 'open' | 'closed';

export class ActuatorChannel {
  private ws: WebSocket | null = null;
  private state: ActuatorState = 'idle';
  private retry: ReturnType<typeof setTimeout> | null = null;
  private closedByUs = false;
  /** 连接还没 OPEN 时先存着，open 后按序补发（第一次点击正好撞上建连是常态） */
  private readonly queue: Record<string, unknown>[] = [];

  constructor(
    private readonly url: string,
    private readonly onChange: (state: ActuatorState, detail?: string) => void,
  ) {}

  get status(): ActuatorState {
    return this.state;
  }

  private setState(next: ActuatorState, detail?: string): void {
    if (this.state === next) return;
    this.state = next;
    this.onChange(next, detail);
  }

  private ensure(): boolean {
    if (this.closedByUs) return false;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return true;
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) return false;
    this.open();
    return false;
  }

  private open(): void {
    if (this.retry) {
      clearTimeout(this.retry);
      this.retry = null;
    }
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch (e) {
      this.setState('closed', (e as Error).message);
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.addEventListener('open', () => {
      if (this.ws !== ws) return;
      this.setState('open');
      this.flush();
    });
    ws.addEventListener('close', () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.setState('closed');
      this.scheduleRetry();
    });
    ws.addEventListener('error', () => {
      if (this.ws === ws) this.setState('closed', '连接出错');
    });
    // 帧照收不发（这条连接不驱动身体），不订阅也不缓存
    ws.addEventListener('message', () => {});
  }

  private scheduleRetry(): void {
    if (this.closedByUs || this.retry) return;
    this.retry = setTimeout(() => {
      this.retry = null;
      this.open();
    }, 2000);
  }

  /** 把队列里的消息按序发出去；没连上就先留着（open 时再调一次） */
  private flush(): void {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    while (this.queue.length) {
      const msg = this.queue[0];
      try {
        ws.send(JSON.stringify({ t: Date.now(), ...msg }));
      } catch {
        return; // 连接正在断：留着，close→重连→open 再补
      }
      this.queue.shift();
    }
  }

  /** 入队 + 尝试立刻发。返回 true = 已入队（**不代表服务端已执行**，结果看 /health） */
  private send(payload: Record<string, unknown>): boolean {
    if (this.closedByUs) return false;
    this.queue.push(payload);
    if (this.queue.length > 8) this.queue.shift();
    this.ensure();
    this.flush();
    return true;
  }

  /** 播 mimic 默认段（服务端自己挑 clip：g1_mimic 的默认动作） */
  mimic(play: boolean): boolean {
    return this.send({ mimic: play ? 'play' : 'stop' });
  }

  /** 右手伸到 scene.json 的 ik_targets 默认取杯点 */
  ik(play: boolean): boolean {
    return this.send({ ik: play ? 'play' : 'stop' });
  }

  close(): void {
    this.closedByUs = true;
    this.queue.length = 0;
    if (this.retry) {
      clearTimeout(this.retry);
      this.retry = null;
    }
    try {
      this.ws?.close();
    } catch {
      /* 已经断了 */
    }
    this.ws = null;
    this.setState('closed');
  }
}
