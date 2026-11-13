/**
 * Tone mix and tone drift panels (Section 8, provisional). Boots the real dashboard against the real metrics.json
 * with synthetic tone-mix / drift columns injected into metrics_daily, so it also works before the metrics rebuild
 * has published them. Also checks the empty-state message when the columns are absent.
 *
 * Run:  node UI/tests/check_tone_panels.mjs
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const script = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
const base = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));

async function boot(metrics) {
  const dom = new JSDOM(html, {
    runScripts: 'dangerously', url: 'http://localhost/',
    beforeParse(win) {
      win.fetch = (u) => Promise.resolve({ ok: true, json: () => Promise.resolve(String(u).includes('metrics.json') ? metrics : {}) });
      win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
      Object.defineProperty(win.HTMLElement.prototype, 'clientWidth', { get: () => 900 });
      Object.defineProperty(win.HTMLElement.prototype, 'clientHeight', { get: () => 400 });
    },
  });
  const s = dom.window.document.createElement('script');
  s.textContent = script;
  dom.window.document.body.appendChild(s);
  await new Promise((r) => setTimeout(r, 2500));
  const btn = [...dom.window.document.querySelectorAll('button, a')].find((e) => /detailed/i.test(e.textContent));
  if (btn) { btn.click(); }
  await new Promise((r) => setTimeout(r, 3000));
  return dom;
}

let ok = 0, bad = 0;
const check = (n, c, d = '') => { (c ? ok++ : bad++); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${d ? '  ' + d : ''}`); };

// ---- 1. current published data: columns may be absent -> a clear empty-state, no crash
const d0 = await boot(JSON.parse(JSON.stringify(base)));
const mixHost0 = d0.window.document.querySelector('[data-panel="tone_mix"]');
const hasCols = base.tables.metrics_daily.columns.includes('tone_pos_n');
check('tone_mix host exists', !!mixHost0);
if (!hasCols) {
  check('absent columns -> explanatory message, not an error', /not in the published metrics yet/.test(mixHost0.textContent) && !/Render error/.test(mixHost0.textContent));
}

// ---- 2. inject synthetic columns and check the numbers shown
const m = JSON.parse(JSON.stringify(base));
const t = m.tables.metrics_daily;
const ci = (c) => t.columns.indexOf(c);
const iN = ci('tone_n'), iP = ci('platform'), iM = ci('tone_mean'), iW = ci('in_window');   // the dashboard shows in-window rows by default
const add = (name) => { if (ci(name) < 0) { t.columns.push(name); t.rows.forEach((r) => r.push(null)); } return ci(name); };
const cPos = add('tone_pos_n'), cNeg = add('tone_neg_n'), cW = add('tone_weighted'), cCov = add('tone_weight_cov');
const cZ = add('tone_drift_z'), cQ = add('tone_drift_q'), cF = add('tone_drift_flag'), cB = add('tone_drift_base_n');
t.rows.forEach((r) => {
  const n = r[iN];
  if (n == null || n <= 0) { return; }
  r[cPos] = Math.round(0.3 * n); r[cNeg] = Math.round(0.4 * n);   // every cell: ~30% positive, ~40% negative
  if (n >= 5 && r[iM] != null) { r[cW] = r[iM] * 0.9; }
  r[cCov] = 0.5;
});
// the dashboard applies its own platform/topic/window filters, so flag rows that are certain to pass them
const cand = t.rows.filter((r) => r[iN] >= 5 && (r[iW] === true || r[iW] === 1) && r[iP] === 'telegram' && r[ci('topic_id')] !== 'unassigned');
t.rows.forEach((r) => { if (r[iN] >= 5) { r[cZ] = -0.1; r[cF] = false; } });             // scored, not flagged
cand.slice(0, 12).forEach((r, i) => { r[cZ] = -2 - i * 0.5; r[cQ] = 0.01 + i * 0.001; r[cF] = true; r[cB] = 10; });   // flagged drops
const d1 = await boot(m);
const doc = d1.window.document;
const mix = doc.querySelector('[data-panel="tone_mix"]');
const rows = [...mix.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent));
check('tone mix lists platforms with scored posts', rows.length >= 5, `${rows.length} rows`);
const pctNum = (x) => parseFloat(x);
const big = rows.filter((r) => parseInt(r[1].replace(/[^0-9]/g, ''), 10) >= 200);
check('shares add to 100% on every row', rows.every((r) => Math.abs(pctNum(r[2]) + pctNum(r[3]) + pctNum(r[4]) - 100) <= 1.5));
check('injected ~30% positive / ~40% negative show up on large platforms',
  big.length > 0 && big.every((r) => Math.abs(pctNum(r[2]) - 30) <= 1.5 && Math.abs(pctNum(r[4]) - 40) <= 1.5), big.map((r) => `${r[0]} ${r[2]}/${r[4]}`).join('; '));
check('net sentiment score = 100 x (positive share - negative share)',
  big.length > 0 && big.every((r) => Math.abs(pctNum(r[5]) - (pctNum(r[2]) - pctNum(r[4]))) <= 1.2), big.map((r) => r[5]).join(', '));
check('weighted tone differs from the plain average by the injected factor (0.9)',
  big.some((r) => r[6] !== '—' && r[7] !== '—' && Math.abs(parseFloat(r[7]) - 0.9 * parseFloat(r[6])) <= 0.0015), big.map((r) => `${r[6]}->${r[7]}`).slice(0, 3).join('; '));
check('mix card says provisional', /provisional/i.test(mix.parentElement.textContent));
const drift = doc.querySelector('[data-panel="tone_drift"]');
const drows = [...drift.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent));
check('drift table lists only flagged rows, most negative first',
  drows.length >= 1 && drows.length <= 12 && drows.every((r, i) => i === 0 || parseFloat(drows[i - 1][4]) <= parseFloat(r[4])) && drows.every((r) => parseFloat(r[4]) <= -2),
  drows.length + ' rows: ' + drows.map((r) => r[4]).join(', '));
check('drift card explains the baseline excludes today', /today is not in the baseline/i.test(drift.parentElement.textContent));
console.log(`\n  ${ok}/${ok + bad} checks passed`);
process.exit(bad ? 1 : 0);
