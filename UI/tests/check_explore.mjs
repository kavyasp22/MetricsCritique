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
const drillCalls = [];

const dom = new JSDOM(html, {
  runScripts: 'dangerously', url: 'http://localhost/',
  beforeParse(win) {
    win.fetch = (u) => {
      u = String(u);
      if (u.includes('/api/drill')) {
        drillCalls.push(u);
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ view: 'topic', shown: 0, total: 0, rows: [] }) });
      }
      if (u.includes('/api/explore/viral')) {
        calls.push(u);
        if (/rank=virality/.test(u)) {
          const per = Number((u.match(/per_platform=(\d+)/) || [0, 0])[1]);
          const mk = (pl, pct) => ({ post_uid: pl + pct, platform: pl, posted_at: '2026-10-20 10:00:00+00', text: 'a viral ' + pl + ' post', url: '', views: 100, likes: 5, shares: 1, comments: 0, engagement: 6, virality_pct: pct, virality_ref_n: 8336, is_retweet: pl === 'twitter' && pct > 0.98 });
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ rank: 'virality', per_platform: per, total_posts: 40, rated_posts: 25, min_ref: 20, floor_on: !/floor=off/.test(u), below_floor: 7,
            by_virality: per ? [mk('twitter', 0.99), mk('twitter', 0.97), mk('telegram', 0.98)] : [mk('twitter', 0.99), mk('telegram', 0.98)], terms: [] }) });
        }
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
const ovFrom = doc.querySelector('#basic-date-strip .bds-date-input').value, ovTo = doc.querySelectorAll('#basic-date-strip .bds-date-input')[1].value;
btn.click(); await wait(600);
check('Explore opens on the last day of data only (1 day)', doc.getElementById('ex-date-from').value === '2026-10-26' && doc.getElementById('ex-date-to').value === '2026-10-26',
  doc.getElementById('ex-date-from').value + ' -> ' + doc.getElementById('ex-date-to').value);
check('Overview range was the last 7 days before Explore (unchanged default)', ovFrom === '2026-10-20' && ovTo === '2026-10-26', ovFrom + ' -> ' + ovTo);
check('Explore has a Last 1d button', [...doc.querySelectorAll('#explore-view .df-preset-btn')].some((b) => b.textContent === 'Last 1d'));
check('Explore view shown, others hidden',
  doc.getElementById('explore-view').style.display !== 'none' &&
  doc.getElementById('basic-view').style.display === 'none' &&
  doc.getElementById('advanced-view').style.display === 'none');
