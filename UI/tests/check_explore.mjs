/**
 * Explore page: four tiles, per-tile filters, alerts strip, mode switching.
 * Renders the real index.html with the real metrics.json in jsdom; the viral-posts API is stubbed.
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const script = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
const base = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));
const errors = [];
let calls = [];

const dom = new JSDOM(html, {
  runScripts: 'dangerously', url: 'http://localhost/',
  beforeParse(win) {
    win.fetch = (u) => {
      u = String(u);
      if (u.includes('/api/explore/viral')) {
        calls.push(u);
        return Promise.resolve({ ok: true, json: () => Promise.resolve({
          total_posts: 12,
          with_views: [{ post_uid: 'a', platform: 'twitter', posted_at: '2026-10-20 10:00:00+00', text: 'hello world', url: 'https://x.test/1', views: 5000, likes: 10, shares: 2, engagement: 12 }],
          without_views: [{ post_uid: 'b', platform: 'instagram', posted_at: '2026-10-21 10:00:00+00', text: 'no views here', url: null, views: null, likes: 50, shares: 1, engagement: 51 }] }) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(u.includes('metrics.json') ? base : {}) });
    };
    win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
    Object.defineProperty(win.HTMLElement.prototype, 'clientWidth', { get: () => 900 });
    Object.defineProperty(win.HTMLElement.prototype, 'clientHeight', { get: () => 400 });
  },
});
dom.window.addEventListener('error', (e) => errors.push(e.message || String(e)));
const s = dom.window.document.createElement('script');
s.textContent = script; dom.window.document.body.appendChild(s);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
await wait(2500);
const doc = dom.window.document;
let ok = 0, bad = 0;
const check = (n, c, d = '') => { c ? ok++ : bad++; console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${d ? '  ' + d : ''}`); };

const btn = doc.getElementById('mode-explore-btn');
check('Explore button exists', !!btn);
btn.click(); await wait(600);
check('Explore view shown, others hidden',
  doc.getElementById('explore-view').style.display !== 'none' &&
  doc.getElementById('basic-view').style.display === 'none' &&
  doc.getElementById('advanced-view').style.display === 'none');
check('data-mode is explore', doc.body.getAttribute('data-mode') === 'explore');
const tiles = doc.querySelectorAll('.ex-tile');
check('four tiles', tiles.length === 4, String(tiles.length));
check('tile titles', ['Trending Topics', 'Viral Posts', 'Cross-Platform Spread', 'Trend Lifecycle']
  .every((t) => [...tiles].some((x) => x.textContent.includes(t))));
check('Needs attention / status panel rendered', !!doc.querySelector('#explore-view .attn-box'));
check('alerts show entity names, not raw keys like person:xyz', !/\b(person|org|location|event):[a-z_]/i.test(doc.querySelector('#explore-view').textContent.split('Social Signal Monitor')[0]) &&
  !/\b(person|org|location|event):[a-z_]/i.test(doc.querySelector('#explore-view [data-panel="explore_alerts"]').textContent),
  (doc.querySelector('#explore-view .attn-box')?.textContent || '').slice(0, 90));
check('Recommended alerts list has entity names (e.g. Barack Obama) not keys', /Barack Obama|Australian Strategic|Bolsonaro/.test(doc.querySelector('#explore-view [data-panel="explore_alerts"]').textContent));
check('Recommended alerts panel rendered', (doc.querySelector('#explore-view [data-panel="explore_alerts"]')?.textContent || '').length > 20);
check('Trending Topics table has rows', doc.querySelectorAll('#explore-view [data-panel="explore_view"] table tbody tr').length > 0,
  String(doc.querySelectorAll('#explore-view [data-panel="explore_view"] table tbody tr').length));

const view = () => doc.querySelector('#explore-view [data-panel="explore_view"]');
const rows = (k) => { [...doc.querySelectorAll('.ex-tile')].find((x) => x.dataset.tile === k).click(); return wait(500).then(() => view().querySelectorAll('table tbody tr').length); };
const topicHead = [...view().querySelectorAll('table thead th')].map((x) => x.textContent);
check('Trending Topics uses the stored metrics (emergence, q-value, alert days, momentum)',
  ['Emergence', 'Min q', 'Alert days', 'Burst days', 'Momentum'].every((c) => topicHead.some((h) => h.includes(c))), topicHead.join(' | '));
const lifeRows = await rows('life');
check('Lifecycle has onset slope, half-life and persistence table',
  /Onset slope/.test(view().textContent) && /Persistence by topic and platform/.test(view().textContent));
check('Trend Lifecycle shows rows or an explicit empty state', lifeRows > 0 || /No rows/.test(view().textContent), String(lifeRows));
const spreadRows = await rows('spread');
const subBtns = [...view().querySelectorAll('.df-preset-btn')].map((x) => x.textContent);
check('Spread has three evidence tiers', ['Same text', 'Shared links', 'Similar meaning'].every((c) => subBtns.some((b) => b.includes(c))), subBtns.join(' | '));
[...view().querySelectorAll('.df-preset-btn')].find((x) => x.textContent.includes('Shared links')).click(); await wait(500);
check('no shared link starts in the last 7 days: explicit empty state, not a blank', /No rows/.test(view().textContent));
[...doc.querySelectorAll('#explore-view .df-preset-btn')].find((x) => x.textContent === 'Full window').click(); await wait(600);
check('Shared links tier renders with match confidence column', /Match/.test([...view().querySelectorAll('table thead th')].map((x) => x.textContent).join(' ')));
[...view().querySelectorAll('.df-preset-btn')].find((x) => x.textContent.includes('Similar meaning')).click(); await wait(500);
check('Similar-meaning tier is labelled preliminary', /Preliminary/.test(view().textContent));
check('Cross-Platform Spread shows rows or an explicit empty state', spreadRows > 0 || /No rows/.test(view().textContent), String(spreadRows));
await rows('viral'); await wait(500);
const vtext = view().textContent;
{
  const heads = [...view().querySelectorAll('table thead th')].map((x) => x.textContent);
  const iv = heads.findIndex((x) => x.includes('Observed views')), ir = heads.findIndex((x) => x.includes('Reach vs author norm'));
  const bad = [...view().querySelectorAll('table tbody tr')].filter((tr) => tr.children[iv]?.textContent.trim() === '\u2014' && tr.children[ir]?.textContent.trim() !== '\u2014');
  check('a group with no observed views shows reach as a dash, never 0.0x', iv > 0 && ir > 0 && bad.length === 0, String(bad.length));
}
check('Viral card shows stored virality percentile + audience-normalised reach groups', /Virality pct/.test(vtext) && /Reach vs author norm/.test(vtext));
check('Breakout groups table has rows', view().querySelectorAll('table tbody tr').length > 0, String(view().querySelectorAll('table tbody tr').length));
check('Viral Posts calls the API', calls.length >= 1, calls[0] || '');
check('Viral Posts shows two lists', view().querySelectorAll('.ex-col').length === 2);
check('post without views is in the engagement list, not ranked by views',
  [...view().querySelectorAll('.ex-col')][1]?.textContent.includes('no views here'));
check('source link only for http(s) urls', view().querySelectorAll('.ex-post-link a').length === 1);
// per-tile filter: platform Clear -> back to all, search box applies on Enter
const q = doc.querySelector('.ex-q');
q.value = 'zzzzqqq'; q.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
await wait(700);
check('search sends q to the API', calls.some((c) => c.includes('q=zzzzqqq')));
await rows('topics');
const q2 = doc.querySelector('.ex-q'); q2.value = 'zzzzqqq'; q2.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
await wait(600);
check('a search with no match empties Trending Topics', view().querySelectorAll('table tbody tr').length === 0);
doc.getElementById('mode-basic-btn').click(); await wait(500);
check('switching back to Overview hides Explore', doc.getElementById('explore-view').style.display === 'none' &&
  doc.getElementById('basic-view').style.display !== 'none');
check('zero script errors', errors.length === 0, errors.join(' | '));
console.log(`\nResults: ${ok} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
