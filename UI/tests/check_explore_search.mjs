/**
 * Search on Trending Topics (keyword / theme) and the Theme filter, rendered in jsdom from the real index.html + metrics.json with a stubbed API:
 *   theme dropdown labels come from the taxonomy (no hardcoded names), a keyword search on Trending Topics shows the trend panel
 *   (counts, growth gated by minimum support, stage, chips for topics and themes, top posts), "too few posts" instead of a trend,
 *   a theme is sent to the API with the clear-matches default, the lower-confidence switch, and the Viral Posts tab carries the filter.
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const script = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
const base = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));
const errors = [], trendCalls = [], viralCalls = [];
let trendMode = 'ok';
const days = (from, n) => Array.from({ length: n }, (_, i) => new Date(Date.parse(from) + i * 86400000).toISOString().slice(0, 10));

const dom = new JSDOM(html, {
  runScripts: 'dangerously', url: 'http://localhost/',
  beforeParse(win) {
    win.fetch = (u) => {
      u = String(u);
      const J = (o, ok = true, status = 200) => Promise.resolve({ ok, status, json: () => Promise.resolve(o) });
      if (u.startsWith('/api/feedback')) return J({ votes: {} });
      if (u.includes('/api/explore/search_trend')) {
        trendCalls.push(u);
        const few = trendMode === 'few';
        return J({ q: 'ladakh', theme: '', entity_uid: '', terms: ['ladakh'], min_posts: 20, n_cur: few ? 7 : 120, n_prev: few ? 3 : 60, distinct_cur: few ? 7 : 118,
          unassigned_cur: 70, daily: days('2026-09-01', 80).map((d) => ({ day: d, n: few ? 0 : 5, distinct: few ? 0 : 5, total: 3000 })),
          platforms: [{ platform: 'twitter', n_cur: 60, n_prev: 10 }, { platform: 'telegram', n_cur: 60, n_prev: 50 }],
          topics: [{ topic_id: 't004', n: 40 }, { topic_id: 't005', n: 10 }, { topic_id: 't007', n: 70 }],
          themes: [{ theme: 'D06', tier: 'high', n: 30 }, { theme: 'D06', tier: 'medium', n: 5 }, { theme: 'D05', tier: 'high', n: 8 }] });
      }
      if (u.includes('/api/explore/viral')) {
        viralCalls.push(u);
        return J({ total_posts: 1, terms: ['ladakh'], with_views: [{ post_uid: 'a', platform: 'twitter', posted_at: '2026-10-20 10:00:00+00', author_name: 'x', text: 'Ladakh border talks', url: '', views: 5000, likes: 1, comments: 0, shares: 0, engagement: 1, is_retweet: false }], without_views: [] });
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
const search = async (v) => { const q = doc.querySelector('.ex-q'); q.value = v; q.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter' })); await wait(900); };

doc.getElementById('mode-explore-btn').click(); await wait(800);
await tile('topics');

// ---- theme dropdown ----
const th = doc.getElementById('ex-theme');
const names = th ? [...th.options].map((o) => o.textContent) : [];
check('Trending Topics has a security theme dropdown with 9 themes plus "any"', !!th && th.options.length === 10, String(names.length));
check('theme names come from the taxonomy (D04 reads "Maritime Security & Operations")', names.some((n) => /^D04 .* Maritime Security & Operations$/.test(n)), names[4]);
check('the dropdown says the themes are automatic and provisional', /provisional/.test(th.title));
check('the lower-confidence switch is hidden until a theme is chosen', !doc.getElementById('ex-theme-all'));

// ---- keyword search on Trending Topics ----
await search('ladakh');
const st = doc.querySelector('.ex-search-trend');
check('a keyword on Trending Topics shows the trend panel, not an empty topic list', !!st, view().textContent.slice(0, 80));
check('the call carries the keyword, both periods and no theme', trendCalls.length > 0 && /q=ladakh/.test(trendCalls.at(-1)) && /prev_from=\d{4}/.test(trendCalls.at(-1)) && !/theme=/.test(trendCalls.at(-1)), trendCalls.at(-1));
const txt = st.textContent;
check('counts are shown: posts in range, distinct messages, previous period', /120 posts/.test(txt) && /118 distinct/.test(txt) && /60 in the/.test(txt), txt.slice(0, 160));
check('growth and stage are shown with enough posts', /Growth in posts \+100%/.test(txt) && /stage: /.test(txt), (txt.match(/Growth[^.]*\./) || [''])[0]);
check('a bar chart of posts per day is drawn', !!st.querySelector('.ex-st-plot svg'));
check('platforms are listed with the change against the previous period', /twitter · 60 \(\+500% vs before\)/.test(txt) && /telegram · 60/.test(txt));
const chips = [...st.querySelectorAll('.ex-chip')].map((c) => c.textContent);
check('the two neighbourhood topics are merged into one chip (50)', chips.some((c) => /Neighbourhood/.test(c) && /· 50$/.test(c)), chips.join(' | '));
check('theme chips list clear matches only by default (D06 = 30)', chips.some((c) => /^D06 · .* · 30$/.test(c)), chips.join(' | '));
check('posts without a theme are counted and explained', /70 of these posts have no theme/.test(txt));
check('top posts for the search are listed', st.querySelectorAll('.ex-post, .ex-post-card, .ex-row').length > 0 && /Ladakh border talks/.test(txt));
check('the posts call carries the same keyword', viralCalls.some((u) => /q=ladakh/.test(u)));

// ---- chips act as filters ----
const d06 = [...st.querySelectorAll('.ex-chip')].find((c) => /^D06/.test(c.textContent));
d06.click(); await wait(900);
check('clicking a theme chip filters the search by that theme (clear matches only)', /theme=D06/.test(trendCalls.at(-1)) && !/theme_all/.test(trendCalls.at(-1)), trendCalls.at(-1));
check('the page warns that the theme is provisional', /provisional/.test(view().textContent) && /74% right/.test(view().textContent));
const all = doc.getElementById('ex-theme-all');
check('the lower-confidence switch appears once a theme is chosen', !!all);
all.checked = true; all.dispatchEvent(new win.Event('change')); await wait(900);
check('switching it on sends theme_all=1', /theme_all=1/.test(trendCalls.at(-1)), trendCalls.at(-1));

// ---- Viral Posts carries the theme ----
await tile('viral');
check('Viral Posts sends the theme and the switch', viralCalls.some((u) => /theme=D06/.test(u) && /theme_all=1/.test(u)), viralCalls.at(-1));
check('Viral Posts shows the provisional-theme note', /Theme D06/.test(view().textContent) && /provisional/.test(view().textContent));

// ---- too few posts: no trend ----
trendMode = 'few';
doc.querySelector('.df-preset-btn[title], .ex-ctl button')?.blur();
const rs = [...doc.querySelectorAll('#explore-view .df-preset-btn')].find((b) => b.textContent === 'Reset'); rs.click(); await wait(500);
check('Reset clears the theme', doc.getElementById('ex-theme').value === '');
await tile('topics');
await search('rareword');
const st2 = doc.querySelector('.ex-search-trend');
check('under 20 posts the page says there are too few to call a trend', /Too few posts \(7, need 20\)/.test(st2.textContent), st2.textContent.slice(0, 140));
check('...and shows no growth or stage', !/Growth in posts/.test(st2.textContent) && !/stage: /.test(st2.textContent));

// ---- Show all ----
await tile('viral');
const topSel = [...doc.querySelectorAll('#explore-view select.ex-top')].find((x) => [...x.options].some((o) => /^Show/.test(o.textContent)));
check('the Show list has 10, 25, 50 and Show all', !!topSel && [...topSel.options].map((o) => o.textContent).join(',') === 'Show 10,Show 25,Show 50,Show all', topSel && [...topSel.options].map((o) => o.textContent).join(','));
topSel.value = '100000'; topSel.dispatchEvent(new win.Event('change')); await wait(900);
check('Show all asks the API for at most 300 posts per list (server limit)', /limit=300(&|$)/.test(viralCalls.at(-1)), viralCalls.at(-1));
check('Show all says posts are cut at 300 per list', /cut at 300 per list/.test(view().textContent));
await tile('topics');
check('Show all stays selected when changing tab', [...doc.querySelectorAll('#explore-view select.ex-top')].some((x) => x.value === '100000'));

check('zero script errors', errors.length === 0, errors.slice(0, 3).join(' | '));
console.log(`\nResults: ${ok} passed, ${bad} failed`);
process.exit(bad ? 1 : 0);
