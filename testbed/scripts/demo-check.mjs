#!/usr/bin/env node
// 演示台的验收跑（A + B + C 一次到位），无头 + 本机 Edge。
//
//   node testbed/scripts/demo-check.mjs [--url http://127.0.0.1:5183/] [--headed]
//
// 顺序即验收顺序：
//   A) 没有 --url 就自己起全套（build + sim_server 自选端口 + 静态服务），并 GET 页面确认 200
//   B) 1440×900 与 1280×800 各量一次 getBoundingClientRect（面板不裁 / 状态吸顶 / 曲线卡片完整 /
//      事件折叠 / 字号 ≤2 档 / 三列与四宫格对齐），打印每个关键元素的 rect；console 错误必须 0
//   C) 两档视口各截图一张：testbed/logs/demo-<ts>.png 与 demo-<ts>-1280x800.png
//   C) 用真实 input 事件把 vx 滑杆拖到 0.5，等 3 s：另开一条 WS 旁听采样 speed，
//      再读服务端 /health，要求 mode=POLICY 且采样速度 > 0.3 m/s
//
// 为什么速度来自「另开」的 WS 而不是页面自己的数字：这样「确实在走」不是页面自证。
// 页面的 __demo.state() 只做交叉核对（打印出来，不作判据）。
import puppeteer from 'puppeteer-core';
import path from 'node:path';
import { mkdir, stat } from 'node:fs/promises';
import { startDemoStack, TESTBED } from './lib/demo-server.mjs';
import { wsCtor, healthJson } from './lib/sim-server.mjs';

// 正斜杠就够（Node 在 Windows 上接受），省得源码里到处是转义反斜杠
const EDGE = process.env.BROWSER_PATH ?? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const LIMITS = { sliders: 3, buttons: 10, toggles: 4, speedMin: 0.3, driveSeconds: 3 };

function parseArgs(argv) {
  const out = { url: null, headed: false, keep: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--url') out.url = String(argv[++i]);
    else if (a === '--headed') out.headed = true;
    else if (a === '--keep') out.keep = true;
    else if (a === '--help' || a === '-h') out.url = 'HELP';
  }
  return out;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function stamp() {
  const d = new Date();
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
}

function median(xs) {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

async function openAuditSocket(wsUrl) {
  const { ctor, name } = wsCtor();
  const samples = [];
  const sock = new ctor(wsUrl);
  sock.on('message', (raw) => {
    try {
      const f = JSON.parse(String(raw));
      if (typeof f.speed === 'number' && typeof f.tSim === 'number') {
        samples.push({ at: Date.now(), speed: f.speed, mode: f.mode ?? null, tSim: f.tSim });
      }
    } catch {
      /* 坏帧不计数 */
    }
  });
  await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('旁听 WS 3 s 没连上：' + wsUrl)), 3000);
    sock.once('open', () => { clearTimeout(t); resolve(); });
    sock.once('error', (e) => { clearTimeout(t); reject(e); });
  });
  return { samples, name, close: () => { try { sock.close(); } catch {} } };
}