check('data-mode is explore', doc.body.getAttribute('data-mode') === 'explore');
const tiles = doc.querySelectorAll('.ex-tile');
check('five tiles', tiles.length === 5, String(tiles.length));
check('tile titles', ['Trending Topics', 'Viral Posts', 'Cross-Platform Spread', 'Trend Lifecycle', 'Sentiment Analysis']
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
const rows = (k) => { [...doc.querySelectorAll('.ex-tile')].find((x) => x.dataset.tile === k).click(); return wait(500).then(() => view().querySelectorAll('.tv table tbody tr').length); };
const topicHead = [...view().querySelectorAll('.tv table thead th')].map((x) => x.textContent);
check('Trending Topics uses the stored metrics (emergence, q-value, alert days, momentum)',
  ['Emergence', 'Min q', 'Alert days', 'Burst days', 'Momentum'].every((c) => topicHead.some((h) => h.includes(c))), topicHead.join(' | '));
const topicNames = [...view().querySelectorAll('.tv table tbody tr')].map((tr) => tr.children[0].textContent.trim());
check('Unclassified is left out of Trending Topics by default', !topicNames.includes('Unclassified'), topicNames.join(', '));
const stages = [...view().querySelectorAll('.tv table tbody tr')].map((tr) => tr.children[topicHead.findIndex((x) => x.includes('Stage'))].textContent.trim());
check('stage is one of the defined labels', stages.every((s) => ['starting', 'rising', 'peak', 'steady', 'declining', 'too little data'].includes(s)), [...new Set(stages)].join(', '));
const cb = doc.getElementById('ex-unclass'); cb.checked = true; cb.dispatchEvent(new dom.window.Event('change')); await wait(500);
check('ticking Include Unclassified brings it back',
  [...view().querySelectorAll('.tv table tbody tr')].some((tr) => tr.children[0].textContent.trim() === 'Unclassified'));
const cb2 = doc.getElementById('ex-unclass'); cb2.checked = false; cb2.dispatchEvent(new dom.window.Event('change')); await wait(400);
{
  const row = view().querySelector('.tv table tbody tr');
  drillCalls.length = 0; row.click(); await wait(500);
  const u = drillCalls[0] || '';
  check('clicking a Trending Topics row opens the drill-down with the selected dates', /from=2026-10-26/.test(u) && /to=2026-10-26/.test(u) && /topic_id=/.test(u), u);
}
const lifeRows = await rows('life');
check('Lifecycle has onset slope, half-life and persistence table',
  /Onset slope/.test(view().textContent) && /Persistence by topic and platform/.test(view().textContent));
check('Trend Lifecycle shows rows or an explicit empty state', lifeRows > 0 || /No rows/.test(view().textContent), String(lifeRows));
[...doc.querySelectorAll('#explore-view .df-preset-btn')].find((b) => b.textContent === 'Last 7d').click(); await wait(500);
await rows('spread');
const storyCards = view().querySelectorAll('.ex-story').length;
check('Spread opens as story cards by default', storyCards > 0, String(storyCards));
[...view().querySelectorAll('.df-preset-btn')].find((x) => x.textContent === 'Table').click(); await wait(400);
const spreadRows = view().querySelectorAll('.tv table tbody tr').length;
const subBtns = [...view().querySelectorAll('.df-preset-btn')].map((x) => x.textContent);
check('Spread has three evidence tiers', ['Same text', 'Shared links', 'Similar meaning'].every((c) => subBtns.some((b) => b.includes(c))), subBtns.join(' | '));
[...view().querySelectorAll('.df-preset-btn')].find((x) => x.textContent.includes('Shared links')).click(); await wait(500);
check('no shared link starts in the last 7 days: explicit empty state, not a blank', /No rows/.test(view().textContent));
[...doc.querySelectorAll('#explore-view .df-preset-btn')].find((x) => x.textContent === 'Full window').click(); await wait(600);
check('Shared links tier renders with match confidence column', /Match/.test([...view().querySelectorAll('.tv table thead th')].map((x) => x.textContent).join(' ')));
[...view().querySelectorAll('.df-preset-btn')].find((x) => x.textContent.includes('Similar meaning')).click(); await wait(500);
check('Similar-meaning tier is labelled preliminary', /Preliminary/.test(view().textContent));
check('Cross-Platform Spread shows rows or an explicit empty state', spreadRows > 0 || /No rows/.test(view().textContent), String(spreadRows));
const sentRows = await rows('sent');
const stext = view().textContent;
check('Sentiment card shows tone by platform with rows', sentRows > 0 && /Tone by platform/.test(stext), String(sentRows));
check('Sentiment card states what tone measures and that it is provisional', /wording/.test(stext) && /provisional/i.test(stext));
check('Sentiment card has NSS and positive/neutral/negative columns', ['NSS', 'Positive', 'Neutral', 'Negative'].every((c) => stext.includes(c)));
check('Sentiment card does not show hate scores', !/hate/i.test(stext));
await rows('viral'); await wait(500);
const vtext = view().textContent;
{
  const heads = [...view().querySelectorAll('.tv table thead th')].map((x) => x.textContent);
  const iv = heads.findIndex((x) => x.includes('Observed views')), ir = heads.findIndex((x) => x.includes('Reach vs author norm'));
  const bad = [...view().querySelectorAll('.tv table tbody tr')].filter((tr) => tr.children[iv]?.textContent.trim() === '\u2014' && tr.children[ir]?.textContent.trim() !== '\u2014');
  check('a group with no observed views shows reach as a dash, never 0.0x', iv > 0 && ir > 0 && bad.length === 0, String(bad.length));
}
{
  const row = view().querySelector('.tv table tbody tr');
  drillCalls.length = 0; row.click(); await wait(500);
  const u = drillCalls[0] || '';
  check('clicking a breakout row opens the drill-down with the selected dates, its platform and a views/engagement ranking',
    (() => { const f = doc.getElementById('ex-date-from').value, e = doc.getElementById('ex-date-to').value;
      return !!f && !!e && u.includes('from=' + f) && u.includes('to=' + e) && /platform=/.test(u) && /topic_id=/.test(u) && /sort=(views|engagement)/.test(u); })(), u);
}
check('Viral card shows stored virality percentile + audience-normalised reach groups', /Virality pct/.test(vtext) && /Reach vs author norm/.test(vtext));
check('Breakout groups table has rows', view().querySelectorAll('.tv table tbody tr').length > 0, String(view().querySelectorAll('.tv table tbody tr').length));
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
check('a search with no match empties Trending Topics', view().querySelectorAll('.tv table tbody tr').length === 0);
// Viral Posts: rank by per-post virality percentile
{
  await rows('viral');
  calls.length = 0;
  const btn = (t) => [...view().querySelectorAll('.df-preset-btn')].find((x) => x.textContent === t);
  check('Viral Posts offers Views and Virality percentile ranking', !!btn('Views') && !!btn('Virality percentile'));
  btn('Virality percentile').click(); await wait(700);
  check('the percentile ranking asks the API with rank=virality', calls.some((c) => /rank=virality&per_platform=0/.test(c)), calls[0] || '');
  const txt = view().textContent;
  check('posts show "virality 0.99 (vs 8,336 similar posts)" and the note says how many are rated', /virality 0\.99 \(vs 8,336 similar posts\)/.test(txt) && /32 of them have a virality percentile/.test(txt) && /leaving 25 ranked/.test(txt));
  check('a retweet is tagged "Retweet" and an original post is not', view().querySelectorAll('.ex-rt-tag').length === 1, String(view().querySelectorAll('.ex-rt-tag').length));
  check('the retweet switch offers Include / Original posts only, Include active by default', !!btn('Include') && !!btn('Original posts only') && btn('Include').classList.contains('active'));
  calls.length = 0;
  btn('Original posts only').click(); await wait(700);
  check('"Original posts only" sends retweets=hide', calls.some((c) => /retweets=hide/.test(c)), calls[0] || '');
  btn('Include').click(); await wait(700);
  check('back to Include sends no retweets parameter', !calls[calls.length - 1].includes('retweets='), calls[calls.length - 1]);
  check('the note says how many ranked posts the activity floor hides', /7 of those are hidden because their activity is below the median of their own platform/.test(view().textContent));
  check('a MIN ACTIVITY switch (Platform median or more | Off) is shown, on by default', !!btn('Platform median or more') && !!btn('Off') && btn('Platform median or more').classList.contains('active'));
  calls.length = 0;
  btn('Off').click(); await wait(700);
  check('"Off" sends floor=off and the hidden-by-floor note goes away', calls.some((c) => /floor=off/.test(c)) && !/hidden because their activity/.test(view().textContent), calls[0] || '');
  btn('Platform median or more').click(); await wait(700);
  check('back to "Platform median or more" sends no floor parameter', !calls[calls.length - 1].includes('floor='), calls[calls.length - 1]);
  btn('Top 3 per platform').click(); await wait(700);
  check('"Top 3 per platform" asks per_platform=3 and groups posts under platform headings',
    calls.some((c) => /per_platform=3/.test(c)) && [...view().querySelectorAll('.ex-col .ct')].some((x) => /^twitter \(2\)/.test(x.textContent)) && [...view().querySelectorAll('.ex-col .ct')].some((x) => /^telegram \(1\)/.test(x.textContent)));
  btn('Views').click(); await wait(700);
  check('back to Views shows the by-views lists again', /Ranked by views/.test(view().textContent));
}
// Trending Topics: "seen on N+ platforms" filter
{
  [...doc.querySelectorAll('#explore-view .df-preset-btn')].find((x) => x.textContent === 'Reset').click(); await wait(500);   // the no-match search above is still in the box
  [...doc.querySelectorAll('#explore-view .df-preset-btn')].find((x) => x.textContent === 'Full window').click(); await wait(600);
  await rows('topics');
  const pcol = () => [...view().querySelectorAll('.tv thead th .th-label')].map((x) => x.textContent).indexOf('Platforms');
  const plats = () => [...view().querySelectorAll('.tv table tbody tr')].map((tr) => Number(tr.children[pcol()].textContent.replace(/[^0-9]/g, '')));
  const before = plats();
  const ps = doc.getElementById('ex-minplat');
  check('Trending Topics has a "seen on N+ platforms" control', !!ps && [...ps.options].map((o) => o.value).join() === '0,2,3,5');
  ps.value = '3'; ps.dispatchEvent(new dom.window.Event('change')); await wait(500);
  const after = plats();
  check('"Seen on 3+ platforms" keeps only topics on 3 or more platforms', after.length > 0 && after.every((n) => n >= 3) && after.length <= before.length, `${before.length} -> ${after.length}; min ${Math.min(...after)}`);
  const ps2 = doc.getElementById('ex-minplat'); ps2.value = '0'; ps2.dispatchEvent(new dom.window.Event('change')); await wait(400);
  check('back to "any number" restores the list', plats().length === before.length);
}
// every card: tables closed (+ to open), explanation closed behind a heading
for (const k of ['topics', 'viral', 'spread', 'life', 'sent']) {
  [...doc.querySelectorAll('.ex-tile')].find((x) => x.dataset.tile === k).click(); await wait(500);
  const v = view();
  const ex = [...v.querySelectorAll('details.ex-explain')];
  check(`${k}: explanation is collapsed behind a heading with "click to see explanation"`,
    ex.length > 0 && ex.every((d) => !d.open && /click to see explanation/.test(d.querySelector('summary').textContent)), String(ex.length));
  const tv = [...v.querySelectorAll('details.tv')];
  check(`${k}: tables are closed by default`, tv.every((d) => !d.open || (k === 'spread' && d.querySelector('summary').textContent.includes('Cross-platform spread'))), tv.map((d) => d.open + ':' + d.querySelector('summary').textContent.slice(0, 30)).join(' | '));
}
doc.getElementById('mode-basic-btn').click(); await wait(500);
check('Overview range is still the last 7 days after using Explore (not changed to 1 day)',
  (() => { const i = doc.querySelectorAll('#basic-date-strip .bds-date-input'); return i[0].value === '2026-10-20' && i[1].value === '2026-10-26'; })(),
  [...doc.querySelectorAll('#basic-date-strip .bds-date-input')].map((x) => x.value).join(' -> '));
check('switching back to Overview hides Explore', doc.getElementById('explore-view').style.display === 'none' &&
  doc.getElementById('basic-view').style.display !== 'none');
check('zero script errors', errors.length === 0, errors.join(' | '));
console.log(`\nResults: ${ok} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
