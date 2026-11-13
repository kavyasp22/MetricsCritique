/**
 * Shared-link (URL tier, preliminary) panel: boots the real dashboard against the real
 * metrics.json and checks the card renders, shows both confidence tables, and carries the
 * "preliminary" wording. Same harness style as check_dim_labels.mjs.
 *
 * Run:  node UI/tests/check_url_tier.mjs
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const metrics = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost/',
  beforeParse(win) {
    win.fetch = (url) => Promise.resolve({ ok: true, json: () => Promise.resolve(String(url).includes('metrics.json') ? metrics : {}) });
    win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
    Object.defineProperty(win.HTMLElement.prototype, 'clientWidth', { get: () => 900 });
    Object.defineProperty(win.HTMLElement.prototype, 'clientHeight', { get: () => 400 });
  },
});
const script = dom.window.document.createElement('script');
script.textContent = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
dom.window.document.body.appendChild(script);
await new Promise((r) => setTimeout(r, 3000));
// panels render per mode; the shared-link card lives in the Detailed view
const advBtn = [...dom.window.document.querySelectorAll('button, a')].find((e) => /detailed/i.test(e.textContent));
if (advBtn) { advBtn.click(); }
await new Promise((r) => setTimeout(r, 3000));

let ok = 0, bad = 0;
const check = (n, c, d = '') => { (c ? ok++ : bad++); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${d ? '  ' + d : ''}`); };
const doc = dom.window.document;
const nLinks = (metrics.tables.cross_platform_url_links || { rows: [] }).rows.length;
const host = doc.querySelector('[data-panel="cross_platform_urls"]');
check('URL tier table shipped in metrics.json', nLinks > 0, `${nLinks} links`);
check('panel host exists', !!host);
const sums = host ? [...host.querySelectorAll('summary')].map((s) => s.textContent) : [];
check('two tables: high (open) and medium (collapsed)', sums.length === 2 && /High/.test(sums[0]) && /Medium/.test(sums[1]), sums.join(' | '));
const rows = host ? host.querySelectorAll('details')[0]?.querySelectorAll('tbody tr').length : 0;
check('high table has rows', rows > 0, `${rows} rows`);
check('card says preliminary', /preliminary/i.test(doc.body.textContent) && /Shared-Link Narratives/.test(doc.body.textContent));
check('no estimated views in this card', host ? !/Estimated Views/.test(host.textContent) : false);
console.log(`\n  ${ok}/${ok + bad} checks passed`);
process.exit(bad ? 1 : 0);
