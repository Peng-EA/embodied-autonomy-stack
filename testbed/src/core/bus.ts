export interface SimEvent {
  t_ms: number;
  layer: string;
  event: string;
  decision_id: string;
  [key: string]: unknown;
}

/** 统一事件总线：所有层的事件都带仿真时间戳，日志/回放/UI 读同一份。 */
export class Bus {
  readonly events: SimEvent[] = [];
  onEvent?: (e: SimEvent) => void;
  logAll = true;

  emit(t_ms: number, layer: string, event: string, extra: Record<string, unknown> = {}): void {
    const e: SimEvent = { t_ms, layer, event, decision_id: String(extra.decision_id ?? ''), ...extra };
    if (this.logAll) this.events.push(e);
    this.onEvent?.(e);
  }

  count(event: string): number {
    let n = 0;
    for (const e of this.events) if (e.event === event) n++;
    return n;
  }
}