/** ---------------- 布局断言（D1b B）：只量 rect，不靠截图肉眼看 ---------------- */
async function assertLayout(page, label) {
  const data = await page.evaluate(() => {
    const vp = { w: window.innerWidth, h: window.innerHeight };
    const tol = 0.5;
    const R = (el) => {
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return {
        x: +b.x.toFixed(1),
        y: +b.y.toFixed(1),
        w: +b.width.toFixed(1),
        h: +b.height.toFixed(1),
        bottom: +b.bottom.toFixed(1),
        right: +b.right.toFixed(1),
      };
    };
    const q = (s) => document.querySelector(s);

    const panel = q('#panel');
    const panelBody = q('#panel-body');
    const scroll = q('#panel-scroll');
    const fade = q('.scroll-fade');
    const status = q('#sec-status');
    const chartCard = q('#telemetry-card');
    const chart = q('#chart');
    const uplot = chart && chart.querySelector('.uplot');
    const logFold = q('#log-fold');
    const logEl = q('#log');
    const legend = q('#legend');
    const actBtns = [...document.querySelectorAll('#panel button[data-act]')];
    const presetBtns = [...document.querySelectorAll('#panel button[data-preset]')];

    // 先归零：前面的点击会把开关滚进视野，截图和 rect 都要从"打开页面时"的状态量起
    scroll.scrollTop = 0;

    const rects = {
      panel: R(panel),
      panel_body: R(panelBody),
      panel_scroll: R(scroll),
      status: R(status),
      chart_card: R(chartCard),
      chart: R(chart),
      uplot: R(uplot),
      log_fold: R(logFold),
      legend: R(legend),
    };
    const secEls = [...scroll.querySelectorAll('.sec')];
    const sections = secEls.map((s) => ({
      title: (s.querySelector('h2')?.textContent || '').trim().split(/\s+/)[0],
      r: R(s),
    }));
    const scrollOverflow = scroll.scrollHeight - scroll.clientHeight;

    const checks = [];
    const add = (name, ok, detail = '') => checks.push({ name, pass: !!ok, detail });

    // 1) 面板本身：不超视口、固定宽度、自身不滚（只有 .panel-scroll 滚）
    add(
      'panel_within_viewport',
      rects.panel.bottom <= vp.h + tol && rects.panel.right <= vp.w + tol && rects.panel.y >= 0,
      'panel.bottom=' + rects.panel.bottom + ' vp.h=' + vp.h + ' panel.right=' + rects.panel.right + ' vp.w=' + vp.w,
    );
    add('panel_fixed_width_400', Math.abs(rects.panel.w - 400) <= tol, 'w=' + rects.panel.w);
    add('panel_not_self_scrolling', panel.scrollHeight <= panel.clientHeight + 1, 'scrollH=' + panel.scrollHeight + ' clientH=' + panel.clientHeight);
    add(
      'scroll_container_inside_viewport',
      /auto|scroll/.test(getComputedStyle(scroll).overflowY) && rects.panel_scroll.bottom <= vp.h + tol,
      'overflowY=' + getComputedStyle(scroll).overflowY + ' bottom=' + rects.panel_scroll.bottom,
    );

    // 2) 状态区吸顶：滚到底后 top 仍贴着滚动容器顶边（0 处是自然位置）
    const statusTop0 = R(status).y;
    scroll.scrollTop = scroll.scrollHeight;
    const statusTop1 = R(status).y;
    const maxScroll = scroll.scrollTop;
    const lastSecBottomAtBottom = R(secEls[secEls.length - 1]).bottom;
    const lastSecReachable = lastSecBottomAtBottom <= R(scroll).bottom + tol;
    scroll.scrollTop = 0;
    add(
      'status_sticky',
      getComputedStyle(status).position === 'sticky' && Math.abs(statusTop1 - rects.panel_scroll.y) <= 2,
      'position=' + getComputedStyle(status).position + ' top@0=' + statusTop0 + ' top@bottom=' + statusTop1 + ' scroll=' + maxScroll,
    );
    add(
      'status_visible_while_scrolled',
      statusTop1 >= rects.panel_scroll.y - tol && statusTop1 < vp.h,
      'top@bottom=' + statusTop1,
    );
    add(
      'scroll_reaches_last_section',
      lastSecReachable,
      'overflow=' + scrollOverflow + ' lastSecBottom@bottom=' + lastSecBottomAtBottom + ' scrollBottom=' + R(scroll).bottom,
    );
    add(
      'panel_body_inside_viewport',
      rects.panel_body.bottom <= vp.h + tol && rects.panel_body.y >= 0,
      JSON.stringify(rects.panel_body),
    );
    // 底部渐隐必须和"真的需要滚"一致：不滚的时候别显示、要滚的时候必须有
    const hasScroll = scroll.scrollHeight > scroll.clientHeight + 1;
    const hintOn = panelBody.classList.contains('scrollable') && parseFloat(getComputedStyle(fade).opacity) > 0.5;
    add(
      'scroll_hint_matches_overflow',
      hasScroll === panelBody.classList.contains('scrollable') && (hasScroll ? hintOn : true),
      'overflow=' + scrollOverflow + ' class=' + panelBody.classList.contains('scrollable') + ' fadeOpacity=' + getComputedStyle(fade).opacity,
    );

    // 3) 曲线卡片：独立定高、默认完整可见
    add(
      'chart_card_fully_visible',
      rects.chart_card.bottom <= vp.h + tol && rects.chart_card.y >= 0,
      'y=' + rects.chart_card.y + ' bottom=' + rects.chart_card.bottom + ' vp.h=' + vp.h,
    );
    add('chart_height_110_130', rects.chart.h >= 110 && rects.chart.h <= 130, 'h=' + rects.chart.h);
    add(
      'chart_fully_inside_card',
      !!rects.chart &&
        rects.chart.w >= 200 &&
        rects.chart.right <= rects.chart_card.right + tol &&
        rects.chart.bottom <= rects.chart_card.bottom + tol &&
        rects.chart.y >= rects.chart_card.y - tol,
      'chart=' + JSON.stringify(rects.chart) + ' card=' + JSON.stringify(rects.chart_card),
    );
    add(
      'uplot_matches_chart',
      !!rects.uplot &&
        Math.abs(rects.uplot.h - rects.chart.h) <= 1 &&
        Math.abs(rects.uplot.w - rects.chart.w) <= 1,
      'uplot=' + JSON.stringify(rects.uplot),
    );
    add(
      'chart_card_above_log_fold',
      rects.chart_card.bottom <= rects.log_fold.y + 1,
      'card.bottom=' + rects.chart_card.bottom + ' log.y=' + rects.log_fold.y,
    );

    // 4) 事件折叠区：默认收起、展开最多 3 行、贴在面板底部
    const closedByDefault = logFold.open;
    add(
      'log_fold_collapsed_by_default',
      closedByDefault === false && rects.log_fold.h <= 44,
      'open=' + closedByDefault + ' h=' + rects.log_fold.h,
    );
    add('log_fold_at_panel_bottom', Math.abs(rects.log_fold.bottom - rects.panel.bottom) <= 1.5, 'log.bottom=' + rects.log_fold.bottom + ' panel.bottom=' + rects.panel.bottom);
    add('log_not_on_stage', document.querySelectorAll('#stage .stage-log, #stage #log').length === 0, 'stage_log_nodes=' + document.querySelectorAll('#stage .stage-log, #stage #log').length);
    add('stage_legend_kept', !!legend && rects.legend.bottom <= vp.h + tol, JSON.stringify(rects.legend));

    // 5) 字号：面板内可见文字最多两种字号（含展开后的日志）
    const sizes = new Set();
    const collect = () => {
      for (const el of panel.querySelectorAll('*')) {
        const ownText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
        if (!ownText) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        sizes.add(parseFloat(cs.fontSize).toFixed(2));
      }
    };
    collect();
    logFold.open = true;
    collect();
    const logH = R(logEl).h;
    const openFoldRect = R(logFold);
    const openCardRect = R(chartCard);
    logFold.open = false;
    add('font_sizes_at_most_2', sizes.size <= 2, [...sizes].join(','));
    add('log_max_3_lines', logH >= 40 && logH <= 74, 'open_h=' + logH + ' (3 行正文 + padding)');
    add(
      'log_fold_open_within_viewport',
      openFoldRect.bottom <= vp.h + tol && openFoldRect.y >= 0 && openCardRect.y >= 0,
      'open log=' + JSON.stringify(openFoldRect) + ' chart_card=' + JSON.stringify(openCardRect),
    );

    // 6) 网格：动作 3 列等宽、预设 2×2 对齐
    const cols = (rs) => [...new Set(rs.map((r) => Math.round(r.x)))].sort((a, b) => a - b);
    const actR = actBtns.map(R);
    const preR = presetBtns.map(R);
    const wspan = (rs) => Math.max(...rs.map((r) => r.w)) - Math.min(...rs.map((r) => r.w));
    add(
      'action_grid_3_equal_cols',
      actR.length === 6 && cols(actR).length === 3 && wspan(actR) <= 1,
      'n=' + actR.length + ' cols=' + cols(actR).length + ' w=' + wspan(actR).toFixed(2),
    );
    add(
      'preset_grid_2x2_aligned',
      preR.length === 4 &&
        cols(preR).length === 2 &&
        wspan(preR) <= 1 &&
        Math.abs(preR[0].x - preR[2].x) <= 1 &&
        Math.abs(preR[1].x - preR[3].x) <= 1,
      'n=' + preR.length + ' cols=' + cols(preR).length + ' w=' + wspan(preR).toFixed(2),
    );

    // 7) 分区都可滚到、横向不裁
    add(
      'scroll_sections_fit_width',
      sections.every((s) => s.r.right <= rects.panel.right + tol && s.r.x >= rects.panel.x - tol),
      sections.map((s) => s.title + ':x' + s.r.x + '..' + s.r.right).join(' '),
    );
    add(
      'fixed_cards_within_viewport',
      [chartCard, logFold].every((el) => {
        const r = R(el);
        return r.bottom <= vp.h + tol && r.y >= 0;
      }),
      'chart=' + rects.chart_card.bottom + ' log=' + rects.log_fold.bottom + ' vp.h=' + vp.h,
    );
    add(
      'no_page_overflow',
      document.documentElement.scrollHeight <= vp.h + 1,
      'doc.scrollH=' + document.documentElement.scrollHeight + ' vp.h=' + vp.h,
    );

    return {
      vp,
      rects,
      sections,
      sizes: [...sizes],
      act0: actR[0],
      pre0: preR[0],
      checks,
    };
  });

  const rectLine = (name, r) => {
    if (!r) return console.log('[layout ' + label + '] rect ' + name + ' = MISSING');
    console.log(
      '[layout ' + label + '] rect ' + name + ' = x=' + r.x + ' y=' + r.y + ' w=' + r.w + ' h=' + r.h +
        ' right=' + r.right + ' bottom=' + r.bottom,
    );
  };
  console.log('[layout ' + label + '] viewport=' + data.vp.w + 'x' + data.vp.h);
  for (const k of ['panel', 'panel_body', 'panel_scroll', 'status']) rectLine(k, data.rects[k]);
  for (const s of data.sections) rectLine('sec:' + s.title, s.r);
  for (const k of ['chart_card', 'chart', 'uplot', 'log_fold', 'legend']) rectLine(k, data.rects[k]);
  rectLine('btn:act[0]', data.act0);
  rectLine('btn:preset[0]', data.pre0);
  console.log('[layout ' + label + '] font_sizes=' + data.sizes.join(','));

  const fails = [];
  for (const c of data.checks) {
    console.log('[layout ' + label + '] ' + (c.pass ? 'PASS' : 'FAIL') + ' ' + c.name + (c.detail ? ' · ' + c.detail : ''));
    if (!c.pass) fails.push('[' + label + '] 布局断言 ' + c.name + ' 失败：' + c.detail);
  }
  console.log('[layout ' + label + '] checks=' + data.checks.filter((c) => c.pass).length + '/' + data.checks.length);
  return { fails };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.url === 'HELP') {
    console.log('node testbed/scripts/demo-check.mjs [--url http://127.0.0.1:5183/] [--headed]');
    return 0;
  }

  let stack = null;
  let url = args.url;
  let sim = null;
  if (!url) {
    stack = await startDemoStack({ webPort: 0, build: true, quiet: false });
    url = stack.url;
    sim = stack.sim;
  } else {
    let cfg = null;
    try {
      const r = await fetch(new URL('/config.json', url).href, { signal: AbortSignal.timeout(3000) });
      if (r.ok) cfg = await r.json();
    } catch {
      cfg = null;
    }
    sim = cfg && cfg.simWs ? { url: cfg.simWs, healthUrl: cfg.simHttp } : null;
  }
  if (!sim || !sim.url) {
    throw new Error('拿不到 sim_server 的 ws 地址（/config.json 没应答）：先 node testbed/scripts/serve-demo.mjs');
  }

  const pageUrl = new URL(url);
  pageUrl.searchParams.set('ws', sim.url);

  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: !args.headed,
    args: ['--no-sandbox', '--disable-gpu-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  });

  const errors = [];
  let pass = true;
  const fails = [];
  const line = (k, v) => console.log(k + '=' + v);

  try {
    const page = await browser.newPage();
    page.on('console', (m) => {
      if (m.type() === 'error') errors.push(m.text());
    });
    page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message));
    await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });

    const resp = await page.goto(pageUrl.href, { waitUntil: 'domcontentloaded', timeout: 45000 });
    const status = resp ? resp.status() : 0;
    console.log('[demo-check] A) page ' + pageUrl.href);
    line('page_status', status);
    if (status !== 200) fails.push('页面 HTTP ' + status + ' != 200');

    await page.waitForFunction('window.__demo && window.__demo.ready === true', { timeout: 60000, polling: 250 });
    await sleep(900); // 让状态区/曲线跑一轮

    const counts = await page.evaluate(() => ({
      sliders: document.querySelectorAll('input[type="range"][data-slider]').length,
      buttons: document.querySelectorAll('button').length,
      toggles: document.querySelectorAll('input[type="checkbox"][data-toggle]').length,
      status: document.querySelectorAll('#panel .stat').length,
    }));
    line('elements', 'sliders=' + counts.sliders + ' buttons=' + counts.buttons + ' toggles=' + counts.toggles + ' status_rows=' + counts.status);
    if (counts.sliders < LIMITS.sliders) fails.push('滑杆 ' + counts.sliders + ' < ' + LIMITS.sliders);
    if (counts.buttons < LIMITS.buttons) fails.push('按钮 ' + counts.buttons + ' < ' + LIMITS.buttons);
    if (counts.toggles < LIMITS.toggles) fails.push('开关 ' + counts.toggles + ' < ' + LIMITS.toggles);

    // 截图里把 clearance / 接触打开：这两个开关得在画面上有后果（不是摆着好看）
    for (const t of ['rays', 'contacts']) await page.click('input[data-toggle="' + t + '"]');
    await sleep(400);
    const vis = await page.evaluate(() => {
      const s = window.__demo.state();
      return { rays: s.rays, contacts: s.contacts, room: s.room };
    });
    line('visual_toggles', 'rays=' + JSON.stringify(vis.rays) + ' contacts=' + JSON.stringify(vis.contacts));
    if (!vis.rays.visible || vis.rays.count !== 7) fails.push('clearance 射线没画出来：' + JSON.stringify(vis.rays));
    if (!vis.contacts.visible || vis.contacts.ankles < 2) fails.push('接触标记没就位：' + JSON.stringify(vis.contacts));

    // B) 1440×900 布局断言：面板不裁、状态吸顶、曲线卡片完整可见、事件折叠、字号/网格
    const lay1440 = await assertLayout(page, '1440x900');
    fails.push(...lay1440.fails);

    const before = await page.evaluate(() => window.__demo.state());
    const audit = await openAuditSocket(sim.url);
    const t0 = Date.now();
    await page.$eval('input[data-slider="vx"]', (el) => {
      el.value = '0.5';
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });

    await sleep(2500);
    await mkdir(path.join(TESTBED, 'logs'), { recursive: true });
    const shotPath = path.join(TESTBED, 'logs', 'demo-' + stamp() + '.png');
    await page.screenshot({ path: shotPath });
    const shotStat = await stat(shotPath);
    line('screenshot_1440x900', path.relative(TESTBED, shotPath) + ' (' + Math.round(shotStat.size / 1024) + ' KB)');
    if (!shotStat.size) fails.push('截图是空文件');

    const elapsedMs = Date.now() - t0;
    if (elapsedMs < LIMITS.driveSeconds * 1000) await sleep(LIMITS.driveSeconds * 1000 - elapsedMs);

    const drive = audit.samples.filter((s) => s.at - t0 <= LIMITS.driveSeconds * 1000 + 400);
    const maxSpeed = drive.reduce((m, s) => Math.max(m, s.speed), 0);
    const p50 = median(drive.map((s) => s.speed));
    const lastMode = drive.length ? drive[drive.length - 1].mode : null;
    line('ws_samples', drive.length + ' name=' + audit.name + ' mode_last=' + lastMode);
    line('speed_max', maxSpeed.toFixed(3) + ' speed_p50=' + (Number.isFinite(p50) ? p50.toFixed(3) : 'NaN'));

    const health = await healthJson(sim.healthUrl);
    const healthMode = health && health.mode ? health.mode.mode : null;
    const healthSwitches = health && health.mode ? health.mode.switches : null;
    line('health_mode', String(healthMode) + ' switches=' + healthSwitches + ' clients=' + (health ? health.clients : '?'));
    if (healthMode !== 'POLICY') fails.push('/health mode=' + healthMode + ' != POLICY');
    if (!(maxSpeed > LIMITS.speedMin)) fails.push('WS 采样最大速度 ' + maxSpeed.toFixed(3) + ' <= ' + LIMITS.speedMin);

    const after = await page.evaluate(() => window.__demo.state());
    line('page_state', JSON.stringify({
      mode: after.mode, speed: after.speed, vx: after.sliders.vx, cmd: after.gate.cmd,
      source: after.gate.source, yawGain: after.gate.yawGain, mesh: after.mesh, room: after.room,
    }));
    line('gate_before', JSON.stringify({ mode: before.mode, speed: before.speed, source: before.gate.source, cmd: before.gate.cmd }));
    audit.close();

    // B + C) 第二档视口 1280×800：同一份布局断言再量一遍，再来一张图
    await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
    await sleep(600);
    const lay1280 = await assertLayout(page, '1280x800');
    fails.push(...lay1280.fails);
    const shot2Path = path.join(TESTBED, 'logs', 'demo-' + stamp() + '-1280x800.png');
    await page.screenshot({ path: shot2Path });
    const shot2Stat = await stat(shot2Path);
    line('screenshot_1280x800', path.relative(TESTBED, shot2Path) + ' (' + Math.round(shot2Stat.size / 1024) + ' KB)');
    if (!shot2Stat.size) fails.push('1280×800 截图是空文件');
    await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });

    line('console_errors', errors.length);
    if (errors.length) {
      fails.push('console 错误 ' + errors.length + ' 条');
      for (const e of errors.slice(0, 5)) console.error('  [console] ' + e);
    }
  } catch (e) {
    fails.push('异常：' + (e instanceof Error ? e.message : String(e)));
    console.error(e instanceof Error ? e.stack || e.message : String(e));
  } finally {
    await browser.close();
    if (!args.keep) {
      try { stack?.close(); } catch {}
    } else if (stack) {
      console.log('[demo-check] --keep：服务留在 ' + stack.url + '（Ctrl+C 退出）');
    }
  }

  pass = fails.length === 0;
  for (const f of fails) console.error('FAIL: ' + f);
  console.log('verdict=' + (pass ? 'pass' : 'fail'));
  return pass ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    console.error('[demo-check] ' + (e instanceof Error ? e.stack || e.message : String(e)));
    process.exit(1);
  },
);
