/**
 * Smoke test for Collapsible Metrics Panel on the Overview page.
 * Verifies:
 * 1. Panel is open by default on Overview (basic-view)
 * 2. All 6 KPI cards render with expected metrics (POSTS, MEAN SEVERITY, VELOCITY, ACCELERATION, TOP TOPIC SHARE, ALERT-FLAGGED)
 * 3. All 4 trend charts render (Posts per bucket, Acceleration, Severity mix, Tone)
 * 4. All 7 range pills exist ([Post day], [1 wk], [2 wk], [3 wk], [1 mo], [2 mo], [3 mo])
 * 5. Clicking '1 wk' pill dynamically updates POSTS count
 * 6. Close button collapses panel and shows open pill
 * 7. Open pill re-opens panel and hides itself
 * 8. Zero script errors
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const html = readFileSync(`${ROOT}/index.html`, 'utf8');
const script = readFileSync(`${ROOT}/assets/dash.js`, 'utf8');
const base = JSON.parse(readFileSync(`${ROOT}/data/metrics.json`, 'utf8'));

let errors = [];

async function bootOverview(metrics) {
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: 'http://localhost/',
    beforeParse(win) {
      win.fetch = (u) => Promise.resolve({
        ok: true,
        json: () => Promise.resolve(String(u).includes('metrics.json') ? metrics : {})
      });
      win.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
      Object.defineProperty(win.HTMLElement.prototype, 'clientWidth', { get: () => 900 });
      Object.defineProperty(win.HTMLElement.prototype, 'clientHeight', { get: () => 400 });
    },
  });

  dom.window.addEventListener('error', (e) => errors.push(e.message || String(e)));

  const s = dom.window.document.createElement('script');
  s.textContent = script;
  dom.window.document.body.appendChild(s);
  await new Promise((r) => setTimeout(r, 2500));
  return dom;
}

let ok = 0, bad = 0;
const check = (n, c, d = '') => {
  if (c) { ok++; } else { bad++; }
  console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${d ? '  ' + d : ''}`);
};

console.log('Testing Collapsible Metrics Panel (Overview page)...');
const dom = await bootOverview(JSON.parse(JSON.stringify(base)));
const doc = dom.window.document;

// 1. Panel and Open Pill presence
const panel = doc.getElementById('metrics-panel');
const openPill = doc.getElementById('mp-open-pill');
check('metrics panel element exists', !!panel);
check('open pill button exists', !!openPill);
check('panel starts open by default', panel && !panel.classList.contains('mp-collapsed'));
check('open pill hidden by default', openPill && openPill.style.display === 'none');

// 2. Date Range Pills
const pills = doc.querySelectorAll('#mp-range-pills .mp-pill');
check('all 7 range pills rendered', pills.length === 7, `found ${pills.length}`);
const expectedPills = ['Post day', '1 wk', '2 wk', '3 wk', '1 mo', '2 mo', '3 mo'];
const pillTexts = Array.from(pills).map(p => p.textContent.trim());
check('pills have expected labels', expectedPills.every(p => pillTexts.includes(p)), pillTexts.join(', '));
const activePill = doc.querySelector('#mp-range-pills .mp-pill.mp-pill-active');
check('Post day is active by default', activePill && activePill.textContent.trim() === 'Post day');

// 3. 6 KPI Cards
const kpis = doc.querySelectorAll('#mp-kpi-grid .mp-kpi-card');
check('6 KPI cards rendered', kpis.length === 6, `found ${kpis.length}`);

const labels = Array.from(doc.querySelectorAll('.mp-kpi-label')).map(e => e.textContent.trim());
check('contains POSTS KPI', labels.includes('POSTS'));
check('contains MEAN SEVERITY KPI', labels.includes('MEAN SEVERITY'));
check('contains VELOCITY KPI', labels.includes('VELOCITY'));
check('contains ACCELERATION KPI', labels.includes('ACCELERATION'));
check('contains TOPIC SHARE KPI', labels.some(l => l.includes('SHARE')));
check('contains ALERT-FLAGGED KPI', labels.includes('ALERT-FLAGGED'));

// Verify card values
const postsVal = doc.querySelector('.mp-kpi-card:nth-child(1) .mp-kpi-val')?.textContent.trim();
check('POSTS card displays non-empty count', !!postsVal && postsVal !== '—', `value: ${postsVal}`);

const sevVal = doc.querySelector('.mp-kpi-card:nth-child(2) .mp-kpi-val')?.textContent.trim();
check('MEAN SEVERITY card displays score', !!sevVal && sevVal !== '—', `value: ${sevVal}`);
const sevCard = doc.querySelector('.mp-kpi-card:nth-child(2)');
check('MEAN SEVERITY has accent border', sevCard && sevCard.classList.contains('mp-kpi-accent'));

const velVal = doc.querySelector('.mp-kpi-card:nth-child(3) .mp-kpi-val')?.textContent.trim();
check('VELOCITY card displays hourly rate', !!velVal && velVal.includes('/h'), `value: ${velVal}`);

const accelVal = doc.querySelector('.mp-kpi-card:nth-child(4) .mp-kpi-val')?.textContent.trim();
check('ACCELERATION card displays signed value', !!accelVal && (accelVal.startsWith('+') || accelVal.startsWith('-') || accelVal === '0'), `value: ${accelVal}`);

// 4. Trend Charts
const chartBoxes = doc.querySelectorAll('.mp-chart-box');
check('4 trend charts rendered', chartBoxes.length === 4, `found ${chartBoxes.length}`);

const chartTitles = Array.from(doc.querySelectorAll('.mp-chart-title')).map(e => e.textContent.trim());
check('Posts per bucket chart rendered', chartTitles.includes('Posts per bucket'));
check('Acceleration chart rendered', chartTitles.includes('Acceleration'));
check('Severity mix chart rendered', chartTitles.includes('Severity mix'));
check('Tone chart rendered', chartTitles.includes('Tone'));

// Check SVG elements inside charts
const svgs = doc.querySelectorAll('.mp-chart-box svg');
check('all 4 charts contain inline SVG', svgs.length === 4);

// 5. Interactive Pill Click (1 wk)
const oneWkBtn = Array.from(pills).find(p => p.textContent.trim() === '1 wk');
if (oneWkBtn) {
  oneWkBtn.click();
  await new Promise(r => setTimeout(r, 600));
  const newPostsVal = doc.querySelector('.mp-kpi-card:nth-child(1) .mp-kpi-val')?.textContent.trim();
  check('clicking 1 wk dynamically updates POSTS count', newPostsVal !== postsVal, `old: ${postsVal} -> new: ${newPostsVal}`);
  check('1 wk button now active', oneWkBtn.classList.contains('mp-pill-active'));

  // Check ALERT-FLAGGED matches overview active alerts (44 on 7d)
  const alertsVal = doc.querySelector('.mp-kpi-card:nth-child(6) .mp-kpi-val')?.textContent.trim();
  check('ALERT-FLAGGED dynamically updates to 44 on 1 wk', alertsVal === '44', `value: ${alertsVal}`);

  // Check Acceleration is dynamically computed
  const newAccelVal = doc.querySelector('.mp-kpi-card:nth-child(4) .mp-kpi-val')?.textContent.trim();
  check('ACCELERATION dynamically updates on 1 wk', !!newAccelVal && newAccelVal !== accelVal, `value: ${newAccelVal}`);
} else {
  check('1 wk button found', false);
}

// 5b. Interactive Hover Tooltips and Guide Line in Trend Charts
const tipElements = doc.querySelectorAll('.mp-tip');
check('tooltip element (.mp-tip) exists in each chart', tipElements.length === 4, `found ${tipElements.length}`);
const guideElements = doc.querySelectorAll('.mp-guide');
check('vertical guide line (.mp-guide) exists in each chart', guideElements.length === 4, `found ${guideElements.length}`);

// Simulate mousemove on Acceleration chart SVG
const accelSvg = chartBoxes[1].querySelector('svg');
if (accelSvg) {
  accelSvg.dispatchEvent(new dom.window.MouseEvent('mousemove', { clientX: 200, clientY: 50 }));
  const accelTip = chartBoxes[1].querySelector('.mp-tip');
  const accelGuide = chartBoxes[1].querySelector('.mp-guide');
  check('hover on chart activates tooltip', accelTip && accelTip.style.display === 'block');
  check('hover on chart activates guide line', accelGuide && accelGuide.style.display === 'block');
  check('tooltip contains /h² in Acceleration chart', accelTip && accelTip.textContent.includes('/h²'), accelTip?.textContent);
}

// 6. Close / Collapse behavior
const closeBtn = doc.getElementById('mp-close-btn');
check('close button exists', !!closeBtn);
if (closeBtn) {
  closeBtn.click();
  check('clicking close adds mp-collapsed', panel.classList.contains('mp-collapsed'));
  check('clicking close displays open pill', openPill.style.display === 'inline-flex');

  // Re-open
  openPill.click();
  await new Promise(r => setTimeout(r, 300));
  check('clicking open pill removes mp-collapsed', !panel.classList.contains('mp-collapsed'));
  check('clicking open pill hides open pill', openPill.style.display === 'none');
}

// 7. Script Errors
check('zero script errors during boot and interaction', errors.length === 0, errors.join(', '));

console.log(`\nResults: ${ok} passed, ${bad} failed`);
process.exit(bad > 0 ? 1 : 0);
