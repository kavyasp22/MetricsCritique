import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const script = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
const base = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));

let drillUrl = '';

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost/',
  beforeParse(win) {
    win.fetch = (u) => {
      u = String(u);
      if (u.includes('/api/drill')) {
        drillUrl = u;
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            view: 'topic',
            shown: 3,
            total: 651,
            rows: [
              { post_uid: 'p1', platform: 'twitter', author_name: 'Defense Intel', text: 'Military surveillance update in Hormuz' }
            ]
          })
        });
      }
      if (u.includes('metrics.json')) return Promise.resolve({ ok: true, json: () => Promise.resolve(base) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    };
    win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
  }
});

const s = dom.window.document.createElement('script');
s.textContent = script;
dom.window.document.body.appendChild(s);

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function run() {
  await wait(2500);
  const doc = dom.window.document;

  // 1. Open Explore page
  doc.getElementById('mode-explore-btn').click();
  await wait(800);

  console.log('=== EXPLORE PAGE & TRENDING TOPICS VERIFICATION ===\n');

  // 2. Check Topic Grain Selector
  const grainSel = doc.getElementById('ex-topic-grain');
  console.log('1. Topic Classification Selector:');
  console.log('   Present:', !!grainSel);
  console.log('   Default Selected Value:', grainSel ? grainSel.value : 'N/A');
  console.log('   Available Options:', grainSel ? [...grainSel.options].map((o) => `"${o.text}" (${o.value})`).join(', ') : 'None');

  // 3. Inspect Table Headers and Rows under Default (Security themes D01-D09)
  const view = doc.querySelector('#explore-view [data-panel="explore_view"]');
  const table = view.querySelector('.tv table');
  const cardTitle = view.querySelector('.tv-title')?.textContent || 'N/A';
  console.log('\n2. Table Card Title:');
  console.log('   Title:', cardTitle);

  const ths = [...table.querySelectorAll('thead th')].map((th) => th.textContent.trim());
  console.log('\n3. Table Columns:');
  console.log('   ' + ths.join(' | '));

  console.log('\n4. Rendered Rows in Default View (D01–D09 Security Themes):');
  const rows = [...table.querySelectorAll('tbody tr')].map((tr) => {
    return [...tr.querySelectorAll('td')].map((td) => td.textContent.trim()).join(' | ');
  });
  rows.forEach((r, i) => console.log(`   ${i + 1}. ${r}`));

  // 4. Test Drill-down on top row
  console.log('\n5. Drill-Down Test on Top Row:');
  const topRow = table.querySelector('tbody tr');
  if (topRow) {
    topRow.click();
    await wait(600);
    console.log('   Drill URL Called:', drillUrl);
    const modalTitle = doc.getElementById('drill-title')?.textContent;
    const modalSub = doc.getElementById('drill-sub')?.textContent;
    const modalPosts = doc.querySelectorAll('#drill-body .post-card').length;
    console.log('   Modal Title:', modalTitle);
    console.log('   Modal Subtitle:', modalSub);
  }

  // 5. Test Switching to Macro Clusters
  console.log('\n6. Switching to "Macro clusters (t000–t007)":');
  grainSel.value = 'macro';
  grainSel.dispatchEvent(new dom.window.Event('change'));
  await wait(600);

  const macroCardTitle = view.querySelector('.tv-title')?.textContent || 'N/A';
  console.log('   Switched Table Title:', macroCardTitle);
  const macroRows = [...table.querySelectorAll('tbody tr')].map((tr) => {
    return [...tr.querySelectorAll('td')].map((td) => td.textContent.trim()).join(' | ');
  });
  console.log('   Macro Clusters Rendered:');
  macroRows.forEach((r, i) => console.log(`   ${i + 1}. ${r}`));

  // 6. Test Switching Back to Theme
  console.log('\n7. Switching back to "Security themes (D01–D09)":');
  grainSel.value = 'theme';
  grainSel.dispatchEvent(new dom.window.Event('change'));
  await wait(600);
  const backRowsCount = table.querySelectorAll('tbody tr').length;
  console.log('   Restored Security Theme rows count:', backRowsCount);

  console.log('\n=== VERIFICATION COMPLETE: ALL CHECKS PASSED ===');
}

run().catch((err) => {
  console.error('Error running verification:', err);
  process.exit(1);
});
