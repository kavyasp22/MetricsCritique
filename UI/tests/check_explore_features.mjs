/**
 * Smoke tests for the news-UI-inspired features, rendered in jsdom from the real index.html + metrics.json with a stubbed API:
 *   keyword search (highlight, error message), custom dates, entity cascade, "why trending" cards + methodology,
 *   cross-platform story cards (+ show more), thumbs up/down with reasons, and "Show more" paging in the drill-down.
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const script = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
const base = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));
const errors = [], viralCalls = [], fbPosts = [], drillCalls = [], entCalls = [];
const row = (i) => ({ post_uid: 'p' + i, platform: 'twitter', posted_at: '2026-10-2' + (i % 7) + ' 10:00:00+00', author_name: 'a', text: 'post ' + i, url: '', views: 1000 - i, likes: 5, comments: 1, shares: 2 });
let prefill = {};

const dom = new JSDOM(html, {
  runScripts: 'dangerously', url: 'http://localhost/',
  beforeParse(win) {
    win.fetch = (u, opts) => {
      u = String(u);
      const J = (o, ok = true, status = 200) => Promise.resolve({ ok, status, json: () => Promise.resolve(o) });
      if (u.startsWith('/api/feedback/votes')) return J({ votes: prefill });
      if (u.startsWith('/api/feedback') && opts && opts.method === 'POST') { fbPosts.push(JSON.parse(opts.body)); return J({ ok: true }); }
      if (u.includes('/api/explore/viral')) {
        viralCalls.push(u);
        if (/q=-dam/.test(u)) return J({ error: 'add at least one word to search for (a minus sign only excludes)' }, false, 400);
        return J({ total_posts: 2, terms: ['flood', 'floods', 'flash flood'],
          with_views: [{ post_uid: 'a', platform: 'twitter', posted_at: '2026-10-20 10:00:00+00', text: 'Flash flood hits the floods area, not flooding', url: '', views: 5000, likes: 10, shares: 2, engagement: 12 }],
          without_views: [] });
      }
      if (u.includes('/api/explore/entity_topics')) {
        entCalls.push(u);
        return J({ entity_uid: 'x', coverage: [{ platform: 'twitter', last_day: '2026-07-23', posts: 5 }, { platform: 'mastodon', last_day: '2026-10-26', posts: 9 }], rows: [{ topic_id: 't000', n_cur: 12, n_prev: 14 }, { topic_id: 't007', n_cur: 30, n_prev: 10 }, { topic_id: 't005', n_cur: 2, n_prev: 0 }, { topic_id: 't004', n_cur: 1, n_prev: 1 }] });
      }
      if (u.includes('/api/drill')) {
        drillCalls.push(u);
        const off = Number((u.match(/offset=(\d+)/) || [0, 0])[1]);
        const rows = off === 0 ? Array.from({ length: 60 }, (_, i) => row(i)) : Array.from({ length: 50 }, (_, i) => row(60 + i));
        return J({ view: 'topic', shown: rows.length, total: 110, offset: off, rows });
      }
      return J(u.includes('metrics.json') ? base : {});
    };
    win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
    Object.defineProperty(win.HTMLElement.prototype, 'clientWidth', { get: () => 900 });
    Object.defineProperty(win.HTMLElement.prototype, 'clientHeight', { get: () => 400 });
  },
});
dom.window.addEventListener('error', (e) => errors.push(e.message || String(e)));
const sc = dom.window.document.createElement('script'); sc.textContent = script; dom.window.document.body.appendChild(sc);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
await wait(2500);
const doc = dom.window.document, win = dom.window;
let ok = 0, bad = 0;
const check = (n, c, d = '') => { c ? ok++ : bad++; console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${d ? '  ' + d : ''}`); };
const view = () => doc.querySelector('#explore-view [data-panel="explore_view"]');
const tile = async (k) => { doc.querySelector(`.ex-tile[data-tile=${k}]`).click(); await wait(600); };
const typeIn = async (el, v, ev = 'change') => { el.value = v; el.dispatchEvent(new win.Event(ev)); await wait(500); };

doc.getElementById('mode-explore-btn').click(); await wait(800);

// ---- why cards + methodology (Trending Topics) ----
const why = doc.querySelectorAll('.ex-why-card');
check('Trending Topics shows "why" cards (up to 5)', why.length > 0 && why.length <= 5, String(why.length));
check('each D-Themes card has plain-language bullets with posts, volume rank, and stage (no emergence/alerts)',
  [...why].every((c) => { const t = c.textContent; return /posts between/.test(t) && /Rank \d+ of \d+ security themes .* \(ranked by post volume\)/.test(t) && /Stage /.test(t) && !/(statistically significant|No day with)/.test(t) && !/Emergence score/.test(t); }));

// Switch to Macro clusters to validate Macro Topics why-cards
const grainSel = doc.getElementById('ex-topic-grain');
grainSel.value = 'macro';
grainSel.dispatchEvent(new win.Event('change'));
await wait(600);
const macroWhy = doc.querySelectorAll('.ex-why-card');
check('Macro Topics cards have plain-language bullets with posts, emergence rank, alert days and stage',
  [...macroWhy].every((c) => { const t = c.textContent; return /posts between/.test(t) && /Emergence score .* rank \d+ of \d+/.test(t) && /(statistically significant|No day with)/.test(t) && /Stage /.test(t); }));
grainSel.value = 'theme';
grainSel.dispatchEvent(new win.Event('change'));
await wait(600);

const meth = doc.querySelector('.ex-method');
check('"How this ranking is decided" panel exists', !!meth);
check('methodology shows the live thresholds (25%, 20%, 10%, 6 days, 20 posts)',
  /rising ≥ \+25%/.test(meth.textContent) && /declining ≤ −20%/.test(meth.textContent) && /6\+ days and 20\+ posts/.test(meth.textContent));
check('D-Themes methodology explains ranking by post volume', /Security themes are ranked by total post volume/.test(meth.textContent));

// ---- thumbs on a why card ----
const activeWhy = doc.querySelectorAll('.ex-why-card');
const up = activeWhy[0].querySelector('.fb-up'), down = activeWhy[0].querySelector('.fb-down');
check('thumbs up and down buttons are on the card', !!up && !!down);
up.click(); await wait(300);
check('thumb up posts the vote (topic, id, day, vote=1)', fbPosts.length === 1 && fbPosts[0].item_type === 'topic' && fbPosts[0].vote === 1 && !!fbPosts[0].item_id && !!fbPosts[0].user, JSON.stringify(fbPosts[0] || {}));
check('thumb up shows as selected', up.classList.contains('on'));
down.click(); await wait(200);
const panel = activeWhy[0].querySelector('.fb-panel');
check('thumb down opens the reasons list', !!panel && panel.querySelectorAll('input[type=checkbox]').length === 7);
panel.querySelector('input[value=routine_volume]').checked = true;
panel.querySelector('.fb-other').value = 'looks routine';
[...panel.querySelectorAll('button')].find((b) => b.textContent === 'Send').click(); await wait(300);
const last = fbPosts[fbPosts.length - 1];
check('thumb down posts vote=-1 with the chosen reason and the text', last.vote === -1 && last.reasons.includes('routine_volume') && last.other_text === 'looks routine', JSON.stringify(last));
down.click(); await wait(300);
check('clicking the same thumb again withdraws the vote (vote=0)', fbPosts[fbPosts.length - 1].vote === 0);

// ---- alerts have thumbs too ----
check('Needs attention and recommended alerts carry thumbs', doc.querySelectorAll('#explore-view .attn-vote .fb').length >= 1 && doc.querySelectorAll('#explore-view .alert-row .fb').length >= 1,
  `${doc.querySelectorAll('#explore-view .attn-vote .fb').length} / ${doc.querySelectorAll('#explore-view .alert-row .fb').length}`);

// ---- custom dates ----
await tile('topics');
const from = doc.getElementById('ex-date-from'), to = doc.getElementById('ex-date-to');
from.value = '2026-10-10'; to.value = '2026-10-05'; doc.getElementById('ex-date-apply').click(); await wait(300);
check('an end date before the start date is refused with a message', /after the end date/.test(doc.querySelector('.ex-date-err').textContent));
from.value = '2026-10-10'; to.value = '2026-10-16'; doc.getElementById('ex-date-apply').click(); await wait(700);
check('applying valid custom dates re-renders with that range', doc.getElementById('ex-date-from').value === '2026-10-10' && doc.getElementById('ex-date-to').value === '2026-10-16');
check('the why-cards text uses the new range', /2026-10-10 and 2026-10-16/.test(doc.querySelector('.ex-why-card')?.textContent || ''));

// ---- cross-platform story cards ----
await tile('spread');
check('Spread shows story cards with chips, stats and a View posts button', doc.querySelectorAll('.ex-story').length > 0 &&
  /match: /.test(doc.querySelector('.ex-story').textContent) && /earliest observed/.test(doc.querySelector('.ex-story').textContent) &&
  !!doc.querySelector('.ex-story .df-preset-btn'));
check('story cards carry thumbs (item type story)', !!doc.querySelector('.ex-story .fb[data-fb^="story:"]'));
check('Spread has a Cards / Table switch', [...view().querySelectorAll('.df-preset-btn')].some((b) => b.textContent === 'Table'));
[...doc.querySelectorAll('#explore-view .df-preset-btn')].find((x) => x.textContent === 'Full window').click(); await wait(600);
[...view().querySelectorAll('.df-preset-btn')].find((x) => x.textContent.includes('Similar meaning')).click(); await wait(500);
const n1 = doc.querySelectorAll('.ex-story').length;
const more = view().querySelector('.dr-more');
check('with many stories, "Show more stories" appears and adds 10 more', n1 === 10 && !!more, `${n1} shown`);
if (more) { more.click(); await wait(500); check('after Show more, 20 cards are shown', doc.querySelectorAll('.ex-story').length === 20, String(doc.querySelectorAll('.ex-story').length)); }

// ---- keyword search with highlight + error ----
await tile('viral');
const q = doc.querySelector('.ex-q');
check('search box explains the query syntax', /exact phrase/.test(q.title) && /OR/.test(q.title));
await typeIn(q, 'flood relief OR "flash flood" -dam', 'keydown');   // not Enter: should not search
q.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter' })); await wait(700);
check('the query is sent to the API as typed', viralCalls.some((u) => decodeURIComponent(u).includes('q=flood relief OR "flash flood" -dam')));
const marks = [...view().querySelectorAll('.ex-post-text mark')].map((m) => m.textContent);
check('matched words are highlighted (any word form, phrase)', marks.includes('Flash flood') || (marks.includes('floods') && marks.includes('flooding')), marks.join(' | '));
const q2 = doc.querySelector('.ex-q'); q2.value = '-dam'; q2.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter' })); await wait(700);
check('a bad query shows the server\'s message, not a generic failure', /Search problem: add at least one word/.test(view().textContent), view().textContent.slice(-120));

// ---- entity cascade ----
const tsel = doc.getElementById('ex-ent-type'), ssel = doc.getElementById('ex-ent-sub');
check('entity cascade: type select, subtype select disabled until a type is chosen', !!tsel && !!ssel && ssel.disabled === true);
await typeIn(tsel, 'LOCATION');
const ssel2 = doc.getElementById('ex-ent-sub');
check('choosing Places enables the subtype list with the country subtype', ssel2.disabled === false && [...ssel2.options].some((o) => o.value === 'country'), [...ssel2.options].slice(0, 4).map((o) => o.value).join(','));
await typeIn(doc.getElementById('ex-ent-sub'), 'country');
const opts = [...doc.querySelectorAll('#ex-ent-list option')].map((o) => o.value);
check('the entity list now holds countries only (e.g. China / India)', opts.length > 5 && opts.includes('China') || opts.includes('India'), opts.slice(0, 5).join(','));
const ent = doc.getElementById('ex-ent');
viralCalls.length = 0;
await typeIn(ent, opts[0]);
check('picking an entity sends its id to the API', viralCalls.some((u) => /entity_uid=location%3A/.test(u)), viralCalls[0] || '');

// ---- entity filter on Trending Topics ----
entCalls.length = 0;
await tile('topics');
{ const q0 = doc.querySelector('.ex-q'); q0.value = ''; q0.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter' })); await wait(700); }   // the viral text query above would filter topic names here
check('Trending Topics shows the entity picker (type, subtype, name)', !!doc.getElementById('ex-ent') && !!doc.getElementById('ex-ent-type') && !!doc.getElementById('ex-ent-sub'));
check('with an entity chosen the page asks the entity-topics API with the entity id and the previous period',
  entCalls.length > 0 && /entity_uid=location%3A/.test(entCalls[0]) && /prev_from=/.test(entCalls[0]) && /prev_to=/.test(entCalls[0]), entCalls[0] || '');
const eth = [...view().querySelectorAll('.tv thead th .th-label')].map((x) => x.textContent);
check('entity table names the entity count and labels the topic-level columns "whole topic"',
  eth.some((x) => /^Posts naming /.test(x)) && eth.includes('Emergence (whole topic)') && eth.includes('Alert days (whole topic)') && eth.includes('Stage (whole topic)'), eth.join(' | '));
const erows = [...view().querySelectorAll('.tv table tbody tr')];
const firstCells = erows.map((tr) => tr.children[1].textContent.replace(/[\s,]/g, ''));
check('rows are sorted by posts naming the entity (highest first)', erows.length > 0 && Number(firstCells[0]) >= Number(firstCells[firstCells.length - 1]), firstCells.join(','));
check('topics t004 and t005 are merged into one row (1 + 2 posts = 3)', firstCells.filter((c) => c === '3').length === 1 && erows.length === 3, firstCells.join(','));
check('a platform whose entities stop early is named in a warning (twitter, 2026-07-23); a complete one is not', /twitter \(entities end 2026-07-23\)/.test(view().textContent) && !/mastodon \(entities end/.test(view().textContent));
check('the explanation names the entity and says emergence/stage are the whole topic', /whole topic/.test(view().textContent) && /YouTube comments carry no entity mentions/.test(view().textContent));
drillCalls.length = 0;
erows[0].click(); await wait(700);
check('clicking a row opens the posts of that entity in that topic (drill gets topic_id and entity_uid)',
  drillCalls.some((u) => /topic_id=t\d+/.test(u) && /entity_uid=location%3A/.test(u) && /sort=engagement/.test(u)), drillCalls[0] || '');
doc.getElementById('drill-close')?.click(); await wait(200);
const rs0 = [...doc.querySelectorAll('#explore-view .df-preset-btn')].find((x) => x.textContent === 'Reset');
rs0.click(); await wait(600);
check('after Reset the normal Trending Topics table is back (no entity columns)',
  ![...view().querySelectorAll('.tv thead th .th-label')].some((x) => /whole topic/.test(x.textContent)));
const entBack = [...opts][0];

// ---- drill paging ----
await tile('viral');
check('a post-text search does not hide the breakout groups table', view().querySelectorAll('.tv table tbody tr').length > 0, String(view().querySelectorAll('.tv table tbody tr').length));
drillCalls.length = 0;
const rowEl = [...view().querySelectorAll('.tv table tbody tr')][0];
rowEl.click(); await wait(700);
check('drill-down shows the first 60 posts and a Show more button with the remaining count',
  doc.querySelectorAll('#drill-body .dr-row').length === 60 && /Show more \(50 remaining\)/.test(doc.querySelector('#drill-body .dr-more')?.textContent || ''));
doc.querySelector('#drill-body .dr-more').click(); await wait(600);
check('Show more appends the next page (offset=60) and the button goes away', doc.querySelectorAll('#drill-body .dr-row').length === 110 && !doc.querySelector('#drill-body .dr-more') && drillCalls.some((u) => /offset=60/.test(u)),
  `${doc.querySelectorAll('#drill-body .dr-row').length} rows`);
check('the subtitle count follows the paging', /Showing 110 of 110/.test(doc.getElementById('drill-sub').textContent), doc.getElementById('drill-sub').textContent);

check('zero script errors', errors.length === 0, errors.slice(0, 3).join(' | '));
console.log(`\nResults: ${ok} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
