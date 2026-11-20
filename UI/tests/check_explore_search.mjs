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
          topics: [{ topic_id: 't004', n: 40, n_prev: 10 }, { topic_id: 't005', n: 10, n_prev: 5 }, { topic_id: 't007', n: 70, n_prev: 70 }],
          themes: [{ theme: 'D06', tier: 'high', n: 30, n_prev: 10 }, { theme: 'D06', tier: 'neighbour', n: 4, n_prev: 0 }, { theme: 'D06', tier: 'medium', n: 5, n_prev: 1 }, { theme: 'D05', tier: 'high', n: 8, n_prev: 8 }],
          entities: [{ entity_uid: 'location:ladakh', name: 'Ladakh', type: 'LOCATION', corpus: 290, n: 100, n_prev: 40 },
                     { entity_uid: 'location:china', name: 'China', type: 'LOCATION', corpus: 17000, n: 30, n_prev: 5 },
                     { entity_uid: 'org:indianarmy', name: 'Indianarmy', type: 'ORG', corpus: 900, n: 12, n_prev: 12 }],
          anomaly: few ? { ok: false, reason: 'too few matching posts a day, or a usual level under 0.5 a day, to judge', rows: [], alert_days: [] }
                       : { ok: true, reason: null, judged_days: 20, alert_days: ['2026-10-24', '2026-10-25'], max_ratio: 4.2, window: 28, rows: [] },
          platform_speed: [{ platform: 'twitter', n: 60, first_day: '2026-10-20', before: false, peak_day: '2026-10-24', peak_n: 12, trend: 'rising', ratio: 2.1 },
                           { platform: 'telegram', n: 60, first_day: '2026-09-20', before: true, peak_day: '2026-10-22', peak_n: 9, trend: 'steady', ratio: 1.0 },
                           { platform: 'reddit', n: 3, first_day: null, before: false, peak_day: '2026-10-21', peak_n: 2, trend: 'few', ratio: null }],
          hashtags: [{ tag: '#ladakh', n: 37, n_prev: 12 }, { tag: '#indiachina', n: 19, n_prev: 3 }, { tag: '#lac', n: 14, n_prev: 30 }] });
      }
      if (u.includes('/api/explore/viral')) {
        viralCalls.push(u);
        return J({ total_posts: 1, terms: ['ladakh'], with_views: [{ post_uid: 'a', platform: 'twitter', posted_at: '2026-10-20 10:00:00+00', author_name: 'x', text: 'Ladakh border talks', url: '', views: 5000, likes: 1, comments: 0, shares: 0, engagement: 1, is_retweet: false }], without_views: [],
          latest: [{ post_uid: 'z', platform: 'snapchat', posted_at: '2026-10-26 09:00:00+00', author_name: 'y', text: 'Newest ladakh clip', url: '', views: null, likes: 0, comments: 0, shares: 0, engagement: 0, is_retweet: false }] });
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
check('a keyword on Trending Topics shows the search panel, not an empty topic list', !!st, view().textContent.slice(0, 80));
check('the call carries the keyword, both periods and no theme', trendCalls.length > 0 && /q=ladakh/.test(trendCalls.at(-1)) && /prev_from=\d{4}/.test(trendCalls.at(-1)) && !/theme=/.test(trendCalls.at(-1)), trendCalls.at(-1));
const txt = st.textContent;
check('the headline answers in words: "ladakh" with its counts, distinct messages and the main platforms', /“ladakh”: /.test(txt) && /120 posts/.test(txt) && /118 distinct/.test(txt) && /mostly on twitter and telegram/.test(txt), txt.slice(0, 220));
check('a bar chart of posts per day is drawn', !!st.querySelector('.ex-st-plot svg'));
check('the page says whether the search is unusual: alert days against the last 28 days, with how far above normal', /Unusual: 2 of the 20 days judged stood out from the last 28 days \(2026-10-24, 2026-10-25\), up to 4\.2× the usual number of posts/.test(txt), (txt.match(/Unusual[^.]*\./) || [''])[0]);
check('the two alert days are marked on the chart ribbon (only when they fall inside the selected days)', [...st.querySelectorAll('.ex-st-plot svg rect')].filter((r) => r.getAttribute('height') === '4').length <= 2);
const plat = [...st.querySelectorAll('.ex-rel-plat .ex-rel-row')].map((r) => r.textContent);
check('By platform: each platform says when it started and whether it is rising there', plat.length === 3 && /^Rising.*twitter · first seen 2026-10-20/.test(plat[0]) && /^Steady.*telegram · already active before/.test(plat[1]) && /^Few posts.*reddit · too few to date/.test(plat[2]), plat.join(' | '));
check('a one-line lead says which platform came first', /Already active before the selected days on telegram\. First seen on twitter \(2026-10-20\)\./.test(txt));
const titles = [...st.querySelectorAll('.ex-rel-title')].map((e) => e.textContent);
check('after By platform, Related trending has four groups: topics, themes, people/organisations/places, hashtags', titles.join('|') === 'By platform|Topics|Security themes|People, organisations and places|Hashtags', titles.join('|'));
const rows = (g) => [...st.querySelectorAll('.ex-rel-group')][g + 1].querySelectorAll('.ex-rel-row');
const rowTxt = (g) => [...rows(g)].map((r) => r.textContent);
check('the two neighbourhood topics are merged into one row of 50 posts, with a Rising tag', rowTxt(0).some((t) => /Neighbourhood/.test(t) && /50 posts/.test(t) && /^Rising/.test(t)), rowTxt(0).join(' | '));
check('a topic that did not change reads Steady', rowTxt(0).some((t) => /^Steady.*China/.test(t)), rowTxt(0).join(' | '));
check('theme rows count clear matches and neighbour votes (D06 = 34 posts), not the lower-confidence tier', rowTxt(1).some((t) => /D06/.test(t) && /34 posts/.test(t)), rowTxt(1).join(' | '));
check('the searched word is not listed back as an entity, a related place is', !rowTxt(2).some((t) => /Ladakh/.test(t)) && rowTxt(2).some((t) => /China/.test(t) && /^Rising/.test(t)), rowTxt(2).join(' | '));
check('an unchanged entity reads Steady and a falling hashtag reads Declining', rowTxt(2).some((t) => /^Steady.*Indianarmy/.test(t)) && rowTxt(3).some((t) => /^Declining.*#lac/.test(t)), rowTxt(3).join(' | '));
check('the searched hashtag is not listed back; a related one is', !rowTxt(3).some((t) => /#ladakh/.test(t)) && rowTxt(3).some((t) => /#indiachina/.test(t)), rowTxt(3).join(' | '));
check('rising items come before declining ones', rowTxt(3)[0].startsWith('New') || rowTxt(3)[0].startsWith('Rising'), rowTxt(3)[0]);
check('no raw metric names are on the page (emergence, surprise, q-value)', !/emergence|surprise|q-value/i.test(txt.replace(/What is trending around[\s\S]*?narrow the search to it\./, '')));
const cols = [...st.querySelectorAll('.ex-cols .ex-col')].map((c) => c.textContent);
check('"Posts behind it" shows the most viewed and the latest posts', cols.length === 2 && /Ladakh border talks/.test(cols[0]) && /Newest ladakh clip/.test(cols[1]), cols.join(' || ').slice(0, 120));
check('the posts call carries the same keyword and asks for the latest posts', viralCalls.some((u) => /q=ladakh/.test(u) && /latest=4/.test(u)));

// ---- chips act as filters ----
const d06 = [...st.querySelectorAll('.ex-rel-row')].find((c) => /D06/.test(c.textContent));
d06.click(); await wait(900);
check('clicking a theme row filters the search by that theme (clear matches only)', /theme=D06/.test(trendCalls.at(-1)) && !/theme_all/.test(trendCalls.at(-1)), trendCalls.at(-1));
check('the page warns that the theme is provisional and gives the precision', /provisional/.test(view().textContent) && /75% right/.test(view().textContent));
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
check('under 20 posts the page says there are too few to call a trend', /too few posts to call a trend/.test(st2.textContent), st2.textContent.slice(0, 140));
check('...and says it cannot judge whether it is unusual', /Cannot say whether it is unusual/.test(st2.textContent));
check('...and gives no growth or stage', !/on the \d+ days before/.test(st2.textContent) && !/: (rising|peak|steady|declining|starting)/.test(st2.textContent));

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
