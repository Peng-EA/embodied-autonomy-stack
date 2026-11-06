import { Sim } from '../core/sim.ts';
import { dockPosition } from '../core/scenario.ts';

let sim: Sim | null = null;
let timer: any = null;
let paused = false;

function post(msg: any) {
  (self as any).postMessage(msg);
}

async function start(opts: any) {
  stop();
  sim = await Sim.create(opts);
  paused = false;
  const events: any[] = [];
  let lastT = 0;
  let lastSent = -1;

  const tick = () => {
    if (!sim || paused) return;
    sim.step();
    const t = sim.t;
    if (t - lastSent >= 0.05) {
      lastSent = t;
      const newEvents = sim.bus.events.filter((e) => e.t_ms > lastT);
      lastT = t * 1000;
      const p = sim.world.pose();
      post({
        type: 'frame',
        t,
        x: p.x,
        z: p.z,
        dock: dockPosition(sim.sc, t),
        battery: sim.l1.battery,
        clearance: sim.world.sense().clearance,
        speed: sim.world.speed(),
        fear: sim.l3.fear,
        stale: sim.l4.staleDiscarded,
        degraded: sim.l4.degraded,
        events: newEvents.slice(-8).map((e) => ({ t: e.t_ms / 1000, layer: e.layer, event: e.event })),
      });
    }
    if (sim.done) {
      post({ type: 'done', result: sim.result() });
      stop();
    }
  };
  timer = setInterval(tick, 10);
  post({ type: 'started' });
}

function stop() {
  if (timer) clearInterval(timer);
  timer = null;
}

(self as any).onmessage = (ev: MessageEvent) => {
  const msg = ev.data;
  if (msg.type === 'start') void start(msg.opts);
  else if (msg.type === 'pause') paused = !paused;
  else if (msg.type === 'reset') { stop(); sim = null; post({ type: 'reset' }); }
};
