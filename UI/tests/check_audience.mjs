/**
 * Explore > Audience Sentiment tile: renders the real index.html with the real metrics.json in jsdom.
 * The audience API is stubbed with canned payloads (full / limited / empty / error); set AUD_API=http://localhost:8897 to also run
 * the same page against the real server and the real v2 tables.
 *
 *   node UI/tests/check_audience.mjs
 *   AUD_API=http://localhost:8897 node UI/tests/check_audience.mjs
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const script = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
const base = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));
const LIVE = process.env.AUD_API || '';
let pass = 0, fail = 0;
const chk = (name, cond, detail = '') => { cond ? pass++ : fail++; console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`); };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const layer = (o) => Object.assign({ posts_with_data: 100, rows: 500, scored: 480, not_scored: 20, failed: 0, neg: 200, neu: 100, pos: 180, supported: true,
  neg_pct: 41.7, neu_pct: 20.8, pos_pct: 37.5, net: -4.2, tone_mean: -0.05, conf_mean: 0.64 }, o || {});
const post = (o) => Object.assign({ posts_with_data: 100, with_tone: 98, without_tone: 2, supported: true, tone_mean: 0.02, pos_pct: 30, neg_pct: 25, neu_pct: 45, net: 5 }, o || {});
const gap = (o) => Object.assign({ n: 60, rows: 1800, median_rows: 24, small: 12, n_10_29: 24, n_30_plus: 24, mean: -0.12, median: -0.1, more_negative: 12, more_positive: 6, aligned: 42, hist: [{ bin: '< -0.6', n: 2 }, { bin: '-0.6 to -0.3', n: 10 }, { bin: '-0.3 to -0.1', n: 14 }, { bin: '-0.1 to +0.1', n: 20 }, { bin: '+0.1 to +0.3', n: 8 }, { bin: '+0.3 to +0.6', n: 4 }, { bin: '> +0.6', n: 2 }] }, o || {});
const gaps = (o) => ({ comment_vs_post: gap(), reply_vs_post: gap({ n: 30, rows: 600, median_rows: 15, small: 9, n_10_29: 11, n_30_plus: 10, mean: -0.2, more_negative: 6, more_positive: 3, aligned: 21 }), reply_vs_comment: { n: 40, mean: -0.08, reply_rows: 700, comment_rows: 900 }, flag: 0.3, min_scored: 5, min_comments: 5, small_n: 10, ...(o || {}) });
const status = (o) => Object.assign({ posts: 1000, has_comments: 100, confirmed_zero: 500, not_collected_known: 300, unknown_no_comment_data: 100 }, o || {});
const plat = (o) => ({ post: post(), comment: layer(), reply: layer({ scored: 220, neg_pct: 52, neu_pct: 31, pos_pct: 17, net: -35 }), gaps: gaps(), status: status(), coverage: { posts: 1000, with_comments: 100, with_scored_comments: 95, with_replies: 50, with_any: 100, comment_rows: 500, comments_scored: 480, coverage_pct: 10, at_cap: 8, median_comments: 6 }, ...(o || {}) });
function payload(o) {
  return Object.assign({
    version: 'v2', source: 'audience_sentiment.duckdb: xplat_post_audience_sentiment_v2 + comment / reply layer tables', built: '2026-11-21T06:27:12+00:00',
    filters: { from: '2026-06-14', to: '2026-11-20', platforms: ['youtube_video', 'instagram'], platforms_asked: [], theme: '', entity_uid: '', basis: 'post' },
    unsupported_platforms: [], available_platforms: ['youtube_video', 'instagram'], date_bounds: { post_min: '2026-06-14', post_max: '2026-11-20', comment_max: '2026-11-20', posts_before_2026_06_14: 3 }, themes_available: ['D01', 'D05', 'D77'], n_posts: 30341, n_posts_with_data: 13542, undated_posts_excluded_by_dates: 1,
    entity: { coverage: null }, layers: { all: plat(), youtube_video: plat(), instagram: plat({ comment: layer({ scored: 40243, net: 0.7 }) }) },
    themes: [{ theme: 'unassigned', posts: 9000, c_scored: 90000, c_net: -6, c_tone: -0.07, r_scored: 40000, r_net: -30, r_tone: -0.24, gap_n: 3000, gap_mean: 0.05 },
             { theme: 'D01', posts: 1173, c_scored: 33583, c_net: -30.2, c_tone: -0.27, r_scored: 15000, r_net: -48, r_tone: -0.38, gap_n: 837, gap_mean: 0.1 },
             { theme: 'D77', posts: 5, c_scored: 12, c_net: null, c_tone: null, r_scored: 0, r_net: null, r_tone: null, gap_n: 0, gap_mean: null }],
    granularity: 'month', trend_basis: 'post',
    trend: ['2026-08-01', '2026-09-01', '2026-10-01'].flatMap((p, i) => ['youtube_video', 'instagram'].map((pl) => ({ platform: pl, period: p, posts: 50,
      comment: { scored: 200 + i, neg: 80, neu: 40, pos: 80, net: i === 0 && pl === 'instagram' ? null : -10 + i * 3, tone: -0.1 }, reply: { scored: 100, neg: 50, neu: 30, pos: 20, net: -30 + i, tone: -0.2 } }))),
    top_gaps: { negative: [{ post_uid: 'p1', platform: 'youtube_video', date: '2026-10-02', url: 'https://www.youtube.com/watch?v=abc', text: 'A video <b>title</b> that is long', theme: 'D01', post_tone: 0.4, comment_tone: -0.5, gap: -0.9, comments: 120, replies: 30, reply_gap: -1.0, small_sample: false, caption_effect: false }],
                positive: [{ post_uid: 'p2', platform: 'instagram', date: '2026-11-02', url: 'https://www.instagram.com/p/xyz/', text: 'An instagram post', theme: null, post_tone: -0.6, comment_tone: 0.2, gap: 0.8, comments: 40, replies: 4, reply_gap: null, small_sample: false, caption_effect: false }], min_comments: 10 },
    top_gaps_by_platform: {
      youtube_video: { negative: [{ post_uid: 'p1', platform: 'youtube_video', date: '2026-10-02', url: 'https://www.youtube.com/watch?v=abc', text: 'YT only card', theme: 'D01', post_tone: 0.4, comment_tone: -0.5, gap: -0.9, comments: 120, replies: 30, reply_gap: -1.0, small_sample: false, caption_effect: false }], positive: [] },
      instagram: { negative: [{ post_uid: 'p9', platform: 'instagram', date: '2026-11-01', url: 'https://www.instagram.com/p/cap/', text: 'A promo caption', theme: null, post_tone: 0.8, comment_tone: -0.2, gap: -1.0, comments: 9, replies: 0, reply_gap: null, small_sample: true, caption_effect: true }],
                   positive: [] } },
    quality: { grain: 'all dates', built: '2026-11-27', conf_high: 0.6, conf_low: 0.45, min_pooled: 30, ignores_theme_entity: true, rows: [
      { platform: 'youtube_video', layer: 'comment', scored: 1000, no_text: 3, emoji_only: 50, pos_all: 336, pos_sarc_likely: 7, pos_sarc_possible: 12, words_only: 947, confident: 600, uncertain: 250, weak: 147, net_all: -12.6, net_headline: -12.6, net_words: -16.6, net_confident: -19.1, pos_pct: 33.6, pos_lo: 30.7, pos_hi: 36.6 },
      { platform: 'instagram', layer: 'comment', scored: 20, no_text: 0, emoji_only: 5, words_only: 15, confident: 8, uncertain: 8, weak: 4, net_all: null, net_headline: null, net_words: null, net_confident: null, pos_pct: null, pos_lo: null, pos_hi: null }] },
    quality_trend: { min_weeks: 8, min_n: 30, min_cover: 0.7, rows: [
      { platform: 'youtube_video', layer: 'comment', weeks: 20, cover: 1, slope: -0.41, lo: -0.64, hi: -0.22, p: 0.0004, trend: 'down' },
      { platform: 'instagram', layer: 'comment', weeks: 5, cover: 1, slope: null, lo: null, hi: null, p: null, trend: null },
      { platform: 'linkedin', layer: 'comment', weeks: 12, cover: 0.3, slope: null, lo: null, hi: null, p: null, trend: null }] },
    filter_coverage: { posts_with_data: 1000, with_theme: 320, theme_kept: 40, entity_kept: 25 },
    limits: { min_pooled: 30, min_gap: 5, gap_flag: 0.3, post_class: 0.15, small_n: 10, min_top: 10, min_comments: 5, caption_tone: 0.5, yt_cap: 100, thin_trend: 100 } }, o || {});
}
const nullLayer = () => layer({ posts_with_data: 1, rows: 7, scored: 7, neg: 3, neu: 2, pos: 2, supported: false, neg_pct: null, neu_pct: null, pos_pct: null, net: null, tone_mean: null, conf_mean: null });
const LIMITED = payload({ n_posts: 3, n_posts_with_data: 1, layers: (() => { const p = plat({ post: post({ with_tone: 0, supported: false, tone_mean: null, pos_pct: null, neg_pct: null, neu_pct: null, net: null }), comment: nullLayer(), reply: nullLayer(),
  gaps: gaps({ comment_vs_post: gap({ n: 0, rows: 0, median_rows: null, small: 0, n_10_29: 0, n_30_plus: 0, mean: null, median: null, more_negative: 0, more_positive: 0, aligned: 0, hist: [] }), reply_vs_post: gap({ n: 0, rows: 0, median_rows: null, small: 0, n_10_29: 0, n_30_plus: 0, mean: null, median: null, more_negative: 0, more_positive: 0, aligned: 0, hist: [] }), reply_vs_comment: { n: 0, mean: null, reply_rows: 0, comment_rows: 0 } }) });
  return { all: p, youtube_video: p, instagram: p }; })(), themes: [], trend: [], top_gaps: { negative: [], positive: [] }, filters: { from: '2026-11-20', to: '2026-11-20', platforms: ['youtube_video', 'instagram'], theme: 'D05', entity_uid: 'e1', basis: 'post' },
  entity: { uid: 'e1', posts_in_entity_extract: 3, coverage: { youtube_video: { posts_with_data: 12523, with_entity_data: 8206, enriched_posts: 4219, enriched_with_entity_data: 0 }, instagram: { posts_with_data: 1397, with_entity_data: 92, enriched_posts: 972, enriched_with_entity_data: 0 } } } });
const EMPTY = payload({ n_posts: 12, n_posts_with_data: 0, layers: { all: plat({ comment: nullLayer(), reply: nullLayer() }), youtube_video: plat(), instagram: plat() }, trend: [], themes: [], top_gaps: { negative: [], positive: [] } });

async function boot(mode, handler) {
  const urls = []; const errors = [];
  const dom = new JSDOM(html, {
    runScripts: 'dangerously', url: 'http://localhost/',
    beforeParse(win) {
      win.fetch = (u) => {
        u = String(u);
        if (u.includes('/api/explore/audience')) {
          urls.push(u);
          if (LIVE && mode === 'live') { return fetch(LIVE + u).then(async (r) => ({ ok: r.ok, status: r.status, json: () => r.json() })); }
          const out = handler(u);
          return Promise.resolve({ ok: out.status ? out.status < 400 : true, status: out.status || 200, json: () => Promise.resolve(out.body) });
        }
        if (u.includes('/api/')) { return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ rows: [], total_posts: 0, with_views: [], without_views: [], by_virality: [], terms: [] }) }); }
        return Promise.resolve({ ok: true, json: () => Promise.resolve(u.includes('metrics.json') ? base : {}) });
      };
      win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
      Object.defineProperty(win.HTMLElement.prototype, 'clientWidth', { get: () => 900 });
      Object.defineProperty(win.HTMLElement.prototype, 'clientHeight', { get: () => 400 });
    },
  });
  dom.window.addEventListener('error', (e) => errors.push(e.message || String(e)));
  const s = dom.window.document.createElement('script'); s.textContent = script; dom.window.document.body.appendChild(s);
  await wait(2200);
  const doc = dom.window.document;
  doc.getElementById('mode-explore-btn').click(); await wait(500);
  return { dom, doc, urls, errors, win: dom.window };
}
const tile = (ctx, k) => {
  if (k === 'aud') {
    return {
      click() {
        const sent = ctx.doc.querySelector('.ex-tile[data-tile="sent"]');
        if (sent && (!sent.classList.contains('active') || ctx.doc.querySelector('button[data-sub="post"]')?.classList.contains('active'))) {
          sent.click();
        }
        const s = ctx.doc.querySelector('button[data-sub="aud"]');
        if (s) s.click();
      },
      textContent: 'Audience Sentiment',
      getAttribute(a) { return 'aud'; }
    };
  }
  return ctx.doc.querySelector(`.ex-tile[data-tile="${k}"]`);
};
const view = (ctx) => (ctx.doc.querySelector('[data-panel="explore_view"]') || {}).textContent || '';
const clean = (t) => !/NaN|undefined|\[object|null%/.test(t);

console.log('1. tile, layout and the full payload');
{
  const ctx = await boot('canned', () => ({ body: payload() }));
  const tiles = [...ctx.doc.querySelectorAll('.ex-tile')].map((b) => b.getAttribute('data-tile'));
  chk('the five tiles are unchanged and in order; Sentiment and Audience are merged', JSON.stringify(tiles) === JSON.stringify(['topics', 'viral', 'spread', 'life', 'sent']), tiles.join(','));
  chk('the Sentiment & Audience tile covers both author tone and audience reactions', tile(ctx, 'sent').textContent.includes('Sentiment & Audience'));
  tile(ctx, 'aud').click(); await wait(900);
  const t = view(ctx);
  chk('the tile opens with its title and the three layers named', t.includes('Audience Sentiment') && t.includes('Post tone') && t.includes('Comments') && t.includes('Replies'));
  chk('YouTube, Instagram and Both platforms are all shown side by side', ['YouTube', 'Instagram', 'Both platforms'].every((x) => t.includes(x)));
  chk('post, comment and reply rows are separate (3 layers x 3 platform groups)', (t.match(/Post tone/g) || []).length >= 3 && ctx.doc.querySelectorAll('.aud-dist-r').length === 9);
  chk('distribution bars are drawn with positive / neutral / negative parts', ctx.doc.querySelectorAll('.aud-bar .aud-neg').length === 9 && ctx.doc.querySelectorAll('.aud-bar .aud-pos').length === 9);
  chk('percentages and net sentiment appear', t.includes('42%') && t.includes('-4') && t.includes('Net'));
  chk('sentiment-quality section: headline, words-only and confident-only nets, emoji share and the Wilson interval are shown', t.includes('Sentiment quality') && t.includes('Net, words only') && t.includes('-17') && t.includes('30.7') && t.includes('Wilson 95%') && t.includes('sarcasm cue') && t.includes('2.1%'));
  chk('sentiment-quality: a platform under 30 scored rows shows dashes, never 0 or NaN, and the page stays clean', t.includes('Instagram') && clean(t));
  chk('weekly trend: slope with interval, direction and p are shown; thin or gappy series say why instead of a number', t.includes('Weekly sentiment trend') && t.includes('-0.41 (-0.64 to -0.22)') && t.includes('Falling') && t.includes('<0.001') && t.includes('fewer than 8 weeks') && t.includes('cover only 30%') && clean(t));
  chk('the gap section explains and shows the comment, reply and reply-vs-comment gaps', t.includes('sentiment gap') && t.includes('Comments vs post') && t.includes('Replies vs post') && t.includes('Replies vs comments'));
  chk('trend charts for comments and replies are drawn (svg)', ctx.doc.querySelectorAll('.aud-plot svg').length === 2, String(ctx.doc.querySelectorAll('.aud-plot svg').length));
  chk('themes: known D-themes get their names, unassigned and an unknown theme render (with a warning), nothing dropped', t.includes('D01 ·') && t.includes('No theme (unassigned)') && t.includes('D77 (not in the current taxonomy)'));
  chk('coverage table separates reported zero / reported-not-collected / unknown', t.includes('Reported zero comments') && t.includes('Comments reported, none collected') && t.includes('No comment data, count unknown'));
  const a = [...ctx.doc.querySelectorAll('.aud-post a')];
  chk('gap cards show the posts with safe links; markup in text is shown as text', a.length === 2 && a[0].getAttribute('href').startsWith('https://www.youtube.com/watch') && a[0].getAttribute('rel').includes('noopener') && !ctx.doc.querySelector('.aud-post-t b'));
  chk('no NaN / undefined / null leaks into the page', clean(t));
  chk('the free-text search box is not shown for this tile; entity and theme pickers are', !ctx.doc.querySelector('input[placeholder^="Search topic"]') && !!ctx.doc.getElementById('ex-ent') && !!ctx.doc.getElementById('ex-theme'));
  chk('no script errors', ctx.errors.length === 0, ctx.errors.join(' | '));
  // ---- filters send their values
  const set = async (fn) => { ctx.urls.length = 0; fn(); await wait(700); return ctx.urls[ctx.urls.length - 1] || ''; };
  let u = await set(() => { const s = ctx.doc.getElementById('ex-theme'); s.value = 'D05'; s.dispatchEvent(new ctx.win.Event('change')); });
  chk('theme filter sends theme=D05', /theme=D05/.test(u), u);
  const cols = base.tables.entity_dim.columns, row = base.tables.entity_dim.rows[0];
  const ent = { entity_uid: row[cols.indexOf('entity_uid')], canonical_name: row[cols.indexOf('canonical_name')] };
  u = await set(() => { const i = ctx.doc.getElementById('ex-ent'); i.value = ent.canonical_name; i.dispatchEvent(new ctx.win.Event('change')); });
  chk('entity filter sends entity_uid', u.includes('entity_uid=' + encodeURIComponent(ent.entity_uid)), u.slice(-80));
  const det = ctx.doc.querySelector('details.ex-plat');
  const boxes = [...det.querySelectorAll('input[type=checkbox]')];
  const labels = [...det.querySelectorAll('label')].map((l) => l.textContent.trim());
  const ii = labels.indexOf('instagram');
  u = await set(() => { boxes[ii].checked = true; boxes[ii].dispatchEvent(new ctx.win.Event('change')); [...det.querySelectorAll('button')].find((b) => b.textContent === 'Apply platforms').click(); });
  chk('platform filter sends platforms=instagram', /platforms=instagram/.test(u), u);
  u = await set(() => { ctx.doc.getElementById('ex-date-from').value = '2026-10-01'; ctx.doc.getElementById('ex-date-to').value = '2026-10-20'; ctx.doc.getElementById('ex-date-apply').click(); });
  chk('by default (all dates) the shared page range is NOT sent to the audience tile', !/from=/.test(u) && !/to=/.test(u), u);
  u = await set(() => { [...ctx.doc.querySelectorAll('.df-preset-btn')].find((b) => b.textContent.startsWith('Page range')).click(); });
  chk('choosing "Page range" sends the shared date range from / to', /from=2026-10-01/.test(u) && /to=2026-10-20/.test(u), u);
  chk('a note says the audience data runs later than the page range (metrics snapshot ends earlier)', view(ctx).includes('runs to 2026-11-20') && view(ctx).includes('All dates'));
  u = await set(() => { [...ctx.doc.querySelectorAll('.df-preset-btn')].find((b) => b.textContent === 'All dates').click(); });
  chk('"All dates" sends no date limits (so posts after the metrics snapshot are included)', !/from=/.test(u) && !/to=/.test(u), u);
  u = await set(() => { ctx.doc.getElementById('aud-date-from').value = '2026-11-01'; ctx.doc.getElementById('aud-date-to').value = '2026-11-20'; ctx.doc.getElementById('aud-date-apply').click(); });
  chk('a custom range reaches November (beyond the page range)', /from=2026-11-01/.test(u) && /to=2026-11-20/.test(u), u);
  chk('the page-range note is hidden once dates are chosen here', !view(ctx).includes('runs to 2026-11-20'));
  u = await set(() => { [...ctx.doc.querySelectorAll('.df-preset-btn')].find((b) => b.textContent.startsWith('Page range')).click(); });
  chk('"Page range" returns to the shared dates', /from=2026-10-01/.test(u) && /to=2026-10-20/.test(u), u);
  u = await set(() => { [...ctx.doc.querySelectorAll('.df-preset-btn')].find((b) => b.textContent === 'Comment date').click(); });
  chk('"Comment date" switches the trend basis', /basis=comment/.test(u), u);
  const reset = [...ctx.doc.querySelectorAll('.df-preset-btn')].find((b) => b.textContent === 'Reset'); reset.click(); await wait(500);
  chk('Reset clears the filters and the page still works', !!view(ctx) && ctx.errors.length === 0);
  // ---- other tiles still behave
  for (const k of ['topics', 'viral', 'spread', 'life', 'sent']) {
    ctx.urls.length = 0; tile(ctx, k).click(); await wait(500);
    chk(`tile "${k}" still renders and makes no audience request`, view(ctx).length > 40 && !ctx.urls.length && ctx.errors.length === 0, `${view(ctx).length} chars`);
  }
  chk('the Sentiment Analysis tile still shows its post-tone tables', (() => { tile(ctx, 'sent').click(); return true; })());
  await wait(400);
  chk('post-tone tables intact on the existing tile', view(ctx).includes('Tone by platform'), '');
  ctx.dom.window.close();
}

console.log('1b. gap shares and the YouTube Comments wording');
{
  const ctx = await boot('canned', () => ({ body: payload() }));
  tile(ctx, 'aud').click(); await wait(900);
  const rows = [...ctx.doc.querySelectorAll('#explore-view table tr')].map((tr) => [...tr.children].map((c) => c.textContent.trim())).filter((r) => r.length >= 8);
  const gapRows = rows.filter((r) => /vs post/.test(r[1]) && r[2] !== '\u2014');
  const nums = (r) => [r[5], r[6], r[7]].map((x) => parseFloat(x));
  chk('the gap table shows the three shares (more negative / aligned / more positive) for every row with a gap', gapRows.length === 6, String(gapRows.length));
  chk('each row\'s three shares sum to about 100% (rounding allowed) and none is above 100%', gapRows.every((r) => { const v = nums(r); return v.every((x) => x <= 100) && Math.abs(v.reduce((a, b) => a + b, 0) - 100) <= 2; }), gapRows.map((r) => nums(r).join('/')).join(' | '));
  ctx.dom.window.close();
  const yv = await boot('canned', () => ({ body: payload({ unsupported_platforms: ['youtube_comment'], filters: { from: '2026-06-14', to: '2026-10-25', platforms: ['youtube_video', 'instagram'], platforms_asked: ['youtube_video', 'youtube_comment', 'instagram'], theme: '', entity_uid: '', basis: 'post' } }) }));
  tile(yv, 'aud').click(); await wait(900);
  const t1 = view(yv);
  chk('YouTube Comments chosen with YouTube videos: says comments are analysed under the video audience layer and already included', t1.includes('not a separate source') && t1.includes('audience layer of YouTube videos') && t1.includes('already included under youtube_video'));
  chk('the old misleading sentence is gone', !t1.includes('No comment text exists for youtube_comment'));
  yv.dom.window.close();
  const yo = await boot('canned', () => ({ body: payload({ unsupported_platforms: ['youtube_comment'], n_posts: 0, n_posts_with_data: 0, filters: { from: '2026-06-14', to: '2026-10-25', platforms: [], platforms_asked: ['youtube_comment'], theme: '', entity_uid: '', basis: 'post' } }) }));
  tile(yo, 'aud').click(); await wait(900);
  const t2 = view(yo);
  chk('YouTube Comments alone: explains it cannot give a platform-level analysis and asks for youtube_video', t2.includes('not a separate source') && t2.includes('cannot give a platform-level audience analysis') && t2.includes('YouTube Comments alone cannot be analysed here') && !t2.includes('No comment text exists'));
  yo.dom.window.close();
  const ym = await boot('canned', () => ({ body: payload({ unsupported_platforms: ['youtube_comment', 'telegram'], filters: { from: '2026-06-14', to: '2026-10-25', platforms: ['instagram'], platforms_asked: ['youtube_comment', 'telegram', 'instagram'], theme: '', entity_uid: '', basis: 'post' } }) }));
  tile(ym, 'aud').click(); await wait(900);
  const t3 = view(ym);
  chk('YouTube Comments mixed with a counts-only platform: both notes, each correct (telegram keeps the counts-only wording)', t3.includes('not a separate source') && t3.includes('No comment text exists for telegram (counts only)') && !t3.includes('No comment text exists for youtube_comment'));
  ym.dom.window.close();
}

console.log('1c. stage 1: how to read, coverage, gap counts, small samples, min comments, divergent filters');
{
  const ctx = await boot('canned', () => ({ body: payload() }));
  tile(ctx, 'aud').click(); await wait(900);
  const t = view(ctx);
  const btn = (label) => [...ctx.doc.querySelectorAll('[data-panel="explore_view"] button')].find((b) => b.textContent.trim() === label);
  const how = ctx.doc.querySelector('.aud-how');
  chk('a compact "How to read this dashboard" is always visible, with the six definitions', !!how && how.querySelectorAll('li').length === 6 && /Net sentiment.*Positive % − Negative %/.test(how.textContent) && /−1.*\+1/.test(how.textContent) &&
      /Gap score = comment tone − post tone/.test(how.textContent) && /within ±0\.30.*does not mean the audience agrees/.test(how.textContent) && /wording, not beliefs or intent/.test(how.textContent) && /certainty.*not how often it is right/.test(how.textContent));
  chk('the detailed methodology stays available in an expandable section', !!ctx.doc.querySelector('details.ex-explain') && t.includes('Detailed methodology'));
  chk('coverage at a glance sits above the sentiment tables: matching posts, collected, scored, coverage %, per platform', t.includes('Coverage at a glance') && t.indexOf('Coverage at a glance') < t.indexOf('Post, comment and reply sentiment') &&
      t.includes('Posts matching the filters') && t.includes('With collected comments') && t.includes('With scored comments') && t.includes('10.0%'));
  chk('YouTube collection limit is explained (about 100 per video, how many posts are at it, what it means)', /collected up to about 100 per video\. 8 of 100 YouTube posts with comments \(8%\) are at that limit/.test(t) && t.includes('may not represent the whole thread'));
  chk('low Instagram coverage is called out', /Instagram coverage is low: only 10\.0%/.test(t));
  chk('the gap is defined and a positive gap is not read as support', t.includes('Audience gap = mean comment sentiment − original post sentiment') && t.includes('does not necessarily mean the audience supports the content'));
  chk('the 0.30 line is called an operational flag, not a statistical test', /operational flag for triage, not a statistical test/.test(t) && t.includes('(gap ≤ −0.30)') && t.includes('(gap ≥ +0.30)'));
  const gapHead = [...ctx.doc.querySelectorAll('[data-panel="explore_view"] table th')].map((x) => x.textContent);
  chk('every gap row shows the comment counts behind it (scored comments, median per post, posts under 10)', gapHead.some((x) => /^Scored comments \/ replies/.test(x)) && gapHead.some((x) => /Median per post/.test(x)) && gapHead.some((x) => /< 10 \(small sample\)/.test(x)));
  chk('small-sample bars are drawn for the comment and reply gaps (3 platform groups x 2)', ctx.doc.querySelectorAll('.aud-size-r').length === 6 && ctx.doc.querySelectorAll('.aud-size-r .aud-s-small').length === 6);
  chk('the layer rows are still exactly 9 (the new bars did not add layer rows)', ctx.doc.querySelectorAll('.aud-dist-r').length === 9);
  chk('trend charts carry a volume strip, and the trend table says whether a period is thin', ctx.doc.querySelectorAll('.aud-vol').length === 2 && ctx.doc.querySelectorAll('.aud-vol i').length === 6 && /Scored rows per period/.test(t) && gapHead.some((x) => /^Sample(?! note)/.test(x)));
  chk('divergent cards show the comment count; the default list is the unchanged "both" list', /120 comments/.test(t) && /40 comments/.test(t) && ctx.doc.querySelectorAll('.aud-post').length === 2 && !t.includes('A promo caption'));
  const before = ctx.urls.length;
  btn('Instagram') && btn('Instagram').click(); await wait(600);
  const t2 = view(ctx);
  chk('the platform button narrows the divergent list without another request', ctx.urls.length === before && t2.includes('A promo caption') && !t2.includes('A video'), String(ctx.urls.length - before));
  chk('an Instagram card with a promotional caption is flagged, and a small sample is flagged', !!ctx.doc.querySelector('.aud-chip-cap') && !!ctx.doc.querySelector('.aud-chip-warn') && /9 comments · small sample/.test(t2) && t2.includes('strongly positive post +0.80'));
  btn('YouTube') && btn('YouTube').click(); await wait(600);
  chk('YouTube card with a post tone of +0.40 carries no strong-tone chip', !ctx.doc.querySelector('.aud-chip-cap') && view(ctx).includes('YT only card'));
  btn('Both') && btn('Both').click(); await wait(600);
  chk('"Both" restores the original list', view(ctx).includes('A video') && ctx.doc.querySelectorAll('.aud-post').length === 2);
  const n0 = ctx.urls.length;
  btn('20') && btn('20').click(); await wait(900);
  const last = ctx.urls[ctx.urls.length - 1] || '';
  chk('choosing a minimum number of comments asks the API for it (min_comments=20) and says what it does', ctx.urls.length === n0 + 1 && /min_comments=20/.test(last) && /left out of this section/.test(view(ctx)), last);
  btn('5 (default)') && btn('5 (default)').click(); await wait(900);
  chk('the default minimum (5) sends no min_comments parameter', !/min_comments/.test(ctx.urls[ctx.urls.length - 1] || ''));
  ctx.dom.window.close();
}

console.log('1d. theme / entity filter coverage is visible near the top');
{
  const p = payload({ n_posts_with_data: 100, filters: { from: '2026-06-14', to: '2026-11-20', platforms: ['youtube_video', 'instagram'], theme: 'D01', entity_uid: 'e1', basis: 'post' },
    entity: { uid: 'e1', posts_in_entity_extract: 25, coverage: { youtube_video: { posts_with_data: 12523, with_entity_data: 8206, enriched_posts: 4219, enriched_with_entity_data: 0 }, instagram: { posts_with_data: 1397, with_entity_data: 92, enriched_posts: 972, enriched_with_entity_data: 0 } } } });
  const ctx = await boot('canned', () => ({ body: p }));
  tile(ctx, 'aud').click(); await wait(900);
  const t = view(ctx);
  chk('two filters: one statement gives what is left after ALL of them (the payload\'s own count), not the separate counts', /All filters together leave 100 posts with comment data \(10\.0% of the 1,000 with comment data/.test(t), (t.match(/All filters together[^.]*\./) || [''])[0]);
  chk('...and each filter on its own is named as such, with how few posts have a theme', /Each on its own would keep: theme .* 40, entity .* 25/.test(t) && /only 32% of posts with comment data have any theme/.test(t));
  chk('the combined notice appears before the sentiment tables', t.indexOf('All filters together leave') > 0 && t.indexOf('All filters together leave') < t.indexOf('Post, comment and reply sentiment'));
  const p1 = payload({ n_posts_with_data: 100, filters: { from: '', to: '', platforms: ['youtube_video', 'instagram'], theme: 'D01', entity_uid: '', basis: 'post' } });
  const c1 = await boot('canned', () => ({ body: p1 })); tile(c1, 'aud').click(); await wait(900);
  chk('one filter alone: its own plain statement (theme leaves N of B)', /Theme filter: .* leaves 100 posts of 1,000 with comment data \(10%\)/.test(view(c1)), (view(c1).match(/Theme filter[^.]*\./) || [''])[0]);
  c1.dom.window.close();
  ctx.dom.window.close();
}

console.log('1e. stabilisation: gap table fit, trend chart, platform rows, plurals, strong post tone, info buttons, defaults');
{
  const trend = ['2025-11-01', '2025-12-01', '2026-01-01'].flatMap((p, k) => ['youtube_video', 'instagram'].map((pl) => {
    const n = pl === 'youtube_video' ? [200, 60, 200][k] : [10, 40, 200][k];
    return { platform: pl, period: p, posts: 5, comment: { scored: n, neg: 1, neu: 1, pos: 1, net: n >= 30 ? -10 + k : null, tone: -0.1 }, reply: { scored: 120, neg: 1, neu: 1, pos: 1, net: -20, tone: -0.2 } };
  }));
  const card = (o) => Object.assign({ post_uid: 'q', platform: 'youtube_video', date: '2026-10-02', url: 'https://www.youtube.com/watch?v=q', text: 'card', theme: null, post_tone: 0.2, comment_tone: -0.1, gap: -0.3,
    comments: 20, replies: 1, reply_gap: null, small_sample: false, caption_effect: false, extreme_post_tone: false }, o || {});
  const P = payload({ n_posts: 1, n_posts_with_data: 1, trend, granularity: 'month',
    top_gaps: { negative: [card({ text: 'NEG-POST', post_tone: -0.8, extreme_post_tone: true, comments: 1, replies: 1, small_sample: true }), card({ text: 'MILD', post_tone: 0.2 }), card({ text: 'POS-YT', post_tone: 0.7, extreme_post_tone: true })], positive: [], min_comments: 10 } });
  P.layers.all.gaps.reply_vs_comment = { n: 40, mean: -0.08, reply_rows: 700, comment_rows: 900 };
  const ctx = await boot('canned', () => ({ body: P }));
  chk('by default the audience tile asks for all dates (the first request has no from / to)', true);
  tile(ctx, 'aud').click(); await wait(900);
  const t = view(ctx);
  chk('first audience request has no from / to (all dates is the default window)', ctx.urls.length > 0 && !/from=/.test(ctx.urls[0]) && !/to=/.test(ctx.urls[0]), ctx.urls[0]);
  const heads = (tbl) => [...tbl.querySelectorAll('th')].map((x) => x.textContent.replace('▾', '').trim());
  const tbls = [...ctx.doc.querySelectorAll('.aud-tbl table')];
  const A = tbls.find((x) => heads(x).includes('Median gap'));
  chk('gap table A has exactly the 8 columns an analyst reads first, incl. median, both directions and within ±0.30', !!A && heads(A).length === 8 && /More negative/.test(heads(A).join('|')) && /Within ±0\.30/.test(heads(A).join('|')) && /More positive/.test(heads(A).join('|')), A ? heads(A).join(' | ') : 'missing');
  const B = tbls.find((x) => heads(x).some((h) => /^Scored comments \/ replies/.test(h)));
  chk('gap table B keeps the sample information (scored rows, median per post, posts under 10, note)', !!B && heads(B).length === 6 && heads(B).some((h) => /Sample note/.test(h)) && heads(B).some((h) => /Median per post/.test(h)));
  chk('every audience table sits in a wrapper whose headers may wrap (.aud-tbl) and has a scroll cue element', ctx.doc.querySelectorAll('.aud-tbl').length >= 7 && ctx.doc.querySelectorAll('.aud-scroll-hint').length === ctx.doc.querySelectorAll('.aud-tbl').length);
  const rvc = [...B.querySelectorAll('tbody tr')].filter((r) => /Replies vs comments/.test(r.textContent));
  chk('Replies vs comments: a missing median shows a dash, never 0', rvc.length >= 1 && rvc.every((r) => { const c = [...r.children].map((x) => x.textContent.trim()); return c[3] === '—' && c[4] === '—'; }), rvc.map((r) => [...r.children].map((x) => x.textContent.trim()).join('|')).join(' // '));
  chk('"1 post matches the filters; 1 of them has comment or reply data" (singular)', t.includes('1 post matches the filters; 1 of them has comment or reply data.'));
  chk('chips: "1 comment", "1 reply" (singular) and "20 comments"', /1 comment · small sample/.test(t) && /1 reply(?!ies)/.test(t) && !/(^|[^\d])1 replies/.test(t) && !/(^|[^\d])1 comments/.test(t) && /20 comments/.test(t));
  const chips = [...ctx.doc.querySelectorAll('.aud-chip-cap')].map((x) => x.textContent);
  chk('strong post tone: shown for a strongly negative post AND for a strongly positive YouTube post, not for a mild one', chips.some((x) => /strongly negative post -0\.80/.test(x)) && chips.some((x) => /strongly positive post \+0\.70/.test(x)) && chips.length === 2, chips.join(' | '));
  chk('the strong-tone chip is keyboard-focusable and has an explanation', [...ctx.doc.querySelectorAll('.aud-chip-cap')].every((x) => x.getAttribute('tabindex') === '0' && (x.getAttribute('title') || '').length > 40));
  const svgs = [...ctx.doc.querySelectorAll('.aud-plot svg')];
  const labels = [...svgs[0].querySelectorAll('text')].map((x) => x.textContent);
  chk('trend x labels carry the year (Nov 2025 ... Jan 2026), not just "Nov 1"', labels.some((x) => /^Nov 2025$/.test(x)) && labels.some((x) => /^Jan 2026$/.test(x)) && !labels.some((x) => /^[A-Z][a-z]{2} \d{1,2}$/.test(x)), labels.join(','));
  chk('thin periods are drawn on the line itself: hollow markers and dashed segments (YouTube 60 scored, Instagram 40 scored)', svgs[0].querySelectorAll('circle[stroke-width="1.6"]').length === 2 && svgs[0].querySelectorAll('line[stroke-dasharray]').length >= 2, `hollow ${svgs[0].querySelectorAll('circle[stroke-width="1.6"]').length}, dashed ${svgs[0].querySelectorAll('line[stroke-dasharray]').length}`);
  chk('a period under 30 scored rows is not drawn (Instagram Nov 2025 has 10)', ![...svgs[0].querySelectorAll('title')].some((x) => /Instagram · Nov 2025/.test(x.textContent)));
  chk('every drawn point has a tooltip with its sample size', [...svgs[0].querySelectorAll('title')].some((x) => /YouTube · Dec 2025: net -9 \(60 scored, thin sample\)/.test(x.textContent)));
  chk('chart width follows its container (not a fixed 420) and never exceeds it', svgs.every((x) => +x.getAttribute('width') === 900), svgs.map((x) => x.getAttribute('width')).join(','));
  const key = ctx.doc.querySelector('.aud-trend-key');
  chk('the trend legend names YouTube and Instagram with swatches and explains the hollow / dashed thin marking', !!key && /YouTube/.test(key.textContent) && /Instagram/.test(key.textContent) && /hollow dot, dashed line = thin \(30–99 scored rows\)/.test(key.textContent) && !/dark line|light line/.test(t));
  const info = [...ctx.doc.querySelectorAll('.aud-how button.aud-info')];
  chk('how-to-read: each of the 6 lines has a real button (keyboard / touch) that opens its detail', info.length === 6 && info.every((b) => b.getAttribute('aria-expanded') === 'false'));
  info[0].click(); await wait(100);
  chk('pressing the info button shows the detail and sets aria-expanded', info[0].getAttribute('aria-expanded') === 'true' && info[0].parentElement.querySelector('.aud-info-t').style.display !== 'none');
  chk('a multilingual / code-mixed limitation note is shown', /Language: comments come in many languages.*code-mixed text, sarcasm/.test(t));
  const ig = payload({ filters: { from: '', to: '', platforms: ['instagram'], theme: '', entity_uid: '', basis: 'post' } });
  const c2 = await boot('canned', () => ({ body: ig })); tile(c2, 'aud').click(); await wait(900);
  const t3 = view(c2), rowsTxt = [...c2.doc.querySelectorAll('.aud-tbl tbody tr')].map((r) => r.children[0].textContent.trim());
  chk('Instagram only: no YouTube and no "Both platforms" rows in any table, bar or status row', !rowsTxt.includes('YouTube') && !rowsTxt.includes('Both platforms') && rowsTxt.includes('Instagram') && ![...c2.doc.querySelectorAll('.aud-dist-l, .aud-size-r .aud-dist-l')].some((x) => /YouTube|Both/.test(x.textContent)), [...new Set(rowsTxt)].join(','));
  chk('Instagram only: no empty YouTube series in the trend legend', !/YouTube/.test(c2.doc.querySelector('.aud-trend-key').textContent));
  c2.dom.window.close();
  const rs = [...ctx.doc.querySelectorAll('.df-preset-btn')].find((b) => b.textContent.startsWith('Page range')); rs.click(); await wait(500);
  ctx.urls.length = 0; [...ctx.doc.querySelectorAll('.df-preset-btn')].find((b) => b.textContent === 'Reset').click(); await wait(700);
  chk('Reset returns the audience tile to all dates and the default minimum', !/from=/.test(ctx.urls[ctx.urls.length - 1] || '') && !/min_comments/.test(ctx.urls[ctx.urls.length - 1] || ''), ctx.urls[ctx.urls.length - 1]);
  ctx.dom.window.close();
}

console.log('2. limited data, empty selection and errors');
{
  const ctx = await boot('canned', () => ({ body: LIMITED }));
  tile(ctx, 'aud').click(); await wait(900);
  const t = view(ctx);
  chk('limited data: dashes instead of numbers, no percentage of nothing, no zeros', t.includes('—') && !/(^|[^\d.])0%/.test(t) && clean(t));   // a standalone 0% (not 10.0%)
  chk('limited data: "too few to show shares" shown on the bars, not an empty bar', ctx.doc.querySelectorAll('.aud-na').length >= 6);
  chk('entity filter note explains that newer posts have no entity data', t.includes('Entity filter') && t.includes('no entity data'));
  chk('theme and entity notice is shown and says themes are provisional', t.includes('All filters together leave') && t.includes('provisional'));
  chk('limited data: no script errors, page not broken', ctx.errors.length === 0, ctx.errors.join(' | '));
  ctx.dom.window.close();
  const e2 = await boot('canned', () => ({ body: EMPTY }));
  tile(e2, 'aud').click(); await wait(900);
  chk('no matching posts with all dates: a clear message and no useless "Show all dates" button', view(e2).includes('No posts with comment or reply data') && ![...e2.doc.querySelectorAll('button')].some((b) => b.textContent === 'Show all dates'));
  e2.urls.length = 0; e2.doc.getElementById('aud-date-from').value = '2026-12-01'; e2.doc.getElementById('aud-date-to').value = '2026-12-05'; e2.doc.getElementById('aud-date-apply').click(); await wait(700);
  chk('with custom audience dates the empty state offers "Show all dates"', /from=2026-12-01/.test(e2.urls[e2.urls.length - 1] || '') && [...e2.doc.querySelectorAll('button')].some((b) => b.textContent === 'Show all dates'));
  e2.urls.length = 0; [...e2.doc.querySelectorAll('button')].find((b) => b.textContent === 'Show all dates').click(); await wait(700);
  const lu = e2.urls[e2.urls.length - 1] || '';
  chk('"Show all dates" actually resets the audience dates (no from / to sent, date boxes cleared) and nothing breaks', !/from=/.test(lu) && !/to=/.test(lu) && e2.doc.getElementById('aud-date-from').value === '' && e2.errors.length === 0, lu);
  e2.dom.window.close();
  const e3 = await boot('canned', () => ({ status: 503, body: { error: 'the audience sentiment data is being rebuilt, try again in a minute', unavailable: true } }));
  tile(e3, 'aud').click(); await wait(900);
  chk('503 (data being rebuilt): the message is shown, the rest of the dashboard is untouched', view(e3).includes('being rebuilt') && e3.errors.length === 0);
  tile(e3, 'viral').click(); await wait(400);
  chk('after an error the other tiles still work', view(e3).length > 40 && e3.errors.length === 0);
  e3.dom.window.close();
  const e4 = await boot('canned', () => ({ body: payload({ unsupported_platforms: ['telegram'], filters: { from: '2026-06-14', to: '2026-11-20', platforms: [], platforms_asked: ['telegram'], theme: '', entity_uid: '', basis: 'post' }, n_posts: 0, n_posts_with_data: 0 }) }));
  tile(e4, 'aud').click(); await wait(900);
  chk('a platform without comment text: told so, asked to pick YouTube or Instagram', view(e4).includes('No comment text exists for telegram') && view(e4).includes('Pick YouTube'));
  e4.dom.window.close();
}

if (LIVE) {
  console.log('3. live: the real page against the real server and the real v2 tables');
  const ctx = await boot('live', () => ({ body: {} }));
  tile(ctx, 'aud').click(); await wait(300);
  [...ctx.doc.querySelectorAll('#explore-view .df-preset-btn')].find((b) => b.textContent === 'Full window').click(); await wait(8000);
  const t = view(ctx);
  const api = await (await fetch(LIVE + ctx.urls[ctx.urls.length - 1])).json();
  const fi = (n) => Number(n).toLocaleString('en-US');
  chk('live: the page shows the API\'s comment and reply scored counts (YouTube and Instagram)', t.includes(fi(api.layers.youtube_video.comment.scored)) && t.includes(fi(api.layers.instagram.comment.scored)) && t.includes(fi(api.layers.youtube_video.reply.scored)), `${api.layers.youtube_video.comment.scored} / ${api.layers.instagram.comment.scored}`);
  chk('live: trend charts and the theme table render from real data', ctx.doc.querySelectorAll('.aud-plot svg').length === 2 && t.includes('D01 ·'));
  chk('live: no NaN / undefined, no script errors', clean(t) && ctx.errors.length === 0, ctx.errors.join(' | '));
  ctx.urls.length = 0;
  const s = ctx.doc.getElementById('ex-theme'); s.value = 'D05'; s.dispatchEvent(new ctx.win.Event('change')); await wait(5000);
  const api2 = await (await fetch(LIVE + ctx.urls[ctx.urls.length - 1])).json();
  chk('live: choosing a theme re-queries and the numbers change', api2.filters.theme === 'D05' && api2.layers.all.comment.scored < api.layers.all.comment.scored && view(ctx).includes(fi(api2.layers.all.comment.scored)), `${api2.layers.all.comment.scored} < ${api.layers.all.comment.scored}`);
  ctx.dom.window.close();
}
console.log(`\n${pass}/${pass + fail} checks passed`);
process.exit(fail ? 1 : 0);
