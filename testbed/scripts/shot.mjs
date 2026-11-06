import puppeteer from 'puppeteer-core';

const url = process.argv[2] ?? 'http://localhost:4174/console.html';
const out = process.argv[3] ?? 'shot.png';
const browser = await puppeteer.launch({
  executablePath:
    process.env.BROWSER_PATH ?? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu-sandbox'],
});
const page = await browser.newPage();
const errs = [];
page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
page.on('pageerror', (e) => errs.push('PAGEERROR ' + e.message));
await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
await page.goto(url, { waitUntil: 'networkidle2', timeout: 40000 });
await new Promise((r) => setTimeout(r, 3000));
await page.screenshot({ path: out });
console.log('screenshot', out);
console.log('errors', JSON.stringify(errs, null, 1));
await browser.close();
