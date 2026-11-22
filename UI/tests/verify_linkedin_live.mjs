/**
 * Final Live Verification of Audience Sentiment Dashboard after LinkedIn Integration
 * Tests against live server http://localhost:8877/api/explore/audience
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const API_BASE = process.env.AUD_API || 'http://localhost:8882';
const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const script = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
const base = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));

let passed = 0;
let failed = 0;
const results = [];

function assert(condition, message, detail = '') {
  if (condition) {
    passed++;
    results.push({ status: 'PASS', message, detail });
    console.log(`  PASS: ${message}${detail ? ' -> ' + detail : ''}`);
  } else {
    failed++;
    results.push({ status: 'FAIL', message, detail });
    console.error(`  FAIL: ${message}${detail ? ' -> ' + detail : ''}`);
  }
}

async function fetchAudience(params = {}) {
  const qs = new URLSearchParams({ x: '1', basis: 'post', limit: '10', ...params }).toString();
  const res = await fetch(`${API_BASE}/api/explore/audience?${qs}`);
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return await res.json();
}

async function run() {
  console.log('=== AUDIENCE SENTIMENT LIVE SMOKE TEST ===\n');

  // 1. Individual Platform Loads
  console.log('--- 1. Testing Individual Platform Responses ---');
  
  // YouTube Video
  const ytData = await fetchAudience({ platforms: 'youtube_video' });
  assert(ytData.filters.platforms.includes('youtube_video'), 'YouTube filter accepted');
  assert(ytData.layers.youtube_video.comment.scored === 270122, 'YouTube scored comments match expected baseline', `${ytData.layers.youtube_video.comment.scored}`);
  assert(ytData.layers.youtube_video.reply.scored === 121378, 'YouTube scored replies match expected baseline', `${ytData.layers.youtube_video.reply.scored}`);
  assert(ytData.layers.youtube_video.comment.supported === true, 'YouTube comment sentiment supported');

  // Instagram
  const igData = await fetchAudience({ platforms: 'instagram' });
  assert(igData.filters.platforms.includes('instagram'), 'Instagram filter accepted');
  assert(igData.layers.instagram.comment.scored === 40243, 'Instagram scored comments match expected baseline', `${igData.layers.instagram.comment.scored}`);
  assert(igData.layers.instagram.reply.scored === 23814, 'Instagram scored replies match expected baseline', `${igData.layers.instagram.reply.scored}`);
  assert(igData.layers.instagram.comment.supported === true, 'Instagram comment sentiment supported');

  // LinkedIn
  const liData = await fetchAudience({ platforms: 'linkedin' });
  assert(liData.filters.platforms.includes('linkedin'), 'LinkedIn filter accepted');
  assert(liData.layers.linkedin.comment.scored === 4740, 'LinkedIn scored comments match expected baseline', `${liData.layers.linkedin.comment.scored}`);
  assert(liData.layers.linkedin.comment.supported === true, 'LinkedIn comment sentiment supported');
  assert(liData.layers.linkedin.comment.net !== null, 'LinkedIn net sentiment computed', `Net: ${liData.layers.linkedin.comment.net?.toFixed(1)}%`);

  // 2. LinkedIn Comment-Only Behavior (No Replies Layer)
  console.log('\n--- 2. Testing LinkedIn Comment-Only Behavior ---');
  assert(liData.layers.linkedin.reply.rows === 0, 'LinkedIn reply rows is 0 (comment-only)');
  assert(liData.layers.linkedin.reply.scored === 0, 'LinkedIn scored replies is 0');
  assert(liData.layers.linkedin.reply.net === null, 'LinkedIn reply net sentiment is null');
  assert(liData.layers.linkedin.gaps.reply_vs_post.n === 0, 'LinkedIn reply_vs_post gap count is 0');
  assert(liData.layers.linkedin.gaps.reply_vs_comment.n === 0, 'LinkedIn reply_vs_comment gap count is 0');

  // 3. LinkedIn First-10-Comment Limitation
  console.log('\n--- 3. Testing LinkedIn Limitation & Caveats ---');
  assert(liData.platform_caveats?.linkedin !== undefined, 'LinkedIn caveat field present in API response');
  assert(liData.platform_caveats?.linkedin.includes('first ~10 comments'), 'LinkedIn caveat notes ~10 comments limit', liData.platform_caveats?.linkedin);
  assert(liData.platform_caveats?.linkedin.includes('August 2026'), 'LinkedIn caveat notes collection start date');
  assert(liData.limits.li_cap === 10, 'LinkedIn cap in limits object is 10');

  // 4. Cross-Platform Comparison
  console.log('\n--- 4. Testing Cross-Platform Comparison (All 3 Platforms) ---');
  const allData = await fetchAudience({ platforms: 'youtube_video,instagram,linkedin' });
  const totalComments = ytData.layers.youtube_video.comment.scored + igData.layers.instagram.comment.scored + liData.layers.linkedin.comment.scored;
  assert(allData.layers.all.comment.scored === totalComments, 'Cross-platform total comments equal sum of 3 platforms', `${allData.layers.all.comment.scored} == ${totalComments}`);
  assert(allData.available_platforms.includes('linkedin'), 'LinkedIn in available_platforms list');
  assert(allData.layers.all.reply.scored === ytData.layers.youtube_video.reply.scored + igData.layers.instagram.reply.scored, 'Total replies exclude LinkedIn');

  // 5. Post vs Audience Sentiment Gap & Divergent Posts
  console.log('\n--- 5. Testing Sentiment Gap & Divergent Posts ---');
  assert(allData.top_gaps.negative.length > 0, 'Negative divergent posts present', `${allData.top_gaps.negative.length} cards`);
  assert(allData.top_gaps.positive.length > 0, 'Positive divergent posts present', `${allData.top_gaps.positive.length} cards`);
  // LinkedIn posts don't have post tone scores in analytics store, so verify they don't produce invalid gaps
  const liDivergent = (allData.top_gaps_by_platform?.linkedin?.negative || []).concat(allData.top_gaps_by_platform?.linkedin?.positive || []);
  assert(liDivergent.length === 0, 'LinkedIn posts have no false gaps without post tones', '0 cards with fake gaps');

  // 6. DOM Rendering & Visual Validation via JSDOM
  console.log('\n--- 6. Testing DOM Rendering in JSDOM Browser Simulation ---');
  const consoleErrors = [];
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: 'http://localhost/',
    beforeParse(win) {
      win.fetch = (u) => {
        u = String(u);
        if (u.includes('/api/explore/audience')) {
          return fetch(`${API_BASE}${u}`).then(async (r) => ({ ok: r.ok, status: r.status, json: () => r.json() }));
        }
        if (u.includes('/api/')) {
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ rows: [], total_posts: 0 }) });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve(u.includes('metrics.json') ? base : {}) });
      };
      win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
      Object.defineProperty(win.HTMLElement.prototype, 'clientWidth', { get: () => 900 });
      Object.defineProperty(win.HTMLElement.prototype, 'clientHeight', { get: () => 400 });
    }
  });
  const { window } = dom;
  window.console.error = (...args) => consoleErrors.push(args.join(' '));

  // Load script
  const scriptEl = window.document.createElement('script');
  scriptEl.textContent = script;
  window.document.body.appendChild(scriptEl);

  // Wait for initial render and data fetch
  await new Promise(r => setTimeout(r, 800));

  assert(consoleErrors.length === 0, 'Zero console errors during browser boot', consoleErrors.join('; '));

  // Check tile presence in Explore
  const exploreTab = window.document.querySelector('[data-tab="explore"]') || window.document.getElementById('tab-explore');
  if (exploreTab) exploreTab.click();
  await new Promise(r => setTimeout(r, 400));

  const audTile = window.document.getElementById('tile-aud') || window.document.querySelector('[data-tile="aud"]') || window.document.querySelector('.aud-tile');
  assert(audTile !== null || window.document.body.innerHTML.includes('Audience Sentiment'), 'Audience sentiment tile rendered in DOM');

  console.log('\n=======================================');
  console.log(`Results: ${passed} passed, ${failed} failed`);
  console.log('=======================================');

  process.exit(failed > 0 ? 1 : 0);
}

run().catch(err => {
  console.error('Test execution failed:', err);
  process.exit(1);
});
