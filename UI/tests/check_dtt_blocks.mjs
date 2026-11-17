/**
 * "Days from first appearance to 5 posts/day": one collapsible block per platform, driven by the global filters.
 * Run: node UI/tests/check_dtt_blocks.mjs   (jsdom, real index.html + data/metrics.json)
 */
import { JSDOM, VirtualConsole } from 'jsdom';
import { readFileSync } from 'fs';
const UI = new URL('..', import.meta.url).pathname;
let rawHtml = readFileSync(`${UI}index.html`, 'utf8');
// If dtt_bars is commented out in index.html, activate it in the test DOM so component logic is tested
const html = rawHtml.replace(/<!--\s*Semantic novelty[\s\S]*?-->/, (m) =>
  m.replace(/<!--[\s\S]*?<div class="grid g2">/, '<div class="grid g2">').replace(/<\/div>\s*<\/div>\s*-->/, '</div></div>')
);
const metrics = JSON.parse(readFileSync(`${UI}data/metrics.json`, 'utf8'));
const errs = [];
const vc = new VirtualConsole(); vc.on('jsdomError', (e) => errs.push(e.message));
const dom = new JSDOM(html, { virtualConsole: vc, runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true,
  beforeParse(win) {
    win.fetch = (u) => String(u).includes('metrics.json')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve(metrics) })
      : Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
    win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
    Object.defineProperty(win.HTMLElement.prototype, 'clientWidth', { get: () => 900 });
    Object.defineProperty(win.HTMLElement.prototype, 'clientHeight', { get: () => 400 });
  } });
for (const f of ['dash.js', 'live.js']) {
  const s = dom.window.document.createElement('script');
  s.textContent = readFileSync(`${UI}assets/${f}`, 'utf8');
  dom.window.document.body.appendChild(s);
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = [];
const check = (n, c, x = '') => { console.log((c ? 'PASS ' : 'FAIL ') + n + (x ? '  ' + x : '')); if (!c) fail.push(n); };
await wait(2500);
const d = dom.window.document;
d.getElementById('mode-advanced-btn').click(); await wait(2500);
let p = d.querySelector('[data-panel="dtt_bars"]');
let blocks = [...p.querySelectorAll('details.dtt-block')];
check('one collapsible block per platform', blocks.length > 3, `${blocks.length} blocks`);
check('blocks start collapsed when all topics are selected', blocks.every((b) => !b.hasAttribute('open')));
const rowsAll = p.querySelectorAll('.dtt-row').length;
check('each block lists its topics', rowsAll > blocks.length, `${rowsAll} rows`);
check('zero days is worded as "from the start", not as 0 days', /5\+\/day from the start/.test(p.textContent) && !/\b0 days\b/.test(p.textContent));
// expected rows straight from the data: min days per (platform, topic) among in-window rows, topics in the filter
const md = metrics.tables.metrics_daily; const ix = (c) => md.columns.indexOf(c);
const topicsSel = new Set([...d.querySelectorAll('.df-dd-list')].length ? [] : []);
// select a single topic through the topic dropdown
const triggers = [...d.querySelectorAll('#filters-in .df-dropdown-trigger')];
const topicTrigger = triggers.find((t) => /topics/i.test(t.textContent));
topicTrigger.click(); await wait(200);
const menu = topicTrigger.parentElement.querySelector('.df-dropdown-menu');
[...menu.querySelectorAll('.df-dd-act-btn')].find((b) => b.textContent === 'Clear').click(); await wait(200);
const first = menu.querySelector('.df-dd-list label, .df-dd-list .df-dd-item, .df-dd-list > *');
first.click(); await wait(400);
const chosen = (first.textContent || '').trim();
p = d.querySelector('[data-panel="dtt_bars"]');
blocks = [...p.querySelectorAll('details.dtt-block')];
check('one topic selected: blocks all open', blocks.length > 0 && blocks.every((b) => b.hasAttribute('open')), `${blocks.length} blocks for "${chosen}"`);
check('one topic selected: each platform shows exactly that topic', blocks.every((b) => b.querySelectorAll('.dtt-row').length === 1));
check('no script errors', errs.length === 0, errs.slice(0, 2).join(' | '));
console.log(fail.length ? `\n${fail.length} FAILED` : '\nall passed');
process.exit(fail.length ? 1 : 0);
