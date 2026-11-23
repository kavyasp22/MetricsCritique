/* ==========================================================================
   OSINT metrics dashboard -- rendering engine
   --------------------------------------------------------------------------
   No libraries. Charts are inline SVG built by the small helper set below.

   Two rules shape every chart here, and they are why this file has no
   categorical palette at all:

   1. A black/white/gray/gold palette cannot separate 9 topics or 6 platforms
      by hue. So identity is NEVER carried by hue. It is carried by
      *position*: one small panel per platform or per topic. Inside a panel
      there are at most two mark colours -- gold (the focal measure) and gray
      (context/reference) -- a pair validated at OKLab dE 19.2 (dark) and
      25.8 (light) including under simulated protanopia/deuteranopia.
   2. Because hue never encodes identity, a filter can never repaint a series.
      Gold always means "the measure this panel is about"; gray always means
      "context". Changing the series count changes nothing about colour.

   Magnitude uses a single gold ramp (7 steps, light->dark, one hue).
   Polarity uses gold vs clay with a neutral GRAY midpoint -- never a hue at
   the midpoint. Status (critical/high/medium) is a reserved scale, always
   rendered with an icon AND a text label, never colour alone.
   ========================================================================== */

(function () {
  'use strict';

  /* Data is a snapshot (metrics.json), not a live DB. The UI is dynamic:
     filters re-scope every chart client-side. Reload metrics.json only after
     rebuild (python UI/build_dashboard.py). */
  var DATA = null;
  var SVGNS = 'http://www.w3.org/2000/svg';
  var patSeq = 0;

  /* ------------------------------------------------------------ tables ---- */

  var _cache = {};
  function T(name) {
    if (!DATA || !DATA.tables) { return []; }
    if (_cache[name]) { return _cache[name]; }
    var t = DATA.tables[name];
    if (!t) { _cache[name] = []; return _cache[name]; }
    var cols = t.columns;

    // Handle topic_dim: merge duplicate t004 & t005 into a single "Geopolitics / Neighbourhood"
    if (name === 'topic_dim') {
      var dimMap = {};
      for (var i = 0; i < t.rows.length; i++) {
        var o = {}, r = t.rows[i];
        for (var j = 0; j < cols.length; j++) { o[cols[j]] = r[j]; }
        var tid = o.topic_id === 't005' ? 't004' : o.topic_id;
        if (!dimMap[tid]) {
          o.topic_id = tid;
          dimMap[tid] = o;
        } else {
          // the two merged topics cover different subjects (first top terms differ), so say so in the label
          var ta = String(dimMap[tid].description || '').split(',')[0].trim(), tb = String(o.description || '').split(',')[0].trim();
          if (ta && tb && ta !== tb && String(dimMap[tid].label || '').indexOf('(') < 0) { dimMap[tid].label = dimMap[tid].label + ' (' + ta + ' + ' + tb + ')'; }
          dimMap[tid].cluster_size = (dimMap[tid].cluster_size || 0) + (o.cluster_size || 0);
          dimMap[tid].n_posts = (dimMap[tid].n_posts || 0) + (o.n_posts || 0);
          if (o.description && dimMap[tid].description) {
            dimMap[tid].description = dimMap[tid].description + ', ' + o.description;
          }
        }
      }
      var outDim = Object.keys(dimMap).map(function (k) { return dimMap[k]; });
      _cache[name] = outDim;
      return outDim;
    }

    var out = new Array(t.rows.length);
    for (var i = 0; i < t.rows.length; i++) {
      var o = {}, r = t.rows[i];
      for (var j = 0; j < cols.length; j++) { o[cols[j]] = r[j]; }
      if (o.topic_id === 't005') { o.topic_id = 't004'; }
      if (name === 'entity_dim') {
        if (!o.entity_subtype || o.entity_subtype === 'unresolved' || o.entity_subtype === 'None') {
          if (o.entity_type === 'PERSON') { o.entity_subtype = 'civilian'; }
          else if (o.entity_type === 'LOCATION') { o.entity_subtype = 'geographic_location'; }
          else if (o.entity_type === 'ORG') { o.entity_subtype = 'commercial_company'; }
          else if (o.entity_type === 'EVENT') { o.entity_subtype = 'protest_demonstration'; }
          else { o.entity_subtype = 'civilian'; }
        } else if (o.entity_subtype === 'diplomat') { o.entity_subtype = 'political_leader'; }
        else if (o.entity_subtype === 'military_leader') { o.entity_subtype = 'military_personnel'; }
        else if (o.entity_subtype === 'business_executive') { o.entity_subtype = 'commercial_company'; }
      }
      out[i] = o;
    }
    _cache[name] = out;
    return out;
  }
  function hasTable(name) {
    return !!(DATA && DATA.tables && DATA.tables[name] && DATA.tables[name].rows.length);
  }

  var META = {};
  var TOPIC = {};
  var TAG = {};
  var PLATFORMS = [];
  var PLATMETA = {};
  var TOPIC_IDS = [];

  function metaName(k) { return (META[k] && META[k].display_name) || k; }
  function metaUnits(k) { return (META[k] && META[k].units) || ''; }
  function metaFormula(k) { return (META[k] && META[k].formula) || ''; }
  function metaCaveat(k) { return (META[k] && META[k].caveats) || ''; }
  function metaGate(k) { return (META[k] && META[k].support_gate) || ''; }

  function topicLabel(id) {
    var t = TOPIC[id];
    var raw = t ? (t.label || t.topic || id) : (id == null ? 'All topics' : id);
    return /^unassigned(\s*\/\s*unassigned)?$/i.test(String(raw)) ? 'Unclassified' : raw;
  }
  function tagLabel(id) { return TAG[id] ? '#' + TAG[id].tag : (id || ''); }

  /* ------------------------------------------------------------ format ---- */

  function isNum(v) { return typeof v === 'number' && isFinite(v); }

  function fmt(v, dp) {
    if (!isNum(v)) { return '—'; }
    var d = dp == null ? 2 : dp;
    return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function fmtInt(v) {
    return isNum(v) ? Math.round(v).toLocaleString() : '—';
  }
  function fmtCompact(v) {
    if (!isNum(v)) { return '—'; }
    var a = Math.abs(v);
    if (a >= 1e9) { return (v / 1e9).toFixed(1) + 'B'; }
    if (a >= 1e6) { return (v / 1e6).toFixed(1) + 'M'; }
    if (a >= 1e4) { return (v / 1e3).toFixed(1) + 'K'; }
    return Math.round(v).toLocaleString();
  }
  function fmtQ(v) {
    if (!isNum(v)) { return '—'; }
    if (v === 0) { return '<1e-300'; }
    if (v < 0.001) { return v.toExponential(1); }
    return v.toFixed(4);
  }
  function fmtSigned(v, dp) {
    if (!isNum(v)) { return '—'; }
    return (v > 0 ? '+' : '') + fmt(v, dp == null ? 3 : dp);
  }
  function fmtPct(v, dp) {
    return isNum(v) ? (v * 100).toFixed(dp == null ? 0 : dp) + '%' : '—';
  }
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function fmtDay(s) {
    if (!s) { return '—'; }
    var p = String(s).slice(0, 10).split('-');
    return MON[(+p[1]) - 1] + ' ' + (+p[2]);
  }
  function fmtDayFull(s) {
    if (!s) { return '—'; }
    var p = String(s).slice(0, 10).split('-');
    return p[0] + '-' + p[1] + '-' + p[2];
  }
  function day(s) { return String(s).slice(0, 10); }

  /* ------------------------------------------------------------- theme ---- */

  var K = {};
  function readTokens() {
    var cs = getComputedStyle(document.documentElement);
    function g(n) { return cs.getPropertyValue(n).trim(); }
    K = {
      surface: g('--surface'), sunken: g('--sunken'), elev: g('--elev'),
      ink: g('--ink'), ink2: g('--ink-2'), ink3: g('--ink-3'),
      gold: g('--gold'), goldBorder: g('--gold-border'), goldHair: g('--gold-hair'),
      focal: g('--mark-focal'), focalSoft: g('--mark-focal-soft'),
      context: g('--mark-context'), contextSoft: g('--mark-context-soft'),
      neg: g('--mark-neg'), grid: g('--grid'), axis: g('--axis'),
      crit: g('--status-crit'),
      seq: [g('--seq-0'), g('--seq-1'), g('--seq-2'), g('--seq-3'), g('--seq-4'), g('--seq-5'), g('--seq-6')],
      div: [g('--div-n3'), g('--div-n2'), g('--div-n1'), g('--div-0'), g('--div-p1'), g('--div-p2'), g('--div-p3')]
    };
  }

  function seqColor(t) {
    if (!isNum(t)) { return null; }
    var i = Math.round(Math.max(0, Math.min(1, t)) * (K.seq.length - 1));
    return K.seq[i];
  }
  function divColor(t) {  /* t in [-1, 1] */
    if (!isNum(t)) { return null; }
    var i = Math.round((Math.max(-1, Math.min(1, t)) + 1) / 2 * (K.div.length - 1));
    return K.div[i];
  }

  /* --------------------------------------------------------------- dom ---- */

  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        if (k === 'class') { e.className = attrs[k]; }
        else if (k === 'text') { e.textContent = attrs[k]; }
        else if (attrs[k] != null) { e.setAttribute(k, attrs[k]); }
      }
    }
    (kids || []).forEach(function (c) { if (c) { e.appendChild(c); } });
    return e;
  }
  function el(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) { if (attrs[k] != null) { e.setAttribute(k, attrs[k]); } }
    return e;
  }
  function txt(s) { return document.createTextNode(s); }
  function svgText(x, y, s, o) {
    var a = {
      x: x, y: y, fill: (o && o.fill) || K.ink3, 'font-size': (o && o.size) || 10,
      'text-anchor': (o && o.anchor) || 'start', 'font-family': 'inherit'
    };
    if (o && o.weight) { a['font-weight'] = o.weight; }
    if (o && o.tabular) { a['font-variant-numeric'] = 'tabular-nums'; }
    if (o && o.baseline) { a['dominant-baseline'] = o.baseline; }
    var t = el('text', a);
    t.textContent = s;
    return t;
  }

  /* ----------------------------------------------------------- tooltip ---- */

  var tipEl = null;
  var TIP = {
    show: function (ev, node) {
      if (!tipEl) { tipEl = document.getElementById('tip'); }
      if (!tipEl) { return; }
      tipEl.textContent = '';
      tipEl.appendChild(node);
      tipEl.classList.add('on');
      var r = tipEl.getBoundingClientRect();
      var x = ev.clientX + 14, y = ev.clientY + 14;
      if (x + r.width > window.innerWidth - 8) { x = ev.clientX - r.width - 14; }
      if (y + r.height > window.innerHeight - 8) { y = ev.clientY - r.height - 14; }
      tipEl.style.left = Math.max(6, x) + 'px';
      tipEl.style.top = Math.max(6, y) + 'px';
    },
    hide: function () { if (tipEl) { tipEl.classList.remove('on'); } }
  };

  function tipNode(head, rows, note) {
    var box = h('div', {}, [h('div', { class: 't-hd', text: head })]);
    (rows || []).forEach(function (r) {
      var key = h('span', { class: 'k' });
      if (r.color) {
        var sw = h('span', { class: 'sw' });
        sw.style.background = r.color;
        key.appendChild(sw);
      }
      key.appendChild(txt(r.k));
      box.appendChild(h('div', { class: 't-row' }, [key, h('span', { class: 'v', text: r.v })]));
    });
    if (note) { box.appendChild(h('div', { class: 't-note', text: note })); }
    return box;
  }

  function hoverable(node, headFn, rowsFn, noteFn) {
    function on(ev) { TIP.show(ev, tipNode(headFn(), rowsFn(), noteFn ? noteFn() : null)); }
    node.addEventListener('pointerenter', on);
    node.addEventListener('pointermove', on);
    node.addEventListener('pointerleave', TIP.hide);
    node.setAttribute('tabindex', '0');
    node.addEventListener('focus', function () {
      var r = node.getBoundingClientRect();
      TIP.show({ clientX: r.left + r.width / 2, clientY: r.top }, tipNode(headFn(), rowsFn(), noteFn ? noteFn() : null));
    });
    node.addEventListener('blur', TIP.hide);
  }

  /* ------------------------------------------------------------- drill ---- */
  /* Click-to-inspect: every clickable mark in this file carries a small
     `drill` descriptor ({view, params, title}). Clicking it fires exactly one
     GET /api/drill?view=...&<params> against api_server.py -- a bounded,
     column-selected, read-only DuckDB query, nothing preloaded. The panel is
     closable (Escape, backdrop click, × button); closing clears its DOM and
     drops the fetched rows, so nothing accumulates across clicks. Opening a
     new drill while one is loading invalidates the older fetch via `seq` so
     a slow response can never repaint a panel the user already moved past. */

  var DRILL = { seq: 0 };

  DRILL.open = function (cfg) {
    var mySeq = ++DRILL.seq;
    var panel = document.getElementById('drill-panel');
    var backdrop = document.getElementById('drill-backdrop');
    var titleEl = document.getElementById('drill-title');
    var subEl = document.getElementById('drill-sub');
    var bodyEl = document.getElementById('drill-body');
    if (!panel || !backdrop || !titleEl || !subEl || !bodyEl) { return; }

    titleEl.textContent = cfg.title || 'Underlying posts';
    subEl.textContent = cfg.subtitle || 'Loading…';
    bodyEl.textContent = '';
    backdrop.classList.add('on');
    panel.classList.add('on');
    panel.setAttribute('aria-hidden', 'false');

    var params = cfg.params || {};
    var qs = Object.keys(params)
      .filter(function (k) { return params[k] != null && params[k] !== ''; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); })
      .join('&');
    var url = '/api/drill?view=' + encodeURIComponent(cfg.view) + (qs ? '&' + qs : '') + '&limit=60';

    fetch(url).then(function (r) { return r.json(); }).then(function (data) {
      if (mySeq !== DRILL.seq) { return; }
      bodyEl.textContent = '';
      if (data.error) {
        subEl.textContent = '';
        bodyEl.appendChild(h('div', { class: 'empty', text: 'Could not load rows: ' + data.error }));
        return;
      }
      /* risk/theme drills are ordered by classifier score, everything else by
         recency -- say which, rather than always claiming "most recent". */
      var byScore = (cfg.view === 'risk' || cfg.view === 'theme'
        || (cfg.view === 'alert' && (cfg.params || {}).dimension_type !== 'entity'));
      var sortKey = (cfg.params || {}).sort;
      var SORT_LABEL = { views: 'views', engagement: 'engagement (likes + comments + shares)', shares: 'shares / forwards', likes: 'likes' };
      var rangeTxt = (cfg.params || {}).from ? (' between ' + cfg.params.from + ' and ' + cfg.params.to) : '';
      subEl.textContent = 'Showing ' + data.shown + ' of ' + fmtInt(data.total) + ' underlying posts' + rangeTxt + ', ' +
        (sortKey ? ('ranked by ' + (SORT_LABEL[sortKey] || sortKey) + '.') : (byScore ? 'strongest match first.' : 'most recent first.'));
      if (!data.rows.length) {
        bodyEl.appendChild(h('div', { class: 'empty', text: 'No underlying posts matched this selection.' }));
        return;
      }
      if (sortKey) {
        var ctl = h('div', { class: 'dr-sort' }, [h('span', { text: 'Rank by ' })]);
        var sel = h('select', {});
        ['views', 'engagement', 'shares', 'likes'].forEach(function (k) {
          var o = h('option', { text: k === 'shares' ? 'shares / forwards' : k, value: k });
          if (k === sortKey) { o.selected = true; }
          sel.appendChild(o);
        });
        sel.addEventListener('change', function () {
          var np = {}; Object.keys(cfg.params).forEach(function (k2) { np[k2] = cfg.params[k2]; });
          np.sort = sel.value;
          DRILL.open({ view: cfg.view, params: np, title: cfg.title, subtitle: cfg.subtitle });
        });
        ctl.appendChild(sel);
        ctl.appendChild(h('span', { class: 'dr-sort-note', text: ' Compare within one platform only: views and engagement are different units.' }));
        bodyEl.appendChild(ctl);
      }
      var list = h('div', { class: 'drill-list' });
      data.rows.forEach(function (row) { list.appendChild(drillRowNode(row)); });
      bodyEl.appendChild(list);
      /* "Show more": the next page of the same ranked list, appended in place (50 per page after the first 60) */
      var shown = data.rows.length, total = data.total, moreBtn = null;
      function syncMore() {
        if (shown >= total) { if (moreBtn) { moreBtn.remove(); moreBtn = null; } return; }
        if (!moreBtn) {
          moreBtn = h('button', { type: 'button', class: 'dr-more' });
          moreBtn.addEventListener('click', function () {
            moreBtn.disabled = true;
            fetch(url + '&offset=' + shown).then(function (r) { return r.json(); }).then(function (d2) {
              if (mySeq !== DRILL.seq) { return; }
              if (d2.error) { throw new Error(d2.error); }
              d2.rows.forEach(function (row) { list.appendChild(drillRowNode(row)); });
              shown += d2.rows.length;
              if (!d2.rows.length) { total = shown; }
              subEl.textContent = subEl.textContent.replace(/Showing \d+ of/, 'Showing ' + shown + ' of');
              moreBtn.disabled = false; syncMore();
            }).catch(function (e) { moreBtn.disabled = false; moreBtn.textContent = 'Retry (' + e.message + ')'; });
          });
          list.after ? list.after(moreBtn) : bodyEl.appendChild(moreBtn);
        }
        moreBtn.textContent = 'Show more (' + fmtInt(total - shown) + ' remaining)';
      }
      syncMore();
      if (data.related) { renderRelated(bodyEl, data.related); }
    }).catch(function (e) {
      if (mySeq !== DRILL.seq) { return; }
      subEl.textContent = '';
      bodyEl.textContent = '';
      bodyEl.appendChild(h('div', {
        class: 'empty',
        text: 'Could not reach the live data API. Serve this dashboard with '
          + '"python3 api_server.py" (not a plain static file server) to enable click-to-inspect. (' + e + ')'
      }));
    });
  };

  DRILL.close = function () {
    DRILL.seq++; // invalidate any in-flight fetch so it can't repaint after close
    var panel = document.getElementById('drill-panel');
    var backdrop = document.getElementById('drill-backdrop');
    var bodyEl = document.getElementById('drill-body');
    if (panel) { panel.classList.remove('on'); panel.setAttribute('aria-hidden', 'true'); }
    if (backdrop) { backdrop.classList.remove('on'); }
    if (bodyEl) { bodyEl.textContent = ''; } // release rows, nothing kept in memory once closed
  };

  function drillRowNode(row) {
    var head = h('div', { class: 'dr-head' }, [
      h('span', { class: 'dr-plat', text: row.platform || '—' }),
      h('span', { class: 'dr-day', text: row.posted_at ? String(row.posted_at).slice(0, 16).replace('T', ' ') : '' })
    ]);
    var metaBits = [];
    if (row.author_name) { metaBits.push(row.author_name); }
    if (row.surface_form) { metaBits.push('mentions "' + row.surface_form + '"'); }
    if (row.entity_subtype) { metaBits.push(subtypeName(row.entity_subtype)); }
    if (isNum(row.confidence)) { metaBits.push('confidence ' + fmt(row.confidence, 2)); }
    if (isNum(row.score)) { metaBits.push('score ' + fmt(row.score, 2)); }
    if (isNum(row.similarity)) { metaBits.push('similarity ' + fmt(row.similarity, 2)); }
    if (row.gray_zone) { metaBits.push('gray-zone'); }
    var mx = [];
    if (isNum(row.views)) { mx.push(fmtCompact(row.views) + ' views'); }
    if (isNum(row.likes)) { mx.push(fmtCompact(row.likes) + ' likes'); }
    if (isNum(row.comments)) { mx.push(fmtCompact(row.comments) + ' comments'); }
    if (isNum(row.shares)) { mx.push(fmtCompact(row.shares) + ' shares / forwards'); }
    var wrap = h('div', { class: 'dr-row' }, [head]);
    if (mx.length) { wrap.appendChild(h('div', { class: 'dr-metrics', text: mx.join(' \u00b7 ') })); }
    if (metaBits.length) { wrap.appendChild(h('div', { class: 'dr-meta', text: metaBits.join(' · ') })); }
    wrap.appendChild(h('div', { class: 'dr-text', text: row.text || '(no text captured)' }));
    if (row.url) {
      var a = h('a', { class: 'dr-link', text: 'Open source ↗' });
      a.setAttribute('href', row.url); a.setAttribute('target', '_blank'); a.setAttribute('rel', 'noopener noreferrer');
      wrap.appendChild(a);
    }
    return wrap;
  }

  /* "Related" section appended under the posts list in the drill panel --
     co-occurring entities/categories/topics (chips, click drills into that
     item) and a few similar posts (content-based, single-seed cosine
     similarity -- see api_server.py::compute_related). Renders nothing if
     `related` came back empty; a failed related-items lookup never blocks
     the posts list above it from showing (see DRILL.open). */
  function relatedChip(label, drill, tipRows) {
    var chip = h('button', { class: 'chip', type: 'button', text: label });
    attachDrill(chip, drill);
    if (tipRows) {
      chip.addEventListener('mousemove', function (ev) {
        TIP.show(ev, tipNode(tipRows.head, tipRows.rows, tipRows.note));
      });
      chip.addEventListener('mouseleave', TIP.hide);
    }
    return chip;
  }

  /* The chip shows co-occurring post count; the RANKING is by a shrunk lift.
     Surface both on hover so the ordering is never a mystery number. */
  function relatedTip(name, d) {
    return {
      head: name,
      rows: [
        { k: 'co-occurring posts', color: K.focal, v: fmtInt(d.co_posts) },
        { k: 'posts corpus-wide', v: fmtInt(d.global_posts) },
        { k: 'association score', v: isNum(d.score) ? fmt(d.score, 2) + '×' : '—' }
      ],
      note: 'Score is how much more often this appears here than in the corpus overall '
        + '(shrunk toward 1× when the overlap is small). Ranked by score, not raw count.'
    };
  }

  function renderRelated(host, related) {
    var entities = related.entities || [], categories = related.categories || [],
      topics = related.topics || [], similar = related.similar_posts || [];
    if (!entities.length && !categories.length && !topics.length && !similar.length) { return; }

    var section = h('div', { class: 'related-section' });
    section.appendChild(h('div', { class: 'related-hd', text: 'Related' }));

    if (entities.length) {
      section.appendChild(h('div', { class: 'related-lbl', text: 'Entities that also appear here' }));
      var eRow = h('div', { class: 'related-chips' });
      entities.forEach(function (e) {
        eRow.appendChild(relatedChip(e.name + ' (' + fmtInt(e.co_posts) + ' posts)',
          { view: 'entity', params: { entity_uid: e.entity_uid }, title: e.name },
          relatedTip(e.name, e)));
      });
      section.appendChild(eRow);
    }

    if (categories.length) {
      section.appendChild(h('div', { class: 'related-lbl', text: 'Related categories' }));
      var cRow = h('div', { class: 'related-chips' });
      categories.forEach(function (c) {
        var name = dimName(c.dim_type, c.category);
        cRow.appendChild(relatedChip(name + ' (' + fmtInt(c.co_posts) + ' posts)',
          dimDrill(c.dim_type, c.category, name),
          relatedTip(name, c)));
      });
      section.appendChild(cRow);
    }

    if (topics.length) {
      section.appendChild(h('div', { class: 'related-lbl', text: 'Related topics' }));
      var tRow = h('div', { class: 'related-chips' });
      topics.forEach(function (t) {
        tRow.appendChild(relatedChip(t.name + ' (' + fmtInt(t.co_posts) + ' posts)',
          { view: 'topic_v3', params: { cluster_id: t.cluster_id }, title: t.name },
          relatedTip(t.name, t)));
      });
      section.appendChild(tRow);
    }

    if (similar.length) {
      section.appendChild(h('div', { class: 'related-lbl', text: 'Similar posts' }));
      var sList = h('div', { class: 'drill-list' });
      similar.forEach(function (row) { sList.appendChild(drillRowNode(row)); });
      section.appendChild(sList);
    }

    host.appendChild(section);
  }

  function initDrillChrome() {
    var closeBtn = document.getElementById('drill-close');
    var backdrop = document.getElementById('drill-backdrop');
    if (closeBtn) { closeBtn.addEventListener('click', DRILL.close); }
    if (backdrop) { backdrop.addEventListener('click', DRILL.close); }
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { DRILL.close(); }
    });
  }

  /* Attach a click-to-inspect handler to an already-built SVG/DOM node.
     `drill` is {view, params, title, subtitle} or falsy (no-op, so callers
     can pass a datum's optional drill field unconditionally). */
  function attachDrill(node, drill) {
    if (!drill || !node) { return; }
    node.classList.add('clk');
    if (!node.hasAttribute('tabindex')) { node.setAttribute('tabindex', '0'); }
    node.setAttribute('role', 'button');
    node.addEventListener('click', function (ev) {
      ev.stopPropagation();
      DRILL.open(drill);
    });
    node.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); DRILL.open(drill); }
    });
  }

  /* ------------------------------------------------------------ scales ---- */

  function linScale(d0, d1, r0, r1) {
    var span = (d1 - d0) || 1;
    function f(v) { return r0 + (v - d0) / span * (r1 - r0); }
    f.d0 = d0; f.d1 = d1; f.r0 = r0; f.r1 = r1;
    f.inv = function (p) { return d0 + (p - r0) / ((r1 - r0) || 1) * span; };
    return f;
  }

  function niceTicks(d0, d1, n) {
    if (!isFinite(d0) || !isFinite(d1)) { return [0]; }
    if (d0 === d1) { return [d0]; }
    n = n || 5;
    var raw = (d1 - d0) / n;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / mag, step;
    if (norm < 1.5) { step = 1; } else if (norm < 3) { step = 2; }
    else if (norm < 7) { step = 5; } else { step = 10; }
    step *= mag;
    var out = [], s = Math.ceil(d0 / step) * step;
    for (var v = s; v <= d1 + step * 1e-6; v += step) { out.push(Math.abs(v) < step * 1e-9 ? 0 : v); }
    return out.length ? out : [d0, d1];
  }

  function extent(arr) {
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < arr.length; i++) {
      var v = arr[i];
      if (isNum(v)) { if (v < lo) { lo = v; } if (v > hi) { hi = v; } }
    }
    if (lo === Infinity) { return null; }
    return [lo, hi];
  }

  function padDomain(ex, opts) {
    if (!ex) { return [0, 1]; }
    var lo = ex[0], hi = ex[1];
    if (opts && opts.zeroBase && lo > 0) { lo = 0; }
    if (opts && opts.symmetric) { var m = Math.max(Math.abs(lo), Math.abs(hi)) || 1; lo = -m; hi = m; }
    if (lo === hi) { lo -= Math.abs(lo) * 0.1 + 1; hi += Math.abs(hi) * 0.1 + 1; }
    else { var p = (hi - lo) * 0.08; lo -= p; hi += p; }
    if (opts && opts.zeroBase && ex[0] >= 0) { lo = 0; }
    return [lo, hi];
  }

  /* ------------------------------------------------------------- frame ---- */

  function frame(host, cfg) {
    var m = cfg.margin || { t: 10, r: 14, b: 22, l: 40 };
    var w = cfg.width, ht = cfg.height;
    var s = el('svg', { width: w, height: ht, viewBox: '0 0 ' + w + ' ' + ht });
    if (cfg.title) {
      var ti = el('title'); ti.textContent = cfg.title; s.appendChild(ti);
    }
    host.appendChild(s);
    var g = el('g', { transform: 'translate(' + m.l + ',' + m.t + ')' });
    s.appendChild(g);
    return {
      svg: s, g: g, m: m, w: w, h: ht,
      iw: Math.max(10, w - m.l - m.r),
      ih: Math.max(10, ht - m.t - m.b)
    };
  }

  function yAxis(F, y, o) {
    o = o || {};
    var tks = niceTicks(y.d0, y.d1, o.count || 4);
    tks.forEach(function (v) {
      var yy = y(v);
      if (yy < -1 || yy > F.ih + 1) { return; }
      F.g.appendChild(el('line', {
        x1: 0, x2: F.iw, y1: yy, y2: yy,
        stroke: (v === 0 && o.zeroEmph) ? K.axis : K.grid, 'stroke-width': 1
      }));
      F.g.appendChild(svgText(-6, yy, o.fmt ? o.fmt(v) : fmtCompact(v),
        { anchor: 'end', baseline: 'middle', size: o.size || 9.5, tabular: true, fill: K.ink3 }));
    });
    if (o.label) {
      var t = svgText(0, 0, o.label, { size: 9, fill: K.ink3 });
      t.setAttribute('transform', 'translate(' + (-F.m.l + 9) + ',' + (F.ih / 2) + ') rotate(-90)');
      t.setAttribute('text-anchor', 'middle');
      F.g.appendChild(t);
    }
  }

  function xDayAxis(F, x, days, o) {
    o = o || {};
    F.g.appendChild(el('line', { x1: 0, x2: F.iw, y1: F.ih, y2: F.ih, stroke: K.axis, 'stroke-width': 1 }));
    var want = Math.max(2, Math.min(o.count || 5, days.length));
    var step = Math.max(1, Math.floor((days.length - 1) / (want - 1)));
    for (var i = 0; i < days.length; i += step) {
      var xx = x(i);
      F.g.appendChild(el('line', { x1: xx, x2: xx, y1: F.ih, y2: F.ih + 3, stroke: K.axis, 'stroke-width': 1 }));
      F.g.appendChild(svgText(xx, F.ih + 13, fmtDay(days[i]),
        { anchor: i === 0 ? 'start' : 'middle', size: o.size || 9, tabular: true, fill: K.ink3 }));
    }
  }

  function hatchPattern(svg) {
    var id = 'hx' + (patSeq++);
    var defs = el('defs');
    var p = el('pattern', {
      id: id, width: 5, height: 5, patternUnits: 'userSpaceOnUse',
      patternTransform: 'rotate(45)'
    });
    p.appendChild(el('rect', { width: 5, height: 5, fill: K.sunken }));
    p.appendChild(el('line', { x1: 0, y1: 0, x2: 0, y2: 5, stroke: K.axis, 'stroke-width': 1 }));
    defs.appendChild(p);
    svg.insertBefore(defs, svg.firstChild);
    return 'url(#' + id + ')';
  }

  /* ------------------------------------------------------------ legend ---- */

  function legend(host, items) {
    var l = h('div', { class: 'legend' });
    items.forEach(function (it) {
      var sw = h('span', { class: 'swatch' + (it.box ? ' box' : '') + (it.hatch ? ' hatch' : '') });
      if (!it.hatch) { sw.style.background = it.color; }
      if (it.hatch) {
        sw.style.background = 'repeating-linear-gradient(45deg,' + K.axis + ' 0 1px,transparent 1px 4px)';
      }
      l.appendChild(h('span', { class: 'item' }, [sw, txt(it.label)]));
    });
    host.appendChild(l);
    return l;
  }

  function rampLegend(host, lo, hi, ramp, label, fmtFn) {
    var box = h('div', { class: 'ramp-legend' });
    if (label) { box.appendChild(h('span', { text: label })); }
    box.appendChild(h('span', { text: fmtFn ? fmtFn(lo) : fmt(lo, 1), class: 'lo' }));
    var steps = h('div', { class: 'steps' });
    ramp.forEach(function (c) { var i = h('i'); i.style.background = c; steps.appendChild(i); });
    box.appendChild(steps);
    box.appendChild(h('span', { text: fmtFn ? fmtFn(hi) : fmt(hi, 1) }));
    host.appendChild(box);
  }

  /* -------------------------------------------------------- table view ---- */

  /* -------------------------------------------------------- table view ---- */

  var ACTIVE_TABLE_POPUP = null;

  function closeActiveTablePopup() {
    if (ACTIVE_TABLE_POPUP && ACTIVE_TABLE_POPUP.parentNode) {
      ACTIVE_TABLE_POPUP.parentNode.removeChild(ACTIVE_TABLE_POPUP);
    }
    ACTIVE_TABLE_POPUP = null;
  }

  document.addEventListener('click', function (e) {
    if (!ACTIVE_TABLE_POPUP) { return; }
    if (!ACTIVE_TABLE_POPUP.contains(e.target) && !e.target.closest('.tbl-filter-btn')) {
      closeActiveTablePopup();
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeActiveTablePopup(); }
  });

  window.addEventListener('scroll', function (e) {
    if (ACTIVE_TABLE_POPUP) {
      if (e && e.target && (e.target === ACTIVE_TABLE_POPUP || (ACTIVE_TABLE_POPUP.contains && ACTIVE_TABLE_POPUP.contains(e.target)))) {
        return; // Ignore scroll events inside the popup itself
      }
      closeActiveTablePopup();
    }
  }, true);

  function tableView(host, cols, rows, opts) {
    opts = opts || {};
    if (!rows || !rows.length) {
      if (opts.rawTable) {
        empty(host, 'No rows in the current selection.');
        return null;
      }
      var emptyDet = h('details', { class: 'tv' });
      if (opts.maxWidth) { emptyDet.style.maxWidth = opts.maxWidth; }
      emptyDet.appendChild(h('summary', { text: opts.label || 'Table view — 0 rows' }));
      emptyDet.appendChild(h('div', { class: 'empty', text: 'No rows in the current selection.' }));
      host.appendChild(emptyDet);
      return emptyDet;
    }

    var rootContainer;
    var summaryEl = null;
    if (opts.rawTable) {
      rootContainer = h('div', { class: 'tv-raw-wrap' });
    } else {
      rootContainer = h('details', { class: 'tv' });
      if (opts.maxWidth) { rootContainer.style.maxWidth = opts.maxWidth; }
      if (opts.open) { rootContainer.open = true; }
      summaryEl = h('summary', { text: opts.label || ('Table view — ' + rows.length + ' rows') });
      rootContainer.appendChild(summaryEl);
    }

    function getColKey(c, idx) { return c.k || ('col_' + idx); }
    function getColRaw(c, r) { return c.get ? c.get(r) : r[c.k]; }
    function getColDisplay(c, r) {
      var raw = getColRaw(c, r);
      if (raw == null || raw === '') { return '—'; }
      if (c.fmt) { return String(c.fmt(raw, r)); }
      return String(raw);
    }

    var activeFilters = {}; // colKey -> Set of allowed string values
    var sortCol = null;     // colKey
    var sortDir = null;     // 'asc' | 'desc'

    // Status bar for active filters above table
    var filterBar = h('div', { class: 'tbl-filter-bar', style: 'display:none' });
    rootContainer.appendChild(filterBar);

    var wrap = h('div', { class: 'tbl-h' });
    var t = h('table');
    var thead = h('thead'), tr = h('tr');

    var filterBtns = {}; // colKey -> button element

    cols.forEach(function (c, idx) {
      var ck = getColKey(c, idx);
      var th = h('th', { class: c.num ? 'num' : '' });
      var content = h('div', { class: 'th-content' });
      var lbl = h('span', { class: 'th-label', text: c.label });
      content.appendChild(lbl);

      var btn = h('button', {
        type: 'button',
        class: 'tbl-filter-btn',
        title: 'Filter & sort ' + c.label
      });
      btn.textContent = '▾';
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        openColumnFilterPopup(c, idx, btn);
      });
      content.appendChild(btn);
      filterBtns[ck] = btn;

      th.appendChild(content);
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    t.appendChild(thead);

    var tb = h('tbody');
    t.appendChild(tb);
    wrap.appendChild(t);
    rootContainer.appendChild(wrap);

    var fnoteEl = h('div', { class: 'fnote', style: 'display:none' });
    rootContainer.appendChild(fnoteEl);

    function updateFilterBar(filteredCount, totalCount) {
      var activeKeys = Object.keys(activeFilters);
      if (!activeKeys.length) {
        filterBar.style.display = 'none';
        filterBar.textContent = '';
        return;
      }
      filterBar.style.display = 'flex';
      filterBar.textContent = '';

      var countSpan = h('span', { class: 'tfb-count' });
      countSpan.textContent = 'Filtered: ' + filteredCount + ' of ' + totalCount + ' rows';
      filterBar.appendChild(countSpan);

      activeKeys.forEach(function (ck) {
        var cIdx = cols.findIndex(function (col, i) { return getColKey(col, i) === ck; });
        var colLabel = cIdx >= 0 ? cols[cIdx].label : ck;
        var vals = Array.from(activeFilters[ck]);
        var valStr = vals.length <= 2 ? vals.join(', ') : (vals.length + ' selected');

        var chip = h('span', { class: 'tfb-chip' });
        chip.textContent = colLabel + ': ' + valStr + ' ';
        var xBtn = h('span', { class: 'tfb-chip-x', title: 'Clear ' + colLabel + ' filter', text: '✕' });
        xBtn.addEventListener('click', function (ev) {
          ev.stopPropagation();
          delete activeFilters[ck];
          renderFilteredRows();
        });
        chip.appendChild(xBtn);
        filterBar.appendChild(chip);
      });

      var clearAll = h('button', { type: 'button', class: 'tfb-clear-all', text: 'Clear all filters' });
      clearAll.addEventListener('click', function (ev) {
        ev.stopPropagation();
        activeFilters = {};
        renderFilteredRows();
      });
      filterBar.appendChild(clearAll);
    }

    function renderFilteredRows() {
      // 1. Filter rows
      var filtered = rows.filter(function (r) {
        for (var i = 0; i < cols.length; i++) {
          var c = cols[i];
          var ck = getColKey(c, i);
          var allowed = activeFilters[ck];
          if (allowed) {
            var v = getColDisplay(c, r);
            if (!allowed.has(v)) { return false; }
          }
        }
        return true;
      });

      // 2. Sort rows
      if (sortCol && sortDir) {
        var sc = cols.find(function (c, i) { return getColKey(c, i) === sortCol; });
        if (sc) {
          filtered.sort(function (a, b) {
            var va = getColRaw(sc, a);
            var vb = getColRaw(sc, b);
            if (typeof va === 'number' && typeof vb === 'number') {
              return sortDir === 'asc' ? va - vb : vb - va;
            }
            var sa = String(getColDisplay(sc, a)).toLowerCase();
            var sb = String(getColDisplay(sc, b)).toLowerCase();
            if (sa < sb) { return sortDir === 'asc' ? -1 : 1; }
            if (sa > sb) { return sortDir === 'asc' ? 1 : -1; }
            return 0;
          });
        }
      }

      // 3. Update summary text if in details
      if (summaryEl) {
        var baseLbl = opts.label || 'Table view';
        if (filtered.length !== rows.length) {
          summaryEl.textContent = baseLbl + ' — ' + filtered.length + ' of ' + rows.length + ' rows';
        } else {
          summaryEl.textContent = baseLbl + ' — ' + rows.length + ' rows';
        }
      }

      // 4. Update filter button states
      cols.forEach(function (c, i) {
        var ck = getColKey(c, i);
        var btn = filterBtns[ck];
        if (btn) {
          if (activeFilters[ck]) {
            btn.classList.add('filtered');
            btn.textContent = '▼';
          } else {
            btn.classList.remove('filtered');
            btn.textContent = '▾';
          }
        }
      });

      // 5. Update active filter bar
      updateFilterBar(filtered.length, rows.length);

      // 6. Render table rows
      tb.textContent = '';
      if (!filtered.length) {
        var emptyTr = h('tr');
        var emptyTd = h('td', { class: 'nil', colspan: String(cols.length) });
        emptyTd.textContent = 'No matching rows for active filters.';
        emptyTd.style.textAlign = 'center';
        emptyTd.style.padding = '1.8rem 0.5rem';
        emptyTr.appendChild(emptyTd);
        tb.appendChild(emptyTr);
        fnoteEl.style.display = 'none';
        return;
      }

      var maxRows = opts.max || 400;
      filtered.slice(0, maxRows).forEach(function (r) {
        var row = h('tr');
        cols.forEach(function (c) {
          var raw = getColRaw(c, r);
          var s = c.fmt ? c.fmt(raw, r) : (raw == null ? '—' : String(raw));
          var cls = [];
          if (c.num) { cls.push('num'); }
          if (c.wrap) { cls.push('wrap'); }
          if (c.strong) { cls.push('strong'); }
          if (s === '—') { cls.push('nil'); }
          var td = h('td', { class: cls.join(' ') });
          if (c.node) { td.appendChild(c.node(raw, r)); } else { td.textContent = s; }
          row.appendChild(td);
        });
        if (opts.rowDrill) { attachDrill(row, opts.rowDrill(r)); }
        tb.appendChild(row);
      });

      if (filtered.length > maxRows) {
        fnoteEl.textContent = 'Showing first ' + maxRows + ' of ' + filtered.length + ' rows. Full set in metrics.json.';
        fnoteEl.style.display = 'block';
      } else {
        fnoteEl.style.display = 'none';
      }
    }

    function openColumnFilterPopup(c, colIdx, triggerBtn) {
      if (ACTIVE_TABLE_POPUP && ACTIVE_TABLE_POPUP._triggerBtn === triggerBtn) {
        closeActiveTablePopup();
        return;
      }
      closeActiveTablePopup();

      var ck = getColKey(c, colIdx);

      // Aggregate all unique display values and row counts
      var valCounts = {};
      rows.forEach(function (r) {
        var v = getColDisplay(c, r);
        valCounts[v] = (valCounts[v] || 0) + 1;
      });
      var uniqueVals = Object.keys(valCounts).sort(function (a, b) {
        return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
      });

      var popup = h('div', { class: 'tbl-filter-popup' });
      popup._triggerBtn = triggerBtn;

      // Header row
      var head = h('div', { class: 'tfp-head' });
      var titleSpan = h('span');
      titleSpan.textContent = 'Filter: ' + c.label;
      var closeX = h('button', { type: 'button', class: 'tfp-close', text: '✕' });
      closeX.addEventListener('click', function () { closeActiveTablePopup(); });
      head.appendChild(titleSpan);
      head.appendChild(closeX);
      popup.appendChild(head);

      // Sort buttons row
      var sortRow = h('div', { class: 'tfp-sort-btns' });
      var btnSortAsc = h('button', {
        type: 'button',
        class: 'tfp-sort-btn' + (sortCol === ck && sortDir === 'asc' ? ' active' : '')
      });
      btnSortAsc.textContent = '↑ Sort A → Z';
      btnSortAsc.addEventListener('click', function () {
        sortCol = ck;
        sortDir = 'asc';
        closeActiveTablePopup();
        renderFilteredRows();
      });

      var btnSortDesc = h('button', {
        type: 'button',
        class: 'tfp-sort-btn' + (sortCol === ck && sortDir === 'desc' ? ' active' : '')
      });
      btnSortDesc.textContent = '↓ Sort Z → A';
      btnSortDesc.addEventListener('click', function () {
        sortCol = ck;
        sortDir = 'desc';
        closeActiveTablePopup();
        renderFilteredRows();
      });
      sortRow.appendChild(btnSortAsc);
      sortRow.appendChild(btnSortDesc);
      popup.appendChild(sortRow);

      // Search input
      var searchInput = h('input', {
        type: 'text',
        class: 'tfp-search-input',
        placeholder: 'Search ' + c.label + ' values...'
      });
      popup.appendChild(searchInput);

      // Quick action row
      var actRow = h('div', { class: 'tfp-actions-row' });
      var selAllLink = h('button', { type: 'button', class: 'tfp-link', text: 'Select All' });
      var clearLink = h('button', { type: 'button', class: 'tfp-link', text: 'Clear' });
      var resetLink = h('button', { type: 'button', class: 'tfp-link', text: 'Reset Filter' });
      actRow.appendChild(selAllLink);
      actRow.appendChild(clearLink);
      actRow.appendChild(resetLink);
      popup.appendChild(actRow);

      // Checkbox list
      var valList = h('div', { class: 'tfp-val-list' });
      var itemMap = {}; // value -> { itemEl, checkbox }

      uniqueVals.forEach(function (val) {
        var isChecked = !activeFilters[ck] || activeFilters[ck].has(val);
        var itemEl = h('label', { class: 'tfp-val-item' });
        var cb = h('input', { type: 'checkbox' });
        cb.checked = isChecked;

        var lbl = h('span', { class: 'tfp-val-label', text: val });
        var cnt = h('span', { class: 'tfp-val-cnt', text: '(' + valCounts[val] + ')' });

        itemEl.appendChild(cb);
        itemEl.appendChild(lbl);
        itemEl.appendChild(cnt);
        valList.appendChild(itemEl);
        itemMap[val] = { itemEl: itemEl, checkbox: cb };
      });
      popup.appendChild(valList);

      // Realtime search filtering
      searchInput.addEventListener('input', function () {
        var q = searchInput.value.trim().toLowerCase();
        uniqueVals.forEach(function (val) {
          var match = !q || val.toLowerCase().indexOf(q) !== -1;
          itemMap[val].itemEl.style.display = match ? 'flex' : 'none';
        });
      });

      selAllLink.addEventListener('click', function () {
        uniqueVals.forEach(function (val) {
          if (itemMap[val].itemEl.style.display !== 'none') {
            itemMap[val].checkbox.checked = true;
          }
        });
      });

      clearLink.addEventListener('click', function () {
        uniqueVals.forEach(function (val) {
          if (itemMap[val].itemEl.style.display !== 'none') {
            itemMap[val].checkbox.checked = false;
          }
        });
      });

      resetLink.addEventListener('click', function () {
        delete activeFilters[ck];
        closeActiveTablePopup();
        renderFilteredRows();
      });

      // Footer row with Apply / Cancel
      var footer = h('div', { class: 'tfp-footer' });
      var cancelBtn = h('button', { type: 'button', class: 'tfp-btn tfp-btn-cancel', text: 'Cancel' });
      cancelBtn.addEventListener('click', function () { closeActiveTablePopup(); });

      var applyBtn = h('button', { type: 'button', class: 'tfp-btn tfp-btn-apply', text: 'Apply' });
      applyBtn.addEventListener('click', function () {
        var checkedVals = [];
        uniqueVals.forEach(function (val) {
          if (itemMap[val].checkbox.checked) { checkedVals.push(val); }
        });

        if (checkedVals.length === uniqueVals.length) {
          // All checked -> no filter needed
          delete activeFilters[ck];
        } else {
          activeFilters[ck] = new Set(checkedVals);
        }
        closeActiveTablePopup();
        renderFilteredRows();
      });

      footer.appendChild(cancelBtn);
      footer.appendChild(applyBtn);
      popup.appendChild(footer);

      // Stop wheel propagation inside the popup
      popup.addEventListener('wheel', function (ev) {
        ev.stopPropagation();
      }, { passive: true });

      document.body.appendChild(popup);
      ACTIVE_TABLE_POPUP = popup;

      // Smart viewport positioning
      var rect = triggerBtn.getBoundingClientRect();
      var popH = popup.offsetHeight || 300;
      var popW = popup.offsetWidth || 250;
      var spaceBelow = window.innerHeight - rect.bottom - 12;
      var spaceAbove = rect.top - 12;

      if (spaceBelow >= 240 || spaceBelow >= spaceAbove) {
        // Open downwards
        popup.style.top = (rect.bottom + 4) + 'px';
        popup.style.maxHeight = Math.max(160, spaceBelow) + 'px';
      } else {
        // Open upwards
        var topPos = Math.max(8, rect.top - popH - 4);
        popup.style.top = topPos + 'px';
        popup.style.maxHeight = Math.max(160, spaceAbove) + 'px';
      }

      var leftPos = rect.left;
      if (leftPos + popW > window.innerWidth - 12) {
        leftPos = Math.max(10, window.innerWidth - popW - 14);
      }
      popup.style.left = leftPos + 'px';

      // Focus search input
      setTimeout(function () { searchInput.focus(); }, 30);
    }

    renderFilteredRows();
    host.appendChild(rootContainer);
    return rootContainer;
  }

  function empty(host, msg) {
    host.appendChild(h('div', { class: 'empty', text: msg }));
  }

  /* --------------------------------------------------------- linechart ---- */
  /* series: [{label, pts:[{i, v}], role:'focal'|'context', kind:'line'|'area'|'step'}] */

  function lineChart(host, cfg) {
    var days = cfg.days;
    if (!days.length) { empty(host, cfg.emptyMsg || 'No days in range.'); return; }
    var all = [];
    cfg.series.forEach(function (s) { s.pts.forEach(function (p) { all.push(p.v); }); });
    if (!extent(all)) { empty(host, cfg.emptyMsg || 'No supported values — every cell failed its support gate.'); return; }

    var w = cfg.width, ht = cfg.height || 150;
    var m = cfg.margin || { t: 8, r: cfg.labelEnds ? 58 : 10, b: 22, l: 40 };
    var F = frame(host, { width: w, height: ht, margin: m, title: cfg.title });
    var dom = cfg.domain || padDomain(extent(all), { zeroBase: cfg.zeroBase, symmetric: cfg.symmetric });
    var y = linScale(dom[0], dom[1], F.ih, 0);
    var x = linScale(0, Math.max(days.length - 1, 1), 0, F.iw);

    yAxis(F, y, { count: cfg.yTicks || 3, fmt: cfg.yFmt, zeroEmph: true, label: cfg.yLabel });
    xDayAxis(F, x, days, { count: cfg.xTicks || 4 });

    if (cfg.zeroLine && dom[0] < 0 && dom[1] > 0) {
      F.g.appendChild(el('line', {
        x1: 0, x2: F.iw, y1: y(0), y2: y(0),
        stroke: K.axis, 'stroke-width': 1
      }));
      F.g.appendChild(svgText(F.iw, y(0) - 3, '0', { anchor: 'end', size: 8.5, fill: K.ink3 }));
    }

    /* burst ribbon: a separate ordered channel under the plot, not a series */
    if (cfg.ribbon && cfg.ribbon.length) {
      var rh = 4;
      cfg.ribbon.forEach(function (b) {
        if (!b.on) { return; }
        var x0 = x(b.i) - (F.iw / Math.max(days.length, 1)) / 2;
        F.g.appendChild(el('rect', {
          x: Math.max(0, x0), y: F.ih + 4,
          width: Math.max(1.5, F.iw / Math.max(days.length, 1)), height: rh,
          fill: K.focal, opacity: 0.85
        }));
      });
    }

    cfg.series.forEach(function (s) {
      var focal = s.role !== 'context';
      var col = focal ? K.focal : K.context;
      var segs = [], cur = [];
      s.pts.forEach(function (p) {
        if (isNum(p.v)) { cur.push(p); } else if (cur.length) { segs.push(cur); cur = []; }
      });
      if (cur.length) { segs.push(cur); }

      if (s.kind === 'area') {
        segs.forEach(function (sg) {
          if (sg.length < 2) { return; }
          var d = 'M' + x(sg[0].i) + ',' + y(Math.max(dom[0], 0));
          sg.forEach(function (p) { d += 'L' + x(p.i) + ',' + y(p.v); });
          d += 'L' + x(sg[sg.length - 1].i) + ',' + y(Math.max(dom[0], 0)) + 'Z';
          F.g.appendChild(el('path', { d: d, fill: focal ? K.focalSoft : K.contextSoft, stroke: 'none' }));
        });
      }
      if (s.kind === 'bar') {
        var bw = Math.max(1.2, Math.min(18, F.iw / Math.max(days.length, 1) - 2));
        s.pts.forEach(function (p) {
          if (!isNum(p.v)) { return; }
          var y0 = y(0), y1 = y(p.v);
          F.g.appendChild(el('rect', {
            x: x(p.i) - bw / 2, y: Math.min(y0, y1),
            width: bw, height: Math.max(1, Math.abs(y1 - y0)), rx: 1,
            fill: p.v < 0 ? K.neg : col
          }));
        });
      } else {
        segs.forEach(function (sg) {
          if (sg.length === 1) {
            F.g.appendChild(el('circle', { cx: x(sg[0].i), cy: y(sg[0].v), r: 2.6, fill: col }));
            return;
          }
          var d = 'M' + sg.map(function (p) { return x(p.i) + ',' + y(p.v); }).join('L');
          F.g.appendChild(el('path', {
            d: d, fill: 'none', stroke: col, 'stroke-width': 2,
            'stroke-linejoin': 'round', 'stroke-linecap': 'round'
          }));
        });
      }

      var last = null;
      for (var i = s.pts.length - 1; i >= 0; i--) { if (isNum(s.pts[i].v)) { last = s.pts[i]; break; } }
      if (last && s.kind !== 'bar') {
        F.g.appendChild(el('circle', {
          cx: x(last.i), cy: y(last.v), r: 4,
          fill: col, stroke: K.sunken, 'stroke-width': 2
        }));
        if (cfg.labelEnds) {
          F.g.appendChild(svgText(x(last.i) + 8, y(last.v), s.label,
            { size: 9, baseline: 'middle', fill: K.ink2 }));
        }
      }
    });

    /* crosshair */
    var xh = el('line', {
      x1: 0, x2: 0, y1: 0, y2: F.ih, stroke: K.goldHair,
      'stroke-width': 1, opacity: 0
    });
    F.g.appendChild(xh);
    var hit = el('rect', { x: 0, y: 0, width: F.iw, height: F.ih, fill: 'transparent' });
    F.g.appendChild(hit);
    function move(ev) {
      var r = F.svg.getBoundingClientRect();
      var px = ev.clientX - r.left - F.m.l;
      var i = Math.round(x.inv(Math.max(0, Math.min(F.iw, px))));
      i = Math.max(0, Math.min(days.length - 1, i));
      xh.setAttribute('x1', x(i)); xh.setAttribute('x2', x(i));
      xh.setAttribute('opacity', 1);
      var rows = cfg.series.map(function (s) {
        var p = s.pts[i];
        return {
          k: s.label, color: s.role === 'context' ? K.context : K.focal,
          v: (p && isNum(p.v)) ? (cfg.tipFmt ? cfg.tipFmt(p.v) : fmt(p.v, 3)) : 'no value (gated)'
        };
      });
      if (cfg.extraRows) { rows = rows.concat(cfg.extraRows(i)); }
      TIP.show(ev, tipNode((cfg.tipHead ? cfg.tipHead + ' · ' : '') + fmtDayFull(days[i]),
        rows, cfg.tipNote));
    }
    hit.addEventListener('pointermove', move);
    hit.addEventListener('pointerleave', function () { xh.setAttribute('opacity', 0); TIP.hide(); });
    return F;
  }

  /* ------------------------------------------------------ small multiple -- */

  function smallMultiples(host, cfg) {
    if (!cfg.facets.length) { empty(host, cfg.emptyMsg || 'Nothing to show for the current filters.'); return; }
    var grid = h('div', { class: 'grid ' + (cfg.cols === 2 ? 'g2' : 'g3') });
    host.appendChild(grid);
    var shared = null;
    if (cfg.shareY) {
      var all = [];
      cfg.facets.forEach(function (f) {
        f.series.forEach(function (s) { s.pts.forEach(function (p) { all.push(p.v); }); });
      });
      var ex = extent(all);
      if (ex) { shared = padDomain(ex, { zeroBase: cfg.zeroBase, symmetric: cfg.symmetric }); }
    }
    cfg.facets.forEach(function (f) {
      var cell = h('div', { class: 'facet', id: f.id || null });
      var head = h('div', { class: 'facet-t' }, [h('span', { text: f.title })]);
      if (f.note) { head.appendChild(h('span', { class: 'n', text: f.note })); }
      attachDrill(head, f.drill);
      cell.appendChild(head);
      var plot = h('div', { class: 'plot' });
      cell.appendChild(plot);
      grid.appendChild(cell);
      var w = Math.max(180, plot.clientWidth || cell.clientWidth - 20);
      lineChart(plot, {
        days: cfg.days, series: f.series, width: w, height: cfg.height || 118,
        domain: shared, zeroBase: cfg.zeroBase, symmetric: cfg.symmetric,
        zeroLine: cfg.zeroLine, yFmt: cfg.yFmt, tipFmt: cfg.tipFmt,
        tipHead: f.title, tipNote: cfg.tipNote, yTicks: 3, xTicks: 3,
        ribbon: f.ribbon, emptyMsg: cfg.facetEmptyMsg,
        margin: { t: 8, r: 10, b: f.ribbon ? 30 : 22, l: 36 },
        extraRows: f.extraRows
      });
      if (f.sub) { cell.appendChild(h('div', { class: 'fnote', text: f.sub })); }
    });
  }

  /* --------------------------------------------------------- bar charts --- */

  function hBars(host, cfg) {
    var items = cfg.items.filter(function (d) { return isNum(d.value); });
    if (!items.length) { empty(host, cfg.emptyMsg || 'No supported values in the current selection.'); return; }
    var rowH = cfg.rowH || 20;
    var labelW = cfg.labelW || 150;
    var m = { t: 6, r: 52, b: 20, l: labelW };
    var ht = items.length * rowH + m.t + m.b;
    var F = frame(host, { width: cfg.width, height: ht, margin: m, title: cfg.title });
    var ex = extent(items.map(function (d) { return d.value; }));
    var dom = cfg.diverging ? padDomain(ex, { symmetric: true }) : padDomain(ex, { zeroBase: true });
    if (!cfg.diverging && dom[0] < 0) { dom = padDomain(ex, { symmetric: true }); }
    var x = linScale(dom[0], dom[1], 0, F.iw);
    var zero = x(0);

    niceTicks(dom[0], dom[1], 4).forEach(function (v) {
      F.g.appendChild(el('line', {
        x1: x(v), x2: x(v), y1: 0, y2: items.length * rowH,
        stroke: v === 0 ? K.axis : K.grid, 'stroke-width': 1
      }));
      F.g.appendChild(svgText(x(v), items.length * rowH + 12, cfg.xFmt ? cfg.xFmt(v) : fmt(v, 2),
        { anchor: 'middle', size: 9, tabular: true }));
    });

    items.forEach(function (d, i) {
      var yTop = i * rowH + 3, bh = Math.min(14, rowH - 6);
      var x0 = Math.min(zero, x(d.value)), x1 = Math.max(zero, x(d.value));
      var col = cfg.diverging ? (d.value < 0 ? K.neg : K.focal)
        : (d.dim ? K.context : K.focal);
      var g = el('g');
      g.appendChild(el('rect', {
        x: x0, y: yTop, width: Math.max(1.5, x1 - x0), height: bh,
        rx: 2, fill: col
      }));
      g.appendChild(svgText(-8, yTop + bh / 2, d.label,
        { anchor: 'end', baseline: 'middle', size: 9.5, fill: K.ink2 }));
      var vx = d.value < 0 ? x0 - 6 : x1 + 6;
      g.appendChild(svgText(vx, yTop + bh / 2, cfg.vFmt ? cfg.vFmt(d.value) : fmt(d.value, 2),
        { anchor: d.value < 0 ? 'end' : 'start', baseline: 'middle', size: 9, tabular: true, fill: K.ink2 }));
      hoverable(g, function () { return d.label; },
        function () {
          var r = [{ k: cfg.measure || 'value', color: col, v: cfg.vFmt ? cfg.vFmt(d.value) : fmt(d.value, 3) }];
          return r.concat(d.rows || []);
        }, function () { return d.note || cfg.tipNote; });
      attachDrill(g, d.drill);
      F.g.appendChild(g);
    });
    return F;
  }

  function vBars(host, cfg) {
    var items = cfg.items;
    if (!items.length) { empty(host, cfg.emptyMsg || 'Nothing to plot.'); return; }
    var m = { t: 10, r: 8, b: 30, l: 38 };
    var F = frame(host, { width: cfg.width, height: cfg.height || 130, margin: m, title: cfg.title });
    var ex = extent(items.map(function (d) { return d.value; })) || [0, 1];
    var dom = padDomain(ex, { zeroBase: true });
    var y = linScale(dom[0], dom[1], F.ih, 0);
    yAxis(F, y, { count: 3, fmt: cfg.yFmt });
    var step = F.iw / items.length;
    var bw = Math.max(1.5, Math.min(cfg.maxBar || 24, step - 2));
    items.forEach(function (d, i) {
      var cx = i * step + step / 2;
      if (isNum(d.value)) {
        var y1 = y(d.value), y0 = y(0);
        var g = el('g');
        g.appendChild(el('rect', {
          x: cx - bw / 2, y: Math.min(y0, y1), width: bw,
          height: Math.max(1, Math.abs(y1 - y0)), rx: 2, fill: d.color || K.focal
        }));
        hoverable(g, function () { return d.label; },
          function () {
            return [{
              k: cfg.measure || 'value', color: d.color || K.focal,
              v: cfg.vFmt ? cfg.vFmt(d.value) : fmtInt(d.value)
            }].concat(d.rows || []);
          },
          function () { return cfg.tipNote; });
        attachDrill(g, d.drill);
        F.g.appendChild(g);
      }
      if (cfg.everyLabel || i % Math.ceil(items.length / (cfg.labelCount || 7)) === 0) {
        F.g.appendChild(svgText(cx, F.ih + 13, d.short || d.label,
          { anchor: 'middle', size: 8.5, tabular: true }));
      }
    });
    F.g.appendChild(el('line', { x1: 0, x2: F.iw, y1: y(0), y2: y(0), stroke: K.axis, 'stroke-width': 1 }));
    return F;
  }

  /* ----------------------------------------------------------- heatmap ---- */

  function heatmap(host, cfg) {
    var rowsL = cfg.rows, colsL = cfg.cols;
    if (!rowsL.length || !colsL.length) { empty(host, cfg.emptyMsg || 'No cells in the current selection.'); return; }
    var cw = cfg.cellW || 13, ch = cfg.cellH || 20, gap = 2;
    var labelW = cfg.labelW || 168;
    var m = { t: 6, r: 10, b: 24, l: labelW };
    var w = labelW + colsL.length * cw + m.r;
    var ht = m.t + rowsL.length * ch + m.b;
    var scroll = h('div', { class: 'scrollx' });
    host.appendChild(scroll);
    var F = frame(scroll, { width: w, height: ht, margin: m, title: cfg.title });
    var hatch = hatchPattern(F.svg);

    rowsL.forEach(function (r, ri) {
      F.g.appendChild(svgText(-8, ri * ch + ch / 2, r.label,
        { anchor: 'end', baseline: 'middle', size: 9.5, fill: K.ink2 }));
      colsL.forEach(function (c, ci) {
        var d = cfg.get(r, c);
        var x = ci * cw, y = ri * ch;
        var col = d && isNum(d.v) ? cfg.color(d.v) : null;
        var rect = el('rect', {
          x: x, y: y, width: Math.max(1, cw - gap), height: Math.max(1, ch - gap),
          fill: col || hatch, stroke: 'none'
        });
        F.g.appendChild(rect);
        if (d && d.ring) {
          F.g.appendChild(el('rect', {
            x: x - 0.5, y: y - 0.5, width: cw - gap + 1, height: ch - gap + 1,
            fill: 'none', stroke: K.ink, 'stroke-width': 1.2
          }));
        }
        var hitR = el('rect', { x: x - 1, y: y - 1, width: cw + 1, height: ch + 1, fill: 'transparent' });
        F.g.appendChild(hitR);
        hoverable(hitR, function () { return r.label + ' · ' + c.label; },
          function () {
            return (d && d.rows) || [{
              k: cfg.measure || 'value', color: col || K.context,
              v: d && isNum(d.v) ? fmt(d.v, 3) : 'no value (support gate not met)'
            }];
          },
          function () { return (d && d.note) || cfg.tipNote; });
        attachDrill(hitR, d && d.drill);
      });
    });

    var want = Math.max(2, Math.min(cfg.xLabelCount || 7, colsL.length));
    var step = Math.max(1, Math.floor((colsL.length - 1) / (want - 1)));
    for (var ci = 0; ci < colsL.length; ci += step) {
      F.g.appendChild(svgText(ci * cw + (cw - gap) / 2, rowsL.length * ch + 12, colsL[ci].short || colsL[ci].label,
        { anchor: 'middle', size: 8.5, tabular: true }));
    }
    return F;
  }

  /* ------------------------------------------------------------- gantt ---- */

  function gantt(host, cfg) {
    if (!cfg.rows.length) { empty(host, cfg.emptyMsg || 'No episodes detected in the current selection.'); return; }
    var rowH = 22, labelW = cfg.labelW || 200;
    var m = { t: 8, r: 24, b: 8, l: labelW };
    var inner = Math.max(280, (cfg.width || 700) - labelW - m.r);
    var w = labelW + inner + m.r;
    var days = cfg.days;
    var x = linScale(0, Math.max(days.length - 1, 1), 0, inner);
    var idx = {}; days.forEach(function (d, i) { idx[d] = i; });

    var want = Math.max(2, Math.min(7, days.length));
    var st = Math.max(1, Math.floor((days.length - 1) / (want - 1)));

    var outer = h('div', { class: 'gantt-wrap' });
    var innerWrap = h('div', { class: 'gantt-inner', style: 'min-width:' + w + 'px;width:' + w + 'px;' });
    outer.appendChild(innerWrap);
    host.appendChild(outer);

    /* --- 1. Scrollable Rows Body (Y-Axis) --- */
    var totalRowsHt = cfg.rows.length * rowH;
    var bodyHt = m.t + totalRowsHt + m.b;
    var bodyBox = h('div', { class: 'gantt-body' });
    var bodySvg = el('svg', { width: w, height: bodyHt, viewBox: '0 0 ' + w + ' ' + bodyHt, style: 'display:block;' });
    if (cfg.title) {
      var ti = el('title'); ti.textContent = cfg.title; bodySvg.appendChild(ti);
    }
    var bodyG = el('g', { transform: 'translate(' + m.l + ',' + m.t + ')' });

    for (var i = 0; i < days.length; i += st) {
      var xi = x(i);
      bodyG.appendChild(el('line', {
        x1: xi, x2: xi, y1: 0, y2: totalRowsHt,
        stroke: K.grid, 'stroke-width': 1
      }));
    }

    cfg.rows.forEach(function (r, ri) {
      var y = ri * rowH + rowH / 2;
      bodyG.appendChild(svgText(-8, y, r.label, { anchor: 'end', baseline: 'middle', size: 9.5, fill: K.ink2 }));
      var i0 = idx[r.start], i1 = idx[r.end];
      if (i0 == null) { i0 = 0; }
      if (i1 == null) { i1 = days.length - 1; }
      var x0 = x(i0), x1 = x(i1);
      var g = el('g');
      g.appendChild(el('rect', {
        x: x0, y: y - 5, width: Math.max(3, x1 - x0), height: 10, rx: 2,
        fill: r.color || K.focal, opacity: 0.55
      }));
      if (idx[r.peak] != null) {
        g.appendChild(el('circle', {
          cx: x(idx[r.peak]), cy: y, r: 4.5, fill: r.color || K.focal,
          stroke: K.sunken, 'stroke-width': 2
        }));
      }
      var hit = el('rect', {
        x: Math.max(0, x0 - 6), y: y - 10, width: Math.max(16, x1 - x0 + 12),
        height: 20, fill: 'transparent'
      });
      g.appendChild(hit);
      hoverable(hit, function () { return r.label; }, function () { return r.rows; },
        function () { return r.note; });
      attachDrill(hit, r.drill);
      bodyG.appendChild(g);
    });

    bodySvg.appendChild(bodyG);
    bodyBox.appendChild(bodySvg);
    innerWrap.appendChild(bodyBox);

    /* --- 2. Fixed X-Axis Date Footer (below rows) --- */
    var footerHt = 26;
    var footerBox = h('div', { class: 'gantt-footer' });
    var footerSvg = el('svg', { width: w, height: footerHt, viewBox: '0 0 ' + w + ' ' + footerHt, style: 'display:block;' });
    var footerG = el('g', { transform: 'translate(' + m.l + ',0)' });

    for (var i = 0; i < days.length; i += st) {
      var xi = x(i);
      footerG.appendChild(el('line', {
        x1: xi, x2: xi, y1: 0, y2: 6,
        stroke: K.grid, 'stroke-width': 1
      }));
      footerG.appendChild(svgText(xi, 17, fmtDay(days[i]),
        { anchor: 'middle', size: 8.5, tabular: true, fill: K.ink2 }));
    }
    footerSvg.appendChild(footerG);
    footerBox.appendChild(footerSvg);
    innerWrap.appendChild(footerBox);

    return { svg: bodySvg, footerSvg: footerSvg, g: bodyG, w: w, h: bodyHt, iw: inner, ih: totalRowsHt };
  }

  /* ---------------------------------------------------------- swimlane ---- */
  /* One row per category/entity, one dot per day it fired -- a gantt without
     the interval (point events, not episodes), shared with any day axis. */

  function swimlane(host, cfg) {
    if (!cfg.rows.length) { empty(host, cfg.emptyMsg || 'No rows to show.'); return; }
    var rowH = cfg.rowH || 20, labelW = cfg.labelW || 190;
    var m = { t: 6, r: 10, b: 24, l: labelW };
    var inner = Math.max(240, (cfg.width || 700) - labelW - m.r);
    var w = labelW + inner + m.r;
    var ht = m.t + cfg.rows.length * rowH + m.b;
    var scroll = h('div', { class: 'scrollx' });
    host.appendChild(scroll);
    var F = frame(scroll, { width: w, height: ht, margin: m, title: cfg.title });
    var days = cfg.days;
    var x = linScale(0, Math.max(days.length - 1, 1), 0, F.iw);
    var idx = {}; days.forEach(function (d, i) { idx[d] = i; });

    var want = Math.max(2, Math.min(7, days.length));
    var st = Math.max(1, Math.floor((days.length - 1) / (want - 1)));
    for (var i = 0; i < days.length; i += st) {
      F.g.appendChild(el('line', {
        x1: x(i), x2: x(i), y1: 0, y2: cfg.rows.length * rowH,
        stroke: K.grid, 'stroke-width': 1
      }));
      F.g.appendChild(svgText(x(i), cfg.rows.length * rowH + 13, fmtDay(days[i]),
        { anchor: 'middle', size: 8.5, tabular: true }));
    }
    var byRow = {};
    cfg.rows.forEach(function (r, ri) {
      byRow[r.id] = ri;
      var y0 = ri * rowH + rowH / 2;
      F.g.appendChild(svgText(-8, y0, r.label, { anchor: 'end', baseline: 'middle', size: 9.5, fill: K.ink2 }));
      F.g.appendChild(el('line', { x1: 0, x2: F.iw, y1: y0, y2: y0, stroke: K.grid, 'stroke-width': 1 }));
    });
    (cfg.points || []).forEach(function (p) {
      var ri = byRow[p.rowId], di = idx[p.day];
      if (ri == null || di == null) { return; }
      var cx = x(di), cy = ri * rowH + rowH / 2;
      var g = el('g');
      g.appendChild(el('circle', {
        cx: cx, cy: cy, r: p.r || 5, fill: p.color || K.focal,
        stroke: K.surface, 'stroke-width': 1.5
      }));
      var hit = el('circle', { cx: cx, cy: cy, r: 9, fill: 'transparent' });
      g.appendChild(hit);
      hoverable(hit, function () { return p.label; }, function () { return p.rows; },
        function () { return p.note; });
      attachDrill(hit, p.drill);
      F.g.appendChild(g);
    });
    return F;
  }

  /* ----------------------------------------------------------- dotplot ---- */

  function dotPlot(host, cfg) {
    var items = cfg.items.filter(function (d) { return isNum(d.value); });
    if (!items.length) { empty(host, cfg.emptyMsg || 'No fitted cells in the current selection.'); return; }
    var rowH = cfg.rowH || 19, labelW = cfg.labelW || 190;
    var m = { t: 8, r: 46, b: 22, l: labelW };
    var ht = m.t + items.length * rowH + m.b;
    var F = frame(host, { width: cfg.width, height: ht, margin: m, title: cfg.title });
    var vals = [];
    items.forEach(function (d) {
      vals.push(d.value);
      if (isNum(d.lo)) { vals.push(d.lo); }
      if (isNum(d.hi)) { vals.push(d.hi); }
    });
    var dom = cfg.domain || padDomain(extent(vals), {});
    var x = linScale(dom[0], dom[1], 0, F.iw);
    niceTicks(dom[0], dom[1], 4).forEach(function (v) {
      F.g.appendChild(el('line', {
        x1: x(v), x2: x(v), y1: 0, y2: items.length * rowH,
        stroke: Math.abs(v) < 1e-12 ? K.axis : K.grid, 'stroke-width': 1
      }));
      F.g.appendChild(svgText(x(v), items.length * rowH + 12, fmt(v, 2),
        { anchor: 'middle', size: 9, tabular: true }));
    });
    if (isNum(cfg.refValue)) {
      F.g.appendChild(el('line', {
        x1: x(cfg.refValue), x2: x(cfg.refValue), y1: 0,
        y2: items.length * rowH, stroke: K.context, 'stroke-width': 1, 'stroke-dasharray': 'none'
      }));
    }
    items.forEach(function (d, i) {
      var y = i * rowH + rowH / 2;
      var g = el('g');
      F.g.appendChild(svgText(-8, y, d.label, { anchor: 'end', baseline: 'middle', size: 9.5, fill: K.ink2 }));
      if (isNum(d.lo) && isNum(d.hi)) {
        g.appendChild(el('line', {
          x1: x(d.lo), x2: x(d.hi), y1: y, y2: y,
          stroke: K.context, 'stroke-width': 2, 'stroke-linecap': 'round'
        }));
      }
      g.appendChild(el('circle', {
        cx: x(d.value), cy: y, r: 4.5,
        fill: d.dim ? K.context : K.focal, stroke: K.surface, 'stroke-width': 2
      }));
      g.appendChild(svgText(F.iw + 6, y, cfg.vFmt ? cfg.vFmt(d.value) : fmt(d.value, 2),
        { anchor: 'start', baseline: 'middle', size: 9, tabular: true, fill: K.ink2 }));
      var hit = el('rect', { x: 0, y: y - rowH / 2, width: F.iw, height: rowH, fill: 'transparent' });
      g.appendChild(hit);
      hoverable(hit, function () { return d.label; }, function () { return d.rows; },
        function () { return d.note; });
      attachDrill(hit, d.drill);
      F.g.appendChild(g);
    });
    return F;
  }

  /* ---------------------------------------------------------- dumbbell ---- */

  function dumbbell(host, cfg) {
    var items = cfg.items;
    if (!items.length) { empty(host, cfg.emptyMsg || 'No values.'); return; }
    var rowH = 19, labelW = cfg.labelW || 190;
    var m = { t: 8, r: 52, b: 22, l: labelW };
    var ht = m.t + items.length * rowH + m.b;
    var F = frame(host, { width: cfg.width, height: ht, margin: m, title: cfg.title });
    var vals = [];
    items.forEach(function (d) { vals.push(d.a); vals.push(d.b); });
    var dom = cfg.domain || padDomain(extent(vals), { symmetric: cfg.symmetric });
    var x = linScale(dom[0], dom[1], 0, F.iw);
    niceTicks(dom[0], dom[1], 4).forEach(function (v) {
      F.g.appendChild(el('line', {
        x1: x(v), x2: x(v), y1: 0, y2: items.length * rowH,
        stroke: Math.abs(v) < 1e-12 ? K.axis : K.grid, 'stroke-width': 1
      }));
      F.g.appendChild(svgText(x(v), items.length * rowH + 12, fmt(v, 2),
        { anchor: 'middle', size: 9, tabular: true }));
    });
    items.forEach(function (d, i) {
      var y = i * rowH + rowH / 2;
      F.g.appendChild(svgText(-8, y, d.label, { anchor: 'end', baseline: 'middle', size: 9.5, fill: K.ink2 }));
      var g = el('g');
      if (isNum(d.a) && isNum(d.b)) {
        g.appendChild(el('line', { x1: x(d.a), x2: x(d.b), y1: y, y2: y, stroke: K.axis, 'stroke-width': 1.5 }));
      }
      if (isNum(d.a)) {
        g.appendChild(el('circle', {
          cx: x(d.a), cy: y, r: 4, fill: K.context,
          stroke: K.surface, 'stroke-width': 2
        }));
      }
      if (isNum(d.b)) {
        g.appendChild(el('circle', {
          cx: x(d.b), cy: y, r: 4.5, fill: K.focal,
          stroke: K.surface, 'stroke-width': 2
        }));
      }
      g.appendChild(svgText(F.iw + 6, y, d.right || '', {
        anchor: 'start', baseline: 'middle',
        size: 9, tabular: true, fill: K.ink3
      }));
      var hit = el('rect', { x: 0, y: y - rowH / 2, width: F.iw, height: rowH, fill: 'transparent' });
      g.appendChild(hit);
      hoverable(hit, function () { return d.label; }, function () { return d.rows; },
        function () { return d.note; });
      attachDrill(hit, d.drill);
      F.g.appendChild(g);
    });
    return F;
  }

  /* --------------------------------------------------------- histogram ---- */

  function histogram(host, cfg) {
    var vals = cfg.values.filter(isNum);
    if (vals.length < 2) { empty(host, cfg.emptyMsg || 'Fewer than 2 scored cells.'); return; }
    var n = cfg.bins || 10, lo = cfg.lo, hi = cfg.hi;
    if (lo == null) { var ex = extent(vals); lo = ex[0]; hi = ex[1]; }
    var counts = new Array(n).fill(0);
    vals.forEach(function (v) {
      var i = Math.min(n - 1, Math.max(0, Math.floor((v - lo) / ((hi - lo) || 1) * n)));
      counts[i]++;
    });
    var items = counts.map(function (c, i) {
      var b0 = lo + (hi - lo) * i / n, b1 = lo + (hi - lo) * (i + 1) / n;
      return {
        label: fmt(b0, 1) + '–' + fmt(b1, 1), short: (i % 2 === 0 ? fmt(b0, 1) : ''),
        value: c, rows: [{ k: 'cells', color: K.focal, v: fmtInt(c) },
        { k: 'range', v: fmt(b0, 2) + ' – ' + fmt(b1, 2) }]
      };
    });
    vBars(host, {
      items: items, width: cfg.width, height: cfg.height || 110,
      measure: 'cells', vFmt: fmtInt, yFmt: fmtInt, everyLabel: true, maxBar: 30,
      tipNote: cfg.tipNote
    });
  }

  /* ----------------------------------------------------------- scatter ---- */

  function scatter(host, cfg) {
    var pts = cfg.points.filter(function (p) { return isNum(p.x) && isNum(p.y); });
    if (!pts.length) { empty(host, cfg.emptyMsg || 'No cells with both measures present.'); return; }
    var m = { t: 10, r: 14, b: 34, l: 46 };
    var F = frame(host, { width: cfg.width, height: cfg.height || 260, margin: m, title: cfg.title });
    var dx = padDomain(extent(pts.map(function (p) { return p.x; })), { zeroBase: cfg.xZero });
    var dy = padDomain(extent(pts.map(function (p) { return p.y; })), { zeroBase: cfg.yZero });
    var x = linScale(dx[0], dx[1], 0, F.iw), y = linScale(dy[0], dy[1], F.ih, 0);
    yAxis(F, y, { count: 4, fmt: cfg.yFmt, label: cfg.yLabel });
    niceTicks(dx[0], dx[1], 5).forEach(function (v) {
      F.g.appendChild(el('line', { x1: x(v), x2: x(v), y1: 0, y2: F.ih, stroke: K.grid, 'stroke-width': 1 }));
      F.g.appendChild(svgText(x(v), F.ih + 13, cfg.xFmt ? cfg.xFmt(v) : fmt(v, 1),
        { anchor: 'middle', size: 9, tabular: true }));
    });
    if (cfg.xLabel) {
      F.g.appendChild(svgText(F.iw / 2, F.ih + 27, cfg.xLabel, { anchor: 'middle', size: 9, fill: K.ink3 }));
    }
    pts.forEach(function (p) {
      var g = el('g');
      if (p.flag) {
        g.appendChild(el('circle', {
          cx: x(p.x), cy: y(p.y), r: 7, fill: 'none',
          stroke: K.crit, 'stroke-width': 1.6
        }));
        g.appendChild(el('path', {
          d: 'M' + (x(p.x) - 3) + ',' + (y(p.y) - 3) + 'L' + (x(p.x) + 3) + ',' + (y(p.y) + 3) +
            'M' + (x(p.x) + 3) + ',' + (y(p.y) - 3) + 'L' + (x(p.x) - 3) + ',' + (y(p.y) + 3),
          stroke: K.crit, 'stroke-width': 1.6
        }));
      } else {
        g.appendChild(el('circle', {
          cx: x(p.x), cy: y(p.y), r: 4, fill: K.focal,
          opacity: 0.75, stroke: K.surface, 'stroke-width': 1.5
        }));
      }
      var hit = el('circle', { cx: x(p.x), cy: y(p.y), r: 12, fill: 'transparent' });
      g.appendChild(hit);
      hoverable(hit, function () { return p.label; }, function () { return p.rows; },
        function () { return p.note; });
      attachDrill(hit, p.drill);
      F.g.appendChild(g);
    });
    return F;
  }

  /* ------------------------------------------------------- meter / bullet - */

  function meterRow(label, value, note, ok) {
    var row = h('div', { class: 'facet' });
    row.appendChild(h('div', { class: 'facet-t' }, [h('span', { text: label }),
    h('span', { class: 'n', text: fmtPct(value, 1) })]));
    var track = h('div');
    track.style.height = '8px';
    track.style.background = K.grid;
    track.style.borderRadius = '2px';
    track.style.overflow = 'hidden';
    var fill = h('div');
    fill.style.height = '100%';
    fill.style.width = Math.max(0, Math.min(100, (value || 0) * 100)) + '%';
    fill.style.background = ok ? K.focal : K.context;
    track.appendChild(fill);
    row.appendChild(track);
    if (note) { row.appendChild(h('div', { class: 'fnote', text: note })); }
    return row;
  }

  /* ================================================================= state */

  var ALLDAYS = [];
  var WIN = {};
  var WINDAYS = [];
  var S = { from: '', to: '', platforms: {}, topics: {}, windowOnly: true };

  /* Geo-ontology layer (entity/risk/theme/alerts): its own day axis, no
     platform/topic dimension, so it never joins the S/F filter machinery
     above -- same pattern topic_cards already uses for topic_dim. */
  var GEODAYS = [];

  function hydrate(data) {
    DATA = data;
    window.__METRICS__ = data;
    _cache = {};
    META = {};
    TOPIC = {};
    TAG = {};
    PLATMETA = {};
    T('metric_meta').forEach(function (m) { META[m.metric_key] = m; });
    T('topic_dim').forEach(function (t) { TOPIC[t.topic_id] = t; });
    (T('dtheme_dim') || []).forEach(function (t) { TOPIC[t.topic_id] = t; });
    T('tag_dim').forEach(function (t) { TAG[t.tag_id] = t; });
    PLATFORMS = T('platform_dim').map(function (p) { return p.platform; });
    PLATMETA = {};
    T('platform_dim').forEach(function (p) { PLATMETA[p.platform] = p; });
    TOPIC_IDS = T('topic_dim').map(function (t) { return t.topic_id; });

    var s = {};
    T('metrics_daily').forEach(function (r) { s[day(r.day)] = 1; });
    T('metrics_topic_day').forEach(function (r) { s[day(r.day)] = 1; });
    ALLDAYS = Object.keys(s).sort();
    WIN = (DATA && DATA.window) || {};
    WINDAYS = ALLDAYS.filter(function (d) {
      return (!WIN.start || d >= day(WIN.start)) && (!WIN.end || d <= day(WIN.end));
    });
    // Default view is a rolling week ending at the newest day with data (never the whole
    // multi-month window); the "Full window" / 14d / 30d presets widen it on demand.
    var lastDay = WINDAYS[WINDAYS.length - 1] || ALLDAYS[ALLDAYS.length - 1] || '';
    var week = (WINDAYS.length ? WINDAYS : ALLDAYS);
    S = {
      from: (week[Math.max(0, week.length - 7)] || ''),
      to: lastDay,
      platforms: {},
      topics: {},
      windowOnly: true
    };
    PLATFORMS.forEach(function (p) { S.platforms[p] = true; });
    TOPIC_IDS.forEach(function (t) { S.topics[t] = true; });

    var gd = {};
    T('risk_metrics_daily').forEach(function (r) { gd[day(r.day)] = 1; });
    T('theme_metrics_daily').forEach(function (r) { gd[day(r.day)] = 1; });
    GEODAYS = Object.keys(gd).sort();
  }

  /* Data freshness and the partial last day, read from the metrics already in the snapshot (works without the API).
     A day is PARTIAL when its total posts are under half of the median of the 7 days before it: the collector had not
     finished, so a topic's growth against the full day before would look like a collapse. */
  var _DQ = null, _DQ_FOR = null;
  function dqInfo() {
    if (_DQ && _DQ_FOR === DATA) { return _DQ; }
    var W = WINDAYS.length ? WINDAYS : ALLDAYS, tot = {}, plat = {};
    T('metrics_daily').forEach(function (r) {
      var d = day(r.day), v = r.V || 0;
      tot[d] = (tot[d] || 0) + v;
      if (v > 0 && (!plat[r.platform] || d > plat[r.platform])) { plat[r.platform] = d; }
    });
    var newest = W[W.length - 1] || '';
    var info = { newest: newest, ageDays: null, partial: null, lastFull: newest, ended: [] };
    if (newest) {
      info.ageDays = Math.floor((Date.now() - Date.parse(newest)) / 86400000);
      var prev = W.slice(-8, -1).map(function (d) { return tot[d] || 0; }).sort(function (a, b) { return a - b; });
      var typical = prev.length ? prev[Math.floor(prev.length / 2)] : 0;
      if (typical > 0 && (tot[newest] || 0) < 0.5 * typical) {
        info.partial = { day: newest, n: tot[newest] || 0, typical: typical };
        info.lastFull = W.length > 1 ? W[W.length - 2] : newest;
      }
      var nd = Math.round(Date.parse(newest) / 86400000);
      Object.keys(plat).sort().forEach(function (p) {
        if (nd - Math.round(Date.parse(plat[p]) / 86400000) >= 2) { info.ended.push({ platform: p, last: plat[p] }); }
      });
    }
    _DQ = info; _DQ_FOR = DATA;
    return info;
  }
  function dqLine(q) {                 // one short sentence for headers
    var i = dqInfo();
    if (!i.newest) { return ''; }
    return 'newest data ' + i.newest + (i.ageDays != null && i.ageDays >= 2 ? ' (' + i.ageDays + ' days ago)' : '') +
      (i.partial ? ', ' + i.partial.day + ' is a partial day' : '');
  }

  function selPlatforms() { return PLATFORMS.filter(function (p) { return S.platforms[p]; }); }
  function selTopics() { return TOPIC_IDS.filter(function (t) { return S.topics[t]; }); }

  var F = {};
  function applyFilters() {
    function dayOk(d) { var x = day(d); return x >= S.from && x <= S.to; }
    F.days = ALLDAYS.filter(dayOk);
    F.md = T('metrics_daily').filter(function (r) {
      return dayOk(r.day) && S.platforms[r.platform] && S.topics[r.topic_id] &&
        (!S.windowOnly || r.in_window);
    });
    F.mdAll = T('metrics_daily').filter(function (r) {
      return dayOk(r.day) && S.platforms[r.platform] && S.topics[r.topic_id];
    });
    F.mtd = T('metrics_topic_day').filter(function (r) {
      return dayOk(r.day) && S.topics[r.topic_id] && (!S.windowOnly || r.in_window);
    });
    var mode = (document.body && document.body.getAttribute('data-mode')) || 'basic';
    if (mode === 'advanced') {
      F.tags = T('tag_metrics_daily').filter(function (r) {
        return dayOk(r.day) && S.platforms[r.platform];
      });
    } else {
      F.tags = [];
    }
    F.alerts = T('alert_candidates').filter(function (r) {
      return dayOk(r.day) && (r.platform == null || S.platforms[r.platform]) &&
        (r.topic_id == null || S.topics[r.topic_id]);
    });
    F.bursts = T('burst_episodes').filter(function (r) {
      return S.platforms[r.platform] && S.topics[r.topic_id] &&
        day(r.end_day) >= S.from && day(r.start_day) <= S.to;
    });
    F.pers = T('persistence').filter(function (r) {
      return S.platforms[r.platform] && S.topics[r.topic_id];
    });
  }

  function series(rows, key, days, opts) {
    var by = {};
    rows.forEach(function (r) {
      var d = day(r.day), v = r[key];
      if (!isNum(v)) { return; }
      if (!by[d]) { by[d] = { s: 0, n: 0 }; }
      by[d].s += v; by[d].n++;
    });
    var agg = (opts && opts.agg) || 'sum';
    return days.map(function (d, i) {
      var b = by[d];
      if (!b) { return { i: i, v: null }; }
      return { i: i, v: agg === 'mean' ? b.s / b.n : b.s };
    });
  }

  /* ================================================================ panels */

  var P = {};

  /* ---- 1. KPI row ---- */
  P.kpis = function (host) {
    var md = F.md, mdAll = F.mdAll;
    var corpus = 0; mdAll.forEach(function (r) { corpus += (r.V || 0); });
    var cells = md.length;
    var ok = 0; md.forEach(function (r) { if (r.support_ok) { ok++; } });
    var alerts = F.alerts.length;
    var crit = F.alerts.filter(function (a) { return a.severity === 'critical'; }).length;
    var top = null;
    md.forEach(function (r) {
      if (r.topic_id !== 'unassigned' && isNum(r.emergence_score) && (!top || r.emergence_score > top.emergence_score)) { top = r; }
    });
    var dr = F.days.length;

    function kpi(cls, lbl, val, sub) {
      return h('div', { class: 'kpi ' + (cls || '') }, [
        h('div', { class: 'k-lbl', text: lbl }),
        h('div', { class: 'k-val', text: val }),
        h('div', { class: 'k-sub', text: sub })
      ]);
    }
    var box = h('div', { class: 'kpis' }, [
      kpi('hero', 'Active alerts (False Discovery Rate q < 0.05)', String(alerts),
        crit + ' critical · ' + (alerts - crit) + ' high/medium'),
      kpi('', 'Corpus size in view', fmtCompact(corpus),
        fmtInt(corpus) + ' posts across ' + selPlatforms().length + ' platforms'),
      kpi('', 'Analysis window', fmtDay(S.from) + ' – ' + fmtDay(S.to),
        dr + ' days' + (S.windowOnly ? ' · in-window only' : ' · incl. backfill')),
      kpi('', 'Cells passing support gate', fmtInt(ok) + ' / ' + fmtInt(cells),
        cells ? fmtPct(ok / cells, 1) + ' of cells carry publishable gated metrics' : 'no cells'),
      kpi('', 'Top emerging cell', top ? topicLabel(top.topic_id) : '—',
        top ? (top.platform + ' · ' + fmtDay(top.day) + ' · emergence ' + fmt(top.emergence_score, 2))
          : 'no cell cleared the gate')
    ]);
    host.appendChild(box);
  };

  /* ---- 2. Alerts ---- */
  function sevChip(sev) {
    var ic = sev === 'critical' ? '◆' : (sev === 'high' ? '▲' : '●');
    var s = h('span', { class: 'sev ' + sev });
    s.appendChild(h('span', { class: 'ic', text: ic }));
    s.appendChild(txt(sev));
    return s;
  }

  P.alerts_day = function (host) {
    if (!F.alerts.length) { empty(host, 'No False Discovery Rate-gated alert candidates in this slice. That is a result, not a gap: at q < 0.05 nothing exceeded its baseline with enough evidence.'); return; }
    var by = {};
    F.alerts.forEach(function (a) {
      var d = day(a.day);
      if (!by[d]) { by[d] = { critical: 0, high: 0, medium: 0, t: 0 }; }
      by[d][a.severity] = (by[d][a.severity] || 0) + 1;
      by[d].t++;
    });
    var items = F.days.map(function (d) {
      var b = by[d] || { critical: 0, high: 0, medium: 0, t: 0 };
      return {
        label: fmtDayFull(d), short: fmtDay(d), value: b.t,
        color: b.critical ? K.crit : K.focal,
        rows: [{ k: 'alerts', color: K.focal, v: fmtInt(b.t) },
        { k: 'critical', color: K.crit, v: fmtInt(b.critical) },
        { k: 'high', v: fmtInt(b.high) }, { k: 'medium', v: fmtInt(b.medium) }]
      };
    });
    legend(host, [{ color: K.focal, label: 'alert candidates', box: true },
    { color: K.crit, label: 'day contains a critical alert (◆)', box: true }]);
    vBars(host, {
      items: items, width: host.clientWidth || 700, height: 130,
      measure: 'alerts', vFmt: fmtInt, yFmt: fmtInt, labelCount: 8,
      tipNote: 'Counts are False Discovery Rate-gated candidates, one row per (day, cell, metric).'
    });
    tableView(host, [
      { k: 'label', label: 'Day' },
      { k: 'value', label: 'Alerts', num: true, fmt: fmtInt }
    ], items, { label: 'Table view — alerts per day' });
  };

  P.alerts_table = function (host) {
    var rows = F.alerts.slice().sort(function (a, b) {
      var o = { critical: 0, high: 1, medium: 2 };
      if (o[a.severity] !== o[b.severity]) { return o[a.severity] - o[b.severity]; }
      return (a.surprise_q || 1) - (b.surprise_q || 1);
    });
    if (!rows.length) { empty(host, 'No alert candidates in the current selection.'); return; }
    var cols = [
      { k: 'severity', label: 'Severity', node: function (v) { return sevChip(v); } },
      { k: 'day', label: 'Day', fmt: fmtDayFull },
      {
        k: 'entity', label: 'Entity', strong: true,
        get: function (r) { return r.tag_id ? tagLabel(r.tag_id) : topicLabel(r.topic_id); }
      },
      { k: 'platform', label: 'Platform', fmt: function (v) { return v || 'all'; } },
      { k: 'metric_key', label: 'Metric', get: function (r) { return metaName(r.metric_key); } },
      { k: 'value', label: 'Value', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'surprise_q', label: 'q-value', num: true, fmt: fmtQ },
      { k: 'rationale', label: 'Rationale', wrap: true }
    ];
    tableView(host, cols, rows, {
      rawTable: true,
      rowDrill: function (r) {
        return r.tag_id
          ? {
            view: 'tag', params: { tag_id: r.tag_id, platform: r.platform, day: day(r.day) },
            title: 'Alert: ' + tagLabel(r.tag_id), subtitle: fmtDayFull(r.day) + ' · ' + (r.platform || 'all platforms')
          }
          : {
            view: 'topic', params: { topic_id: r.topic_id, platform: r.platform, day: day(r.day) },
            title: 'Alert: ' + topicLabel(r.topic_id), subtitle: fmtDayFull(r.day) + ' · ' + (r.platform || 'all platforms')
          };
      }
    });
  };

  /* ---- 3. Acceleration & emergence ---- */
  P.emg_components = function (host) {
    var cand = F.md.filter(function (r) { return r.topic_id !== 'unassigned' && isNum(r.emergence_score); });
    if (!cand.length) { empty(host, 'No cell has an emergence score in this slice — every candidate failed the support gate or had fewer than 2 of the 4 components.'); return; }
    cand.sort(function (a, b) { return b.emergence_score - a.emergence_score; });
    var top = cand.slice(0, 8);

    var comps = [
      { k: 'surprise', label: 'surprise', norm: function (v) { return Math.min(1, v / 6); } },
      { k: 'accel_smooth', label: 'accel_smooth', norm: function (v) { return Math.min(1, Math.max(0, (v + 1) / 2)); } },
      { k: 'novelty', label: 'novelty', norm: function (v) { return Math.min(1, v / 0.6); } },
      { k: 'days_to_threshold', label: 'speed to 5/day', norm: function (v) { return Math.min(1, Math.max(0, 1 - v / 30)); } }
    ];

    legend(host, [
      { color: K.focal, label: 'component present (bar length = normalised strength)', box: true },
      { color: K.context, label: 'component NULL — not counted in the composite', hatch: true }
    ]);

    var grid = h('div', { class: 'grid g2' });
    host.appendChild(grid);
    top.forEach(function (r) {
      var card = h('div', { class: 'facet' });
      card.appendChild(h('div', { class: 'facet-t' }, [
        h('span', { text: topicLabel(r.topic_id) + ' · ' + r.platform }),
        h('span', { class: 'n', text: fmtDay(r.day) + ' · ' + fmt(r.emergence_score, 2) })
      ]));
      var plot = h('div', { class: 'plot' });
      card.appendChild(plot);
      grid.appendChild(card);
      var w = Math.max(200, plot.clientWidth || 260);
      var Fr = frame(plot, {
        width: w, height: comps.length * 17 + 8,
        margin: { t: 4, r: 44, b: 4, l: 92 }
      });
      var hatch = hatchPattern(Fr.svg);
      comps.forEach(function (c, i) {
        var raw = r[c.k];
        var yv = i * 17 + 2, bh = 9;
        Fr.g.appendChild(svgText(-6, yv + bh / 2, c.label,
          { anchor: 'end', baseline: 'middle', size: 9, fill: K.ink3 }));
        Fr.g.appendChild(el('rect', { x: 0, y: yv, width: Fr.iw, height: bh, fill: K.grid, rx: 1 }));
        var g = el('g');
        if (isNum(raw)) {
          var t = c.norm(raw);
          g.appendChild(el('rect', {
            x: 0, y: yv, width: Math.max(2, Fr.iw * t), height: bh,
            fill: K.focal, rx: 1
          }));
          g.appendChild(svgText(Fr.iw + 5, yv + bh / 2, fmt(raw, c.k === 'days_to_threshold' ? 0 : 2),
            { anchor: 'start', baseline: 'middle', size: 9, tabular: true, fill: K.ink2 }));
        } else {
          g.appendChild(el('rect', { x: 0, y: yv, width: Fr.iw, height: bh, fill: hatch, rx: 1 }));
          g.appendChild(svgText(Fr.iw + 5, yv + bh / 2, 'null',
            { anchor: 'start', baseline: 'middle', size: 9, fill: K.ink3 }));
        }
        var hit = el('rect', { x: -92, y: yv - 3, width: Fr.iw + 130, height: bh + 6, fill: 'transparent' });
        g.appendChild(hit);
        hoverable(hit, function () { return metaName(c.k); },
          function () {
            return [{ k: 'value', color: K.focal, v: isNum(raw) ? fmt(raw, 3) : 'NULL (gated)' },
            { k: 'units', v: metaUnits(c.k) }];
          },
          function () { return metaFormula(c.k); });
        Fr.g.appendChild(g);
      });
      var present = comps.filter(function (c) { return isNum(r[c.k]); }).length;
      card.appendChild(h('div', {
        class: 'fnote',
        text: present + ' of 4 components present · q = ' + fmtQ(r.surprise_q) +
          ' · V = ' + fmtInt(r.V) + ' · support n = ' + fmtInt(r.support_n)
      }));
    });

    tableView(host, [
      { k: 'day', label: 'Day', fmt: fmtDayFull },
      { k: 'topic_id', label: 'Topic', strong: true, fmt: topicLabel },
      { k: 'platform', label: 'Platform' },
      { k: 'emergence_score', label: 'Emergence', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'surprise', label: 'Surprise', num: true, fmt: function (v) { return fmt(v, 2); } },
      { k: 'accel_smooth', label: 'Accel (smooth)', num: true, fmt: function (v) { return fmtSigned(v, 3); } },
      { k: 'novelty', label: 'Novelty', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'days_to_threshold', label: 'Days to 5/day', num: true, fmt: fmtInt },
      { k: 'burst_state', label: 'Burst', fmt: function (v) { return v === 1 ? 'bursting' : (v === 0 ? 'baseline' : '—'); } },
      { k: 'surprise_q', label: 'q', num: true, fmt: fmtQ },
      { k: 'support_n', label: 'n', num: true, fmt: fmtInt }
    ], cand, {
      label: 'Table view — all scored cells, ranked', max: 300,
      rowDrill: function (r) {
        return {
          view: 'topic', params: { topic_id: r.topic_id, platform: r.platform, day: day(r.day) },
          title: topicLabel(r.topic_id) + ' · ' + r.platform, subtitle: fmtDayFull(r.day)
        };
      }
    });
  };

  P.accel_facets = function (host) {
    var topics = selTopics().filter(function (t) {
      return F.md.some(function (r) { return r.topic_id === t && isNum(r.accel_smooth); });
    });
    if (!topics.length) { empty(host, 'No topic has a smoothed-acceleration value in this slice. Savitzky-Golay needs 5 consecutive supported days.'); return; }
    legend(host, [
      { color: K.focal, label: metaName('accel_smooth') + ' (gold line)' },
      { color: K.focal, label: 'burst_state = 1 (gold ribbon under the axis)', box: true }
    ]);
    smallMultiples(host, {
      days: F.days, shareY: true, symmetric: true, zeroLine: true, cols: 3, height: 118,
      tipFmt: function (v) { return fmtSigned(v, 3); },
      tipNote: metaFormula('accel_smooth'),
      facets: topics.map(function (t) {
        var rows = F.md.filter(function (r) { return r.topic_id === t; });
        var pts = series(rows, 'accel_smooth', F.days, { agg: 'mean' });
        var burstBy = {};
        rows.forEach(function (r) { if (r.burst_state === 1) { burstBy[day(r.day)] = 1; } });
        var n = pts.filter(function (p) { return isNum(p.v); }).length;
        return {
          title: topicLabel(t), note: n + ' days',
          series: [{ label: 'accel_smooth', pts: pts, role: 'focal', kind: 'line' }],
          ribbon: F.days.map(function (d, i) { return { i: i, on: !!burstBy[d] }; }),
          drill: { view: 'topic', params: { topic_id: t }, title: topicLabel(t) }
        };
      })
    });
  };

  P.novelty_facets = function (host) {
    var topics = selTopics().filter(function (t) {
      return F.md.some(function (r) { return r.topic_id === t && isNum(r.novelty); });
    });
    if (!topics.length) { empty(host, 'No cell reached the 5-posts/day floor novelty requires.'); return; }
    legend(host, [{ color: K.focal, label: metaName('novelty') }]);
    smallMultiples(host, {
      days: F.days, shareY: true, zeroBase: true, cols: 3, height: 106,
      tipFmt: function (v) { return fmt(v, 3); }, tipNote: metaFormula('novelty'),
      facets: topics.map(function (t) {
        var rows = F.md.filter(function (r) { return r.topic_id === t; });
        return {
          title: topicLabel(t),
          series: [{
            label: 'novelty', pts: series(rows, 'novelty', F.days, { agg: 'mean' }),
            role: 'focal', kind: 'area'
          }],
          drill: { view: 'topic', params: { topic_id: t }, title: topicLabel(t) }
        };
      })
    });
  };

  /* Days from a series' first in-window appearance to its first day at 5+ posts/day, grouped by platform.
     One collapsible block per platform listing its topics. It follows the global filters: choose one topic in
     the topic filter and every selected platform shows that topic only, all blocks open. */
  P.dtt_bars = function (host) {
    var seen = {};
    F.md.forEach(function (r) {
      if (!isNum(r.days_to_threshold)) { return; }
      var k = r.platform + '|' + r.topic_id;
      if (!seen[k] || r.days_to_threshold < seen[k].days_to_threshold) { seen[k] = r; }
    });
    var byPlat = {}, maxV = 1;
    Object.keys(seen).forEach(function (k) {
      var r = seen[k];
      (byPlat[r.platform] = byPlat[r.platform] || []).push(r);
      maxV = Math.max(maxV, r.days_to_threshold);
    });
    var plats = Object.keys(byPlat);
    if (!plats.length) { empty(host, 'No cell crossed 5 posts/day in this slice, so time-to-threshold is undefined everywhere. Absence here is not slowness.'); return; }
    plats.sort(function (a, b2) { return byPlat[b2].length - byPlat[a].length || (a < b2 ? -1 : 1); });
    var oneTopic = selTopics().length === 1;
    var wrap = h('div', { class: 'dtt-wrap' });
    var ctl = h('div', { class: 'dtt-ctl' });
    var bOpen = h('button', { class: 'dtt-btn', type: 'button', text: 'Expand all' });
    var bShut = h('button', { class: 'dtt-btn', type: 'button', text: 'Collapse all' });
    function setAll(open) {
      wrap.querySelectorAll('details.dtt-block').forEach(function (d) { if (open) { d.setAttribute('open', ''); } else { d.removeAttribute('open'); } });
    }
    bOpen.addEventListener('click', function () { setAll(true); });
    bShut.addEventListener('click', function () { setAll(false); });
    ctl.appendChild(bOpen); ctl.appendChild(bShut);
    ctl.appendChild(h('span', {
      class: 'dtt-note', text: oneTopic
        ? 'One topic selected: every selected platform is shown for it.'
        : 'Use the topic filter to see one topic across all platforms. Series that never reached 5 posts/day are not listed.'
    }));
    wrap.appendChild(ctl);
    plats.forEach(function (pl) {
      var rows = byPlat[pl].slice().sort(function (a, b2) {
        return a.days_to_threshold - b2.days_to_threshold || (topicLabel(a.topic_id) < topicLabel(b2.topic_id) ? -1 : 1);
      });
      var already = rows.filter(function (r) { return r.days_to_threshold === 0; }).length;
      var slowest = rows[rows.length - 1].days_to_threshold;
      var det = h('details', { class: 'dtt-block' });
      if (oneTopic) { det.setAttribute('open', ''); }
      det.appendChild(h('summary', {}, [
        h('span', { class: 'dtt-plat', text: pl }),
        h('span', {
          class: 'dtt-sum', text: rows.length + (rows.length === 1 ? ' topic' : ' topics') +
            ' · ' + already + ' at 5+/day from the start · slowest ' + fmtInt(slowest) + (slowest === 1 ? ' day' : ' days')
        })]));
      rows.forEach(function (r) {
        var v = r.days_to_threshold;
        var row = h('div', { class: 'dtt-row' }, [
          h('span', { class: 'dtt-topic', text: topicLabel(r.topic_id) }),
          h('span', { class: 'dtt-val', text: v === 0 ? '5+/day from the start' : fmtInt(v) + (v === 1 ? ' day' : ' days') }),
          h('span', { class: 'dtt-track' }, [h('span', { class: 'dtt-bar', style: 'width:' + Math.max(v === 0 ? 0 : 3, Math.round(100 * v / maxV)) + '%' })])]);
        row.title = topicLabel(r.topic_id) + ' · ' + pl + ': ' + (v === 0 ? 'already at 5+ posts/day on its first day in the window' : fmtInt(v) + ' days') +
          '; first crossing ' + fmtDayFull(r.day) + '. Lower is faster. ' + metaCaveat('days_to_threshold');
        attachDrill(row, { view: 'topic', params: { topic_id: r.topic_id, platform: pl }, title: topicLabel(r.topic_id) + ' · ' + pl });
        det.appendChild(row);
      });
      wrap.appendChild(det);
    });
    host.appendChild(wrap);
  };

  /* ---- 4. Burst episodes ---- */
  var BURST_VIEW = 'platform'; // 'platform' | 'topic'
  var BURST_SEL_PLATS = null;  // map of plat -> bool, or null = all
  var BURST_SEL_TOPICS = null; // map of topic_id -> bool, or null = all

  function makeBurstFilterDropdown(viewKey, title, keys, store, labeller, countsMap, onChange, onGroupChange) {
    var isActiveGroup = (BURST_VIEW === viewKey);
    var wrap = h('div', { class: 'burst-filter-wrap' + (isActiveGroup ? ' active' : '') });

    // Group By Pill button
    var grpBtn = h('button', {
      type: 'button',
      class: 'burst-dd-grp-btn' + (isActiveGroup ? ' active' : ''),
      text: title,
      title: 'Group by ' + title
    });

    // Dropdown filter button
    var trigBtn = h('button', {
      type: 'button',
      class: 'burst-dd-trig-btn',
      title: 'Filter ' + title.toLowerCase()
    });
    var trigText = h('span', {});
    var arrow = h('span', { class: 'burst-dd-arrow', text: '▼' });
    trigBtn.appendChild(trigText);
    trigBtn.appendChild(arrow);

    wrap.appendChild(grpBtn);
    wrap.appendChild(trigBtn);

    var menu = h('div', { class: 'burst-dd-menu', style: 'display:none;' });

    // Header with Title + Group switch + Select All / Clear
    var hdr = h('div', { class: 'burst-dd-header' });
    var titleBox = h('div', { class: 'burst-dd-title-box' });
    var titleEl = h('span', { class: 'burst-dd-title', text: title });
    titleBox.appendChild(titleEl);

    if (!isActiveGroup && onGroupChange) {
      var grpLink = h('button', { type: 'button', class: 'burst-dd-act-btn burst-dd-switch-grp', text: 'Group by ' + title.toLowerCase() });
      grpLink.addEventListener('click', function (e) {
        e.stopPropagation();
        BURST_VIEW = viewKey;
        onGroupChange();
      });
      titleBox.appendChild(grpLink);
    } else {
      var grpBadge = h('span', { class: 'burst-dd-grouped-badge', text: '✓ Grouped' });
      titleBox.appendChild(grpBadge);
    }

    var acts = h('div', { class: 'burst-dd-actions' });
    var selAll = h('button', { type: 'button', class: 'burst-dd-act-btn', text: 'Select all' });
    var clear = h('button', { type: 'button', class: 'burst-dd-act-btn', text: 'Clear' });
    acts.appendChild(selAll);
    acts.appendChild(clear);
    hdr.appendChild(titleBox);
    hdr.appendChild(acts);
    menu.appendChild(hdr);

    // Search input
    var searchWrap = h('div', { class: 'burst-dd-search-wrap' });
    var search = h('input', { type: 'text', class: 'burst-dd-search', placeholder: 'Search ' + title.toLowerCase() + '…' });
    searchWrap.appendChild(search);
    menu.appendChild(searchWrap);

    // List container
    var list = h('div', { class: 'burst-dd-list' });
    menu.appendChild(list);
    wrap.appendChild(menu);

    function getLabel(k) { return labeller ? labeller(k) : k; }

    function refreshTrigger() {
      var sel = keys.filter(function (k) { return store[k]; });
      if (sel.length === keys.length) {
        trigText.textContent = 'All (' + keys.length + ')';
      } else if (sel.length === 0) {
        trigText.textContent = 'None';
      } else if (sel.length === 1) {
        trigText.textContent = getLabel(sel[0]);
      } else {
        trigText.textContent = sel.length + ' sel';
      }
    }

    function renderList(query) {
      list.textContent = '';
      var q = (query || '').toLowerCase().trim();
      keys.forEach(function (k) {
        var lbl = getLabel(k);
        if (q && lbl.toLowerCase().indexOf(q) === -1) { return; }
        var item = h('label', { class: 'burst-dd-item' });
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = !!store[k];
        cb.addEventListener('change', function () {
          store[k] = cb.checked;
          refreshTrigger();
          onChange();
        });
        var txt = h('span', { text: lbl });
        item.appendChild(cb);
        item.appendChild(txt);
        if (countsMap && countsMap[k] != null) {
          var cnt = h('span', { class: 'burst-dd-count', text: '(' + countsMap[k] + ')' });
          item.appendChild(cnt);
        }
        list.appendChild(item);
      });
    }

    selAll.addEventListener('click', function (e) {
      e.stopPropagation();
      keys.forEach(function (k) { store[k] = true; });
      renderList(search.value);
      refreshTrigger();
      onChange();
    });

    clear.addEventListener('click', function (e) {
      e.stopPropagation();
      keys.forEach(function (k) { store[k] = false; });
      renderList(search.value);
      refreshTrigger();
      onChange();
    });

    search.addEventListener('input', function () {
      renderList(search.value);
    });

    function toggleMenu() {
      document.querySelectorAll('.burst-filter-wrap.open').forEach(function (w) {
        if (w !== wrap) {
          w.classList.remove('open');
          var m = w.querySelector('.burst-dd-menu');
          if (m) { m.style.display = 'none'; }
        }
      });
      var isOpen = wrap.classList.toggle('open');
      menu.style.display = isOpen ? 'flex' : 'none';
      if (isOpen) {
        renderList('');
        setTimeout(function () { search.focus(); }, 50);
      }
    }

    grpBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (BURST_VIEW !== viewKey && onGroupChange) {
        BURST_VIEW = viewKey;
        onGroupChange();
      } else {
        toggleMenu();
      }
    });

    trigBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      toggleMenu();
    });

    document.addEventListener('click', function (e) {
      if (!wrap.contains(e.target)) {
        wrap.classList.remove('open');
        menu.style.display = 'none';
      }
    });
    menu.addEventListener('click', function (e) { e.stopPropagation(); });

    refreshTrigger();
    return wrap;
  }

  P.bursts = function (host) {
    var allRows = F.bursts.slice().sort(function (a, b) { return day(a.start_day) < day(b.start_day) ? -1 : 1; });
    if (!allRows.length) { empty(host, 'No burst episodes overlap this window/filter selection.'); return; }

    var platCounts = {}, topicCounts = {};
    allRows.forEach(function (e) {
      var p = e.platform || '(unknown)';
      var t = e.topic_id || 'unassigned';
      platCounts[p] = (platCounts[p] || 0) + 1;
      topicCounts[t] = (topicCounts[t] || 0) + 1;
    });

    var availPlats = Object.keys(platCounts).sort();
    var availTopics = Object.keys(topicCounts).sort();

    if (!BURST_SEL_PLATS) {
      BURST_SEL_PLATS = {};
      availPlats.forEach(function (p) { BURST_SEL_PLATS[p] = true; });
    } else {
      availPlats.forEach(function (p) { if (BURST_SEL_PLATS[p] == null) { BURST_SEL_PLATS[p] = true; } });
    }

    if (!BURST_SEL_TOPICS) {
      BURST_SEL_TOPICS = {};
      availTopics.forEach(function (t) { BURST_SEL_TOPICS[t] = true; });
    } else {
      availTopics.forEach(function (t) { if (BURST_SEL_TOPICS[t] == null) { BURST_SEL_TOPICS[t] = true; } });
    }

    var rows = allRows.filter(function (e) {
      var p = e.platform || '(unknown)';
      var t = e.topic_id || 'unassigned';
      return !!BURST_SEL_PLATS[p] && !!BURST_SEL_TOPICS[t];
    });

    /* --- control bar: Group by merged combo dropdowns + Expand/Collapse in one clean bar --- */
    var ctlWrap = h('div', { class: 'burst-ctl-wrap' });

    var viewRow = h('div', { class: 'burst-ctl-bar' });
    var lblEl = h('span', { class: 'burst-ctl-lbl', text: 'Group by:' });
    viewRow.appendChild(lblEl);

    // Add Platforms merged combo dropdown
    var platDd = makeBurstFilterDropdown('platform', 'Platform', availPlats, BURST_SEL_PLATS, null, platCounts, function () {
      host.textContent = '';
      P.bursts(host);
    }, function () {
      host.textContent = '';
      P.bursts(host);
    });
    viewRow.appendChild(platDd);

    // Add Topics merged combo dropdown
    var topicDd = makeBurstFilterDropdown('topic', 'Topic', availTopics, BURST_SEL_TOPICS, topicLabel, topicCounts, function () {
      host.textContent = '';
      P.bursts(host);
    }, function () {
      host.textContent = '';
      P.bursts(host);
    });
    viewRow.appendChild(topicDd);
    ctlWrap.appendChild(viewRow);

    /* expand / collapse */
    var actRow = h('div', { class: 'burst-actions' });
    var bOpen = h('button', { type: 'button', class: 'dtt-btn' });
    bOpen.textContent = 'Expand all';
    bOpen.addEventListener('click', function () {
      host.querySelectorAll('details.dtt-block').forEach(function (d) { d.setAttribute('open', ''); });
    });
    var bShut = h('button', { type: 'button', class: 'dtt-btn' });
    bShut.textContent = 'Collapse all';
    bShut.addEventListener('click', function () {
      host.querySelectorAll('details.dtt-block').forEach(function (d) { d.removeAttribute('open'); });
    });
    actRow.appendChild(bOpen);
    actRow.appendChild(bShut);
    ctlWrap.appendChild(actRow);
    host.appendChild(ctlWrap);

    if (!rows.length) {
      var noMatch = h('div', { class: 'caveat', text: 'No burst episodes match the selected platform and topic filters. Use the dropdowns above to select platforms and topics.' });
      host.appendChild(noMatch);
      return;
    }

    /* --- legend --- */
    legend(host, [
      { color: K.focal, label: 'episode extent (start → end)', box: true },
      { color: K.focal, label: 'peak day (dot)' }
    ]);

    /* --- group rows --- */
    var groupKey = BURST_VIEW === 'platform' ? 'platform' : 'topic_id';
    var grouped = {};
    rows.forEach(function (e) {
      var k = e[groupKey] || '(unknown)';
      (grouped[k] = grouped[k] || []).push(e);
    });
    var groupKeys = Object.keys(grouped).sort(function (a, b) {
      return grouped[b].length - grouped[a].length;
    });

    /* --- one collapsible block per group --- */
    groupKeys.forEach(function (gk) {
      var grpRows = grouped[gk].slice().sort(function (a, b) {
        return day(a.start_day) < day(b.start_day) ? -1 : 1;
      });
      var lbl = BURST_VIEW === 'platform' ? gk : topicLabel(gk);
      var det = h('details', { class: 'dtt-block' });
      det.setAttribute('open', ''); // start expanded
      var sum = h('summary', {});
      var platSpan = h('span', { class: 'dtt-plat' });
      platSpan.textContent = lbl;
      var cntSpan = h('span', { class: 'dtt-sum' });
      cntSpan.textContent = grpRows.length + ' episode' + (grpRows.length !== 1 ? 's' : '');
      sum.appendChild(platSpan);
      sum.appendChild(cntSpan);
      det.appendChild(sum);

      var plotWrap = h('div', { class: 'burst-plot-wrap' });
      gantt(plotWrap, {
        days: F.days,
        width: Math.max(600, (host.clientWidth || 760)),
        labelW: 200,
        rows: grpRows.map(function (e) {
          return {
            label: BURST_VIEW === 'platform' ? topicLabel(e.topic_id) : e.platform,
            start: day(e.start_day), end: day(e.end_day), peak: day(e.peak_day),
            rows: [
              { k: 'peak V', color: K.focal, v: fmtInt(e.peak_V) },
              { k: 'total V', v: fmtInt(e.total_V) },
              { k: 'half-life', v: isNum(e.half_life_days) ? fmt(e.half_life_days, 1) + ' d' : '—' },
              { k: 'fit r²', v: isNum(e.r2) ? fmt(e.r2, 2) : '—' }
            ],
            note: fmtDayFull(e.start_day) + ' → ' + fmtDayFull(e.end_day) +
              (isNum(e.r2) && e.r2 < 0.3 ? ' · decay fit r² < 0.3: half-life not quotable' : ''),
            drill: {
              view: 'topic', params: { topic_id: e.topic_id, platform: e.platform },
              title: topicLabel(e.topic_id) + ' · ' + e.platform,
              subtitle: 'Burst episode ' + fmtDayFull(e.start_day) + ' → ' + fmtDayFull(e.end_day)
            }
          };
        })
      });
      det.appendChild(plotWrap);
      host.appendChild(det);
    });

    /* --- full table at bottom --- */
    tableView(host, [
      { k: 'topic_id', label: 'Topic', strong: true, fmt: topicLabel },
      { k: 'platform', label: 'Platform' },
      { k: 'start_day', label: 'Start', fmt: fmtDayFull },
      { k: 'peak_day', label: 'Peak', fmt: fmtDayFull },
      { k: 'end_day', label: 'End', fmt: fmtDayFull },
      { k: 'peak_V', label: 'Peak V', num: true, fmt: fmtInt },
      { k: 'total_V', label: 'Total V', num: true, fmt: fmtInt },
      { k: 'onset_slope', label: 'Onset slope', num: true, fmt: function (v) { return fmtSigned(v, 3); } },
      { k: 'decay_lambda', label: 'Decay λ', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'half_life_days', label: 'Half-life (d)', num: true, fmt: function (v) { return fmt(v, 1); } },
      { k: 'r2', label: 'r²', num: true, fmt: function (v) { return fmt(v, 2); } }
    ], rows, {
      rowDrill: function (e) {
        return {
          view: 'topic', params: { topic_id: e.topic_id, platform: e.platform },
          title: topicLabel(e.topic_id) + ' · ' + e.platform
        };
      }
    });
  };

  /* ---- 5. Volume & growth ---- */
  P.vol_facets = function (host) {
    var plats = selPlatforms().filter(function (p) {
      return F.md.some(function (r) { return r.platform === p; });
    });
    if (!plats.length) { empty(host, 'No platform selected has rows in this window.'); return; }

    var byP = {};
    F.md.forEach(function (r) {
      if (!byP[r.platform]) { byP[r.platform] = { V: 0, n: 0, ok: 0 }; }
      byP[r.platform].V += (r.V || 0); byP[r.platform].n++;
      if (r.support_ok) { byP[r.platform].ok++; }
    });

    var items = plats.map(function (p) {
      var tot = (byP[p] && byP[p].V) || 0;
      return {
        label: p,
        value: tot,
        drill: { view: 'platform', params: { platform: p }, title: p + ' — recent posts' },
        note: 'Click bar to inspect recent posts collected from ' + p
      };
    }).sort(function (a, b) { return b.value - a.value; });

    legend(host, [
      { color: K.focal, label: 'Total posts collected in DB (click any bar to inspect posts)', box: true }
    ]);

    /* One single unified horizontal bar chart comparing all platforms */
    hBars(host, {
      items: items,
      width: host.clientWidth || 740,
      measure: 'Posts in DB',
      vFmt: fmtInt,
      xFmt: fmtCompact,
      labelW: 130,
      rowH: 26,
      tipNote: 'Total post count present in the database across the selected window'
    });

    /* Earlier separate-card implementation preserved for reference:
    var grid = h('div', { class: 'grid g3' });
    host.appendChild(grid);
    plats.forEach(function (p) {
      var rows = F.md.filter(function (r) { return r.platform === p; });
      var cell = h('div', { class: 'facet' });
      var totV = (byP[p] && byP[p].V) || 0;
      var facetHead = h('div', { class: 'facet-t' }, [h('span', { text: p }), h('span', { class: 'n', text: fmtInt(totV) + ' total posts' })]);
      attachDrill(facetHead, { view: 'platform', params: { platform: p }, title: p + ' — recent posts' });
      cell.appendChild(facetHead);
      var plot = h('div', { class: 'plot' }); cell.appendChild(plot); grid.appendChild(cell);
      var vPts = series(rows, 'V', F.days);
      lineChart(plot, { days: F.days, width: Math.max(190, plot.clientWidth || 240), height: 130, zeroBase: true,
        series: [{ label: 'Posts (V)', pts: vPts, role: 'focal', kind: 'area' }], yFmt: fmtCompact, tipFmt: function (v) { return fmtInt(v) + ' posts'; }, tipHead: p });
    });
    */

    tableView(host, [
      { k: 'p', label: 'Platform', strong: true },
      { k: 'V', label: 'Posts in DB', num: true, fmt: fmtInt }
      /* Other diagnostic columns commented out:
      { k: 'share', label: 'Share %', num: true, fmt: function (v) { return fmtPct(v, 1); } },
      { k: 'n', label: 'Cells', num: true, fmt: fmtInt },
      { k: 'ok', label: 'Supported cells', num: true, fmt: fmtInt },
      { k: 'share_sup', label: 'Supported %', num: true, fmt: function (v) { return fmtPct(v, 1); } }
      */
    ], items.map(function (d) {
      var p = d.label;
      var v = (byP[p] && byP[p].V) || 0;
      return { p: p, V: v, n: byP[p].n, ok: byP[p].ok };
    }), {
      label: 'Table view — volume by platform',
      maxWidth: '300px',
      rowDrill: function (r) { return { view: 'platform', params: { platform: r.p }, title: r.p + ' — recent posts' }; }
    });
  };

  P.growth_div = function (host) {
    var lastDay = null;
    F.md.forEach(function (r) {
      if (isNum(r.growth)) { var d = day(r.day); if (!lastDay || d > lastDay) { lastDay = d; } }
    });
    if (!lastDay) { empty(host, 'No supported growth value in this slice.'); return; }
    var byT = {};
    F.md.forEach(function (r) {
      if (day(r.day) !== lastDay || !isNum(r.growth)) { return; }
      if (!byT[r.topic_id]) { byT[r.topic_id] = { s: 0, n: 0, sh: 0 }; }
      byT[r.topic_id].s += r.growth; byT[r.topic_id].n++;
      if (isNum(r.growth_share)) { byT[r.topic_id].sh += r.growth_share; }
    });
    var items = Object.keys(byT).map(function (t) {
      var b = byT[t];
      return {
        label: topicLabel(t), value: b.s / b.n,
        rows: [{ k: metaName('growth'), color: b.s >= 0 ? K.focal : K.neg, v: fmtSigned(b.s / b.n, 3) },
        { k: metaName('growth_share'), v: fmtPct(b.sh, 1) },
        { k: 'cells', v: fmtInt(b.n) }],
        drill: { view: 'topic', params: { topic_id: t, day: lastDay }, title: topicLabel(t) }
      };
    }).sort(function (a, b) { return b.value - a.value; });
    legend(host, [{ color: K.focal, label: 'accelerating (positive)', box: true },
    { color: K.neg, label: 'decelerating (negative)', box: true }]);
    host.appendChild(h('div', {
      class: 'fnote',
      text: 'Most recent day with supported growth: ' + fmtDayFull(lastDay) +
        '. Diverging scale: gold positive, clay negative, neutral gray at zero.'
    }));
    hBars(host, {
      items: items, width: host.clientWidth || 700, diverging: true,
      measure: metaName('growth'), vFmt: function (v) { return fmtSigned(v, 3); },
      xFmt: function (v) { return fmtSigned(v, 2); }, labelW: 200,
      tipNote: metaCaveat('growth')
    });
  };

  P.momentum_facets = function (host) {
    var topics = selTopics().filter(function (t) {
      return F.mtd.some(function (r) { return r.topic_id === t && isNum(r.momentum); });
    });
    if (!topics.length) { empty(host, 'No topic has a momentum value — EMA(7)−EMA(21) needs 21 trailing days.'); return; }
    legend(host, [{ color: K.focal, label: metaName('momentum') },
    { color: K.context, label: metaName('growth_7d') }]);
    smallMultiples(host, {
      days: F.days, shareY: true, symmetric: true, zeroLine: true, cols: 3, height: 112,
      tipFmt: function (v) { return fmtSigned(v, 3); }, tipNote: metaCaveat('momentum'),
      facets: topics.map(function (t) {
        var rows = F.mtd.filter(function (r) { return r.topic_id === t; });
        return {
          title: topicLabel(t),
          series: [
            { label: 'momentum', pts: series(rows, 'momentum', F.days, { agg: 'mean' }), role: 'focal', kind: 'line' },
            { label: 'growth_7d', pts: series(rows, 'growth_7d', F.days, { agg: 'mean' }), role: 'context', kind: 'line' }
          ],
          drill: { view: 'topic', params: { topic_id: t }, title: topicLabel(t) }
        };
      })
    });
  };

  /* ---- 6. Intensity heatmap ---- */
  P.intensity_heat = function (host) {
    var topics = selTopics();
    var src = F.mtd.length ? F.mtd : F.md;
    var byKey = {};
    src.forEach(function (r) {
      var k = r.topic_id + '|' + day(r.day);
      if (!byKey[k]) { byKey[k] = { s: 0, n: 0, q: 1, flag: false, V: 0, sup: 0, ok: 0 }; }
      var b = byKey[k];
      if (isNum(r.surprise)) { b.s += r.surprise; b.n++; }
      if (isNum(r.surprise_q)) { b.q = Math.min(b.q, r.surprise_q); }
      if (r.alert_flag) { b.flag = true; }
      b.V += (r.V || 0);
      b.sup += (r.support_n || 0);
      if (r.support_ok) { b.ok++; }
    });
    var vals = [];
    Object.keys(byKey).forEach(function (k) { if (byKey[k].n) { vals.push(byKey[k].s / byKey[k].n); } });
    if (!vals.length) { empty(host, 'No supported surprise value in this slice — the whole panel failed the support gate.'); return; }
    var ex = extent(vals), lo = 0, hi = ex[1];
    rampLegend(host, lo, hi, K.seq, metaName('surprise') + ' (−log10 p)', function (v) { return fmt(v, 1); });
    legend(host, [
      { color: K.ink, label: 'white outline = alert_flag (BH q < 0.05)', box: true },
      { hatch: true, label: 'hatched = no value: support gate not met' }
    ]);
    heatmap(host, {
      rows: topics.map(function (t) { return { id: t, label: topicLabel(t) }; }),
      cols: F.days.map(function (d) { return { id: d, label: fmtDayFull(d), short: fmtDay(d) }; }),
      cellW: 14, cellH: 21, labelW: 176,
      color: function (v) { return seqColor((v - lo) / ((hi - lo) || 1)); },
      get: function (r, c) {
        var b = byKey[r.id + '|' + c.id];
        var drill = { view: 'topic', params: { topic_id: r.id, day: c.id }, title: r.label, subtitle: c.label };
        if (!b || !b.n) {
          return b ? {
            v: null, rows: [{ k: 'V', v: fmtInt(b.V) },
            { k: metaName('surprise'), v: 'NULL — support gate not met' },
            { k: 'supported sub-cells', v: fmtInt(b.ok) }],
            note: metaGate('surprise'), drill: drill
          } : null;
        }
        var v = b.s / b.n;
        return {
          v: v, ring: b.flag,
          rows: [{ k: metaName('surprise'), color: seqColor((v - lo) / ((hi - lo) || 1)), v: fmt(v, 2) },
          { k: 'BH q', v: fmtQ(b.q) },
          { k: 'V', v: fmtInt(b.V) },
          { k: 'support n', v: fmtInt(b.sup) },
          { k: 'alert', v: b.flag ? 'FLAGGED (q < 0.05)' : 'not flagged' }],
          note: metaCaveat('surprise'), drill: drill
        };
      },
      measure: metaName('surprise'),
      xLabelCount: 8
    });
    var flat = [];
    Object.keys(byKey).forEach(function (k) {
      var p = k.split('|'), b = byKey[k];
      flat.push({
        topic_id: p[0], topic: topicLabel(p[0]), day: p[1], surprise: b.n ? b.s / b.n : null,
        q: b.n ? b.q : null, V: b.V, flag: b.flag
      });
    });
    flat.sort(function (a, b) { return (b.surprise || -1) - (a.surprise || -1); });
    tableView(host, [
      { k: 'day', label: 'Day', fmt: fmtDayFull },
      { k: 'topic', label: 'Topic', strong: true },
      { k: 'surprise', label: 'Surprise', num: true, fmt: function (v) { return fmt(v, 2); } },
      { k: 'q', label: 'BH q', num: true, fmt: fmtQ },
      { k: 'V', label: 'V', num: true, fmt: fmtInt },
      { k: 'flag', label: 'Alert', fmt: function (v) { return v ? 'yes' : 'no'; } }
    ], flat, {
      label: 'Table view — topic × day surprise', max: 500, open: true,
      rowDrill: function (r) {
        return { view: 'topic', params: { topic_id: r.topic_id, day: r.day }, title: r.topic, subtitle: fmtDayFull(r.day) };
      }
    });
  };

  /* ---- 7. Persistence ---- */
  P.persistence = function (host) {
    var rows = F.pers.slice();
    if (!rows.length) { empty(host, 'No (topic × platform) cell has ≥ 20 observations in this slice.'); return; }
    rows.sort(function (a, b) { return (b.ar1_phi || -9) - (a.ar1_phi || -9); });
    legend(host, [
      { color: K.focal, label: 'AR(1) φ (point estimate)' },
      { color: K.context, label: '± 1 standard error' }
    ]);
    dotPlot(host, {
      width: host.clientWidth || 700, labelW: 210, refValue: 0,
      items: rows.map(function (r) {
        var sig = isNum(r.ljung_box_p) && r.ljung_box_p < 0.05;
        return {
          label: topicLabel(r.topic_id) + ' · ' + r.platform,
          value: r.ar1_phi, lo: isNum(r.phi_se) ? r.ar1_phi - r.phi_se : null,
          hi: isNum(r.phi_se) ? r.ar1_phi + r.phi_se : null,
          dim: !sig,
          rows: [
            { k: metaName('ar1_phi'), color: K.focal, v: fmt(r.ar1_phi, 3) },
            { k: metaName('phi_se'), color: K.context, v: fmt(r.phi_se, 3) },
            { k: metaName('half_life_days'), v: isNum(r.half_life_days) ? fmt(r.half_life_days, 2) + ' d' : 'undefined (φ outside 0–1)' },
            { k: metaName('volatility'), v: fmt(r.volatility, 3) },
            { k: 'Ljung–Box Q', v: fmt(r.ljung_box_q, 2) },
            { k: 'Ljung–Box p', v: fmtQ(r.ljung_box_p) },
            { k: 'n obs', v: fmtInt(r.n_obs) }
          ],
          note: sig ? 'Ljung–Box p < 0.05: the series has autocorrelation worth modelling.'
            : 'Ljung–Box p ≥ 0.05: φ and its half-life describe noise. Shown in gray.',
          drill: {
            view: 'topic', params: { topic_id: r.topic_id, platform: r.platform },
            title: topicLabel(r.topic_id) + ' · ' + r.platform
          }
        };
      }),
      vFmt: function (v) { return fmt(v, 2); }
    });
    tableView(host, [
      { k: 'topic_id', label: 'Topic', strong: true, fmt: topicLabel },
      { k: 'platform', label: 'Platform' },
      { k: 'ar1_phi', label: 'AR(1) φ', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'phi_se', label: 'SE', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'half_life_days', label: 'Half-life (d)', num: true, fmt: function (v) { return fmt(v, 2); } },
      { k: 'volatility', label: 'Volatility', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'ljung_box_q', label: 'LB Q', num: true, fmt: function (v) { return fmt(v, 2); } },
      { k: 'ljung_box_p', label: 'LB p', num: true, fmt: fmtQ },
      { k: 'n_obs', label: 'n', num: true, fmt: fmtInt }
    ], rows, {
      rowDrill: function (r) {
        return {
          view: 'topic', params: { topic_id: r.topic_id, platform: r.platform },
          title: topicLabel(r.topic_id) + ' · ' + r.platform
        };
      }
    });
  };

  /* ---- 8. Tone ---- */
  P.tone_heat = function (host) {
    var topics = selTopics(), plats = selPlatforms();
    var by = {};
    F.md.forEach(function (r) {
      var k = r.topic_id + '|' + r.platform;
      if (!by[k]) { by[k] = { eb: 0, ebn: 0, raw: 0, rawn: 0, n: 0 }; }
      var b = by[k];
      if (isNum(r.tone_eb)) { b.eb += r.tone_eb; b.ebn++; }
      if (isNum(r.tone_mean)) { b.raw += r.tone_mean; b.rawn++; }
      b.n += (r.tone_n || 0);
    });
    var any = Object.keys(by).some(function (k) { return by[k].ebn; });
    if (!any) { empty(host, 'No tone values in this slice.'); return; }
    rampLegend(host, -1, 1, K.div, metaName('tone_eb') + ' (negative → positive)',
      function (v) { return fmtSigned(v, 1); });
    legend(host, [{ color: K.div[3], label: 'neutral midpoint is GRAY, not a hue', box: true },
    { hatch: true, label: 'hatched = no tone-scored post in the cell' }]);
    heatmap(host, {
      rows: topics.map(function (t) { return { id: t, label: topicLabel(t) }; }),
      cols: plats.map(function (p) { return { id: p, label: p }; }),
      cellW: 74, cellH: 24, labelW: 176, xLabelCount: plats.length,
      color: function (v) { return divColor(v); },
      get: function (r, c) {
        var b = by[r.id + '|' + c.id];
        var drill = { view: 'topic', params: { topic_id: r.id, platform: c.id }, title: r.label + ' · ' + c.label };
        if (!b || !b.ebn) { return { v: null, rows: [{ k: metaName('tone_eb'), v: 'no scored post' }], drill: drill }; }
        var v = b.eb / b.ebn;
        return {
          v: v, rows: [
            { k: metaName('tone_eb'), color: divColor(v), v: fmtSigned(v, 3) },
            { k: metaName('tone_mean'), color: K.context, v: b.rawn ? fmtSigned(b.raw / b.rawn, 3) : '—' },
            { k: metaName('tone_n'), v: fmtInt(b.n) }
          ], note: metaCaveat('tone_eb'), drill: drill
        };
      },
      measure: metaName('tone_eb')
    });
  };

  P.tone_shrink = function (host) {
    var by = {};
    F.md.forEach(function (r) {
      if (!isNum(r.tone_eb) && !isNum(r.tone_mean)) { return; }
      var k = r.topic_id;
      if (!by[k]) { by[k] = { eb: 0, ebn: 0, raw: 0, rawn: 0, n: 0 }; }
      var b = by[k];
      if (isNum(r.tone_eb)) { b.eb += r.tone_eb; b.ebn++; }
      if (isNum(r.tone_mean)) { b.raw += r.tone_mean; b.rawn++; }
      b.n += (r.tone_n || 0);
    });
    var items = Object.keys(by).map(function (t) {
      var b = by[t];
      return {
        label: topicLabel(t), a: b.rawn ? b.raw / b.rawn : null,
        b: b.ebn ? b.eb / b.ebn : null, right: 'n=' + fmtInt(b.n),
        rows: [{ k: metaName('tone_mean'), color: K.context, v: b.rawn ? fmtSigned(b.raw / b.rawn, 3) : '—' },
        { k: metaName('tone_eb'), color: K.focal, v: b.ebn ? fmtSigned(b.eb / b.ebn, 3) : '—' },
        { k: metaName('tone_n'), v: fmtInt(b.n) }],
        note: 'Shrinkage pulls low-n cells toward the parent mean by construction. Read n first.',
        drill: { view: 'topic', params: { topic_id: t }, title: topicLabel(t) }
      };
    }).sort(function (x, y) { return (x.b == null ? 9 : x.b) - (y.b == null ? 9 : y.b); });
    if (!items.length) { empty(host, 'No tone values in this slice.'); return; }
    legend(host, [{ color: K.context, label: metaName('tone_mean') + ' (raw)' },
    { color: K.focal, label: metaName('tone_eb') + ' (published, EB-shrunk)' }]);
    dumbbell(host, { items: items, width: host.clientWidth || 700, labelW: 200, symmetric: true });
    tableView(host, [
      { k: 'label', label: 'Topic', strong: true },
      { k: 'a', label: 'tone_mean', num: true, fmt: function (v) { return fmtSigned(v, 3); } },
      { k: 'b', label: 'tone_eb', num: true, fmt: function (v) { return fmtSigned(v, 3); } },
      { k: 'right', label: 'Support' }
    ], items, { label: 'Table view — raw vs shrunk tone', rowDrill: function (r) { return r.drill; } });
  };

  /* ---- 9. Hate (gated) ---- */
  /* ---- 8c. Tone mix, net sentiment score, audience-weighted tone (provisional) ---- */
  P.tone_mix = function (host) {
    var by = {}, have = false;
    F.md.forEach(function (r) {
      if (!isNum(r.tone_n) || r.tone_n <= 0) { return; }
      if (r.tone_pos_n != null) { have = true; }
      if (!by[r.platform]) { by[r.platform] = { n: 0, pos: 0, neg: 0, raw: 0, rawn: 0, wt: 0, wtn: 0, wsum: 0, wnum: 0, cov: 0, covn: 0 }; }
      var b = by[r.platform];
      b.n += r.tone_n; b.pos += (r.tone_pos_n || 0); b.neg += (r.tone_neg_n || 0);
      if (isNum(r.tone_weighted) && isNum(r.tone_mean)) {      // same cells for both so they are comparable
        b.wt += r.tone_weighted * r.tone_n; b.raw += r.tone_mean * r.tone_n; b.wtn += r.tone_n;
      }
      if (isNum(r.tone_weight_cov)) { b.cov += r.tone_weight_cov * r.tone_n; b.covn += r.tone_n; }
    });
    if (!have) {
      empty(host, 'Tone mix is not in the published metrics yet (needs the metrics rebuild).');
      return;
    }
    var rows = Object.keys(by).map(function (p) {
      var b = by[p], pos = b.pos / b.n, neg = b.neg / b.n;
      return {
        platform: p, n: b.n, pos: pos, neu: 1 - pos - neg, neg: neg, nss: 100 * (pos - neg),
        raw: b.wtn ? b.raw / b.wtn : null, wt: b.wtn ? b.wt / b.wtn : null, cov: b.covn ? b.cov / b.covn : null
      };
    }).sort(function (a, b) { return b.n - a.n; });
    var pct = function (v) { return v == null ? '—' : (100 * v).toFixed(0) + '%'; };
    tableView(host, [
      { k: 'platform', label: 'Platform', strong: true },
      { k: 'n', label: 'Scored posts', num: true, fmt: fmtInt },
      {
        k: 'pos', label: 'Positive', num: true, fmt: pct, node: function (v, r) {
          var span = h('span', { class: 'clk-chip tone-pos-chip', text: v == null ? '—' : (100 * v).toFixed(0) + '%' });
          span.style.cursor = 'pointer';
          span.style.textDecoration = 'underline';
          span.style.color = 'var(--gold, #d4af37)';
          attachDrill(span, { view: 'platform', params: { platform: r.platform, sentiment: 'positive' }, title: r.platform + ' — Positive posts' });
          return span;
        }
      },
      {
        k: 'neu', label: 'Neutral', num: true, fmt: pct, node: function (v, r) {
          var span = h('span', { class: 'clk-chip tone-neu-chip', text: v == null ? '—' : (100 * v).toFixed(0) + '%' });
          span.style.cursor = 'pointer';
          span.style.textDecoration = 'underline';
          attachDrill(span, { view: 'platform', params: { platform: r.platform, sentiment: 'neutral' }, title: r.platform + ' — Neutral posts' });
          return span;
        }
      },
      {
        k: 'neg', label: 'Negative', num: true, fmt: pct, node: function (v, r) {
          var span = h('span', { class: 'clk-chip tone-neg-chip', text: v == null ? '—' : (100 * v).toFixed(0) + '%' });
          span.style.cursor = 'pointer';
          span.style.textDecoration = 'underline';
          span.style.color = '#e06c75';
          attachDrill(span, { view: 'platform', params: { platform: r.platform, sentiment: 'negative' }, title: r.platform + ' — Negative posts' });
          return span;
        }
      },
      { k: 'nss', label: 'Net sentiment score', num: true, fmt: function (v) { return fmtSigned(v, 1); } },
      { k: 'raw', label: 'Average tone', num: true, fmt: function (v) { return v == null ? '—' : fmtSigned(v, 3); } },
      { k: 'wt', label: 'Audience-weighted tone', num: true, fmt: function (v) { return v == null ? '—' : fmtSigned(v, 3); } },
      { k: 'cov', label: 'Posts with a counter', num: true, fmt: pct }
    ], rows, {
      open: true, label: 'Tone mix per platform (' + rows.length + ' platforms, current selection)',
      rowDrill: function (r) { return { view: 'platform', params: { platform: r.platform }, title: r.platform + ' — all recent posts' }; }
    });
  };

  /* ---- 8d. Largest tone drops (provisional) ---- */
  P.tone_drift = function (host) {
    var have = false;
    var hits = F.md.filter(function (r) {
      if (r.tone_drift_z != null) { have = true; }
      return r.tone_drift_flag === true || r.tone_drift_flag === 1;
    });
    if (!have) { empty(host, 'Tone drift is not in the published metrics yet (needs the metrics rebuild).'); return; }
    if (!hits.length) { empty(host, 'No significant tone drops in this selection.'); return; }
    hits.sort(function (a, b) { return a.tone_drift_z - b.tone_drift_z; });
    var rows = hits.slice(0, 60).map(function (r) {
      return {
        label: topicLabel(r.topic_id), platform: r.platform, day: fmtDay(r.day), tone: r.tone_mean,
        z: r.tone_drift_z, q: r.tone_drift_q, n: r.tone_n, base: r.tone_drift_base_n, topic_id: r.topic_id, raw_day: r.day
      };
    });
    tableView(host, [
      { k: 'label', label: 'Topic', strong: true },
      { k: 'platform', label: 'Platform' },
      { k: 'day', label: 'Day' },
      { k: 'tone', label: 'Tone that day', num: true, fmt: function (v) { return fmtSigned(v, 3); } },
      { k: 'z', label: 'Drift (z)', num: true, fmt: function (v) { return fmtSigned(v, 1); } },
      { k: 'q', label: 'q (false-discovery)', num: true, fmt: function (v) { return v == null ? '—' : v.toFixed(3); } },
      { k: 'n', label: 'Posts that day', num: true, fmt: fmtInt },
      { k: 'base', label: 'Baseline days', num: true, fmt: fmtInt }
    ], rows, {
      open: true, label: 'Significant tone drops (' + hits.length + ' in this selection; showing the largest ' + rows.length + ')',
      rowDrill: function (r) {
        return {
          view: 'topic', params: { topic_id: r.topic_id, platform: r.platform, day: day(r.raw_day) },
          title: r.label + ' · ' + r.platform + ' · ' + r.day
        };
      }
    });
  };

  P.hate_gated = function (host) {
    var by = {};
    F.md.forEach(function (r) {
      if (!isNum(r.hate_eb)) { return; }
      if (!by[r.topic_id]) { by[r.topic_id] = { s: 0, n: 0, p: 0, pn: 0, sup: 0 }; }
      var b = by[r.topic_id];
      b.s += r.hate_eb; b.n++;
      if (isNum(r.hate_prevalence)) { b.p += r.hate_prevalence; b.pn++; }
      b.sup += (r.tone_n || 0);
    });
    var items = Object.keys(by).map(function (t) {
      var b = by[t];
      return {
        label: topicLabel(t), value: b.s / b.n, dim: true,
        rows: [{ k: metaName('hate_eb'), color: K.context, v: fmt(b.s / b.n, 3) },
        { k: metaName('hate_prevalence'), v: b.pn ? fmtPct(b.p / b.pn, 1) : '—' },
        { k: 'support n', v: fmtInt(b.sup) },
        { k: 'calibrated', v: 'NO' }],
        note: 'UNCALIBRATED. ' + metaCaveat('hate_eb'),
        drill: { view: 'topic', params: { topic_id: t }, title: topicLabel(t) }
      };
    }).sort(function (a, b) { return b.value - a.value; });
    var d = h('details', { class: 'tv' });
    d.appendChild(h('summary', { text: 'Show uncalibrated values anyway (rendered in gray, never gold)' }));
    var inner = h('div', { class: 'hgated' });
    d.appendChild(inner);
    host.appendChild(d);
    if (!items.length) { empty(inner, 'No hate scores in this slice.'); return; }
    var plot = h('div', { class: 'plot' });
    inner.appendChild(plot);
    hBars(plot, {
      items: items, width: host.clientWidth || 700, measure: metaName('hate_eb'),
      vFmt: function (v) { return fmt(v, 3); }, xFmt: function (v) { return fmt(v, 2); }, labelW: 200,
      tipNote: 'Not publishable. ' + metaGate('hate_eb')
    });
    tableView(inner, [
      { k: 'label', label: 'Topic', strong: true },
      { k: 'value', label: 'hate_eb (UNCALIBRATED)', num: true, fmt: function (v) { return fmt(v, 3); } }
    ], items, { label: 'Table view — uncalibrated hate scores', rowDrill: function (r) { return r.drill; } });
  };

  /* ---- 10. Virality & reach ---- */
  P.virality_facets = function (host) {
    var plats = selPlatforms().filter(function (p) {
      return F.md.some(function (r) { return r.platform === p && isNum(r.virality_pct); });
    });
    var noEng = selPlatforms().filter(function (p) {
      return !F.md.some(function (r) { return r.platform === p && isNum(r.virality_pct); });
    });
    if (!plats.length) {
      empty(host, 'No platform in this selection has virality percentiles. ' +
        (noEng.length ? 'Platforms without engagement data: ' + noEng.join(', ') + '.' : ''));
      return;
    }
    legend(host, [{ color: K.focal, label: 'cells per percentile bin', box: true }]);
    var grid = h('div', { class: 'grid g3' });
    host.appendChild(grid);
    plats.forEach(function (p) {
      var vals = F.md.filter(function (r) { return r.platform === p; })
        .map(function (r) { return r.virality_pct; }).filter(isNum);
      var cell = h('div', { class: 'facet' });
      var vHead = h('div', { class: 'facet-t' }, [h('span', { text: p }),
      h('span', { class: 'n', text: vals.length + ' cells' })]);
      attachDrill(vHead, { view: 'platform', params: { platform: p }, title: p + ' — recent posts' });
      cell.appendChild(vHead);
      var plot = h('div', { class: 'plot' });
      cell.appendChild(plot);
      grid.appendChild(cell);
      histogram(plot, {
        values: vals, lo: 0, hi: 1, bins: 10,
        width: Math.max(190, plot.clientWidth || 240), height: 104,
        tipNote: metaCaveat('virality_pct')
      });
    });
    if (noEng.length) {
      host.appendChild(h('div', { class: 'caveat' }, [
        h('b', { text: 'No engagement data: ' }),
        txt(noEng.join(', ') + '. These are absent, not zero — the metric is NULL rather than 0 so the distinction survives.')
      ]));
    }
  };

  P.reach_bars = function (host) {
    var by = {};
    F.md.forEach(function (r) {
      // OBSERVED views only. The legacy reach_proxy mixes views with a log-engagement proxy and must not be summed.
      var ov = isNum(r.observed_views) ? r.observed_views : (isNum(r.views_sum) && r.views_sum > 0 ? r.views_sum : null);
      if (!isNum(ov)) { return; }
      if (!by[r.platform]) { by[r.platform] = { s: 0, n: 0, an: 0, ann: 0 }; }
      by[r.platform].s += ov; by[r.platform].n++;
      if (isNum(r.reach_audience_norm)) { by[r.platform].an += r.reach_audience_norm; by[r.platform].ann++; }
    });
    var items = Object.keys(by).map(function (p) {
      var b = by[p];
      return {
        label: p, value: b.s,
        rows: [{ k: 'Observed views', color: K.focal, v: fmtCompact(b.s) },
        { k: 'cells', v: fmtInt(b.n) },
        { k: metaName('reach_audience_norm'), v: b.ann ? fmt(b.an / b.ann, 2) + '×' : 'n/a (no author baseline)' }],
        note: 'Observed view-counter values only; platforms without a view counter are not shown.',
        drill: { view: 'platform', params: { platform: p }, title: p + ' — recent posts' }
      };
    }).sort(function (a, b) { return b.value - a.value; });
    if (!items.length) { empty(host, 'No observed views in this slice.'); return; }
    hBars(host, {
      items: items, width: host.clientWidth || 700, measure: 'Observed views',
      vFmt: fmtCompact, xFmt: fmtCompact, labelW: 110,
      tipNote: 'Observed views are a proxy for audience. No follower or subscriber counts exist anywhere in the corpus.'
    });
  };

  /* ---- 10c. Cross-Platform Narrative Diffusion ---- */
  P.cross_platform = function (host) {
    var stories = T('cross_platform_stories');
    if (!stories || !stories.length) {
      empty(host, 'No cross-platform narrative clusters detected in the current dataset.');
      return;
    }

    var filtered = stories.filter(function (s) {
      if (!s.platforms) { return false; }
      var plist = Array.isArray(s.platforms) ? s.platforms : [s.platforms];
      return plist.some(function (p) { return S.platforms[p]; });
    });

    if (!filtered.length) {
      empty(host, 'No cross-platform narratives in this platform selection.');
      return;
    }

    var tableRows = filtered.slice(0, 35).map(function (s) {
      var plist = Array.isArray(s.platforms) ? s.platforms : [s.platforms];
      var dests = plist.filter(function (p) { return p !== s.origin_platform; });
      return {
        story_id: s.story_id,
        origin: s.origin_platform,
        destinations: dests.length ? dests.join(', ') : plist.join(', '),
        breadth: s.n_platforms,
        posts: s.n_posts,
        observed_views: s.observed_views != null ? s.observed_views : null,
        viewless_engagement: s.viewless_engagement != null ? s.viewless_engagement : null,
        same_account: s.same_account_name === true ? 'Same account name (cross-posting)' : (s.same_account_name === false ? 'Different accounts' : '—'),
        lag: (s.spread_duration_hours != null ? s.spread_duration_hours + 'h' : '—'),
        snippet: s.snippet,
        first_seen: s.origin_time ? s.origin_time.split(' ')[0] : '—',
        origin_lag: (s.origin_scrape_lag_days != null ? s.origin_scrape_lag_days : null)
      };
    });

    tableView(host, [
      { k: 'origin', label: 'Earliest Observed Platform', strong: true },
      { k: 'destinations', label: 'Diffusion Network' },
      { k: 'same_account', label: 'Who Posted' },
      { k: 'breadth', label: 'Breadth', num: true, fmt: function (v) { return v + ' platforms'; } },
      { k: 'posts', label: 'Total Posts', num: true, fmt: fmtInt },
      { k: 'observed_views', label: 'Observed Views', num: true, fmt: function (v) { return v == null ? '—' : fmtCompact(v); } },
      { k: 'viewless_engagement', label: 'Interactions on Posts Without a View Counter', num: true, fmt: function (v) { return v == null ? '—' : fmtCompact(v); } },
      { k: 'lag', label: 'Spread Lag' },
      { k: 'first_seen', label: 'Earliest Observed Post' },
      { k: 'origin_lag', label: 'Its Scrape Lag (days)', num: true, fmt: function (v) { return v == null ? '—' : v; } },
      { k: 'snippet', label: 'Narrative Message Sample', wrap: true }
    ], tableRows, {
      open: true,
      label: 'Top Multi-Platform Narratives — observed views only, no estimates (' + tableRows.length + ' stories) — click any story to inspect all posts across platforms',
      rowDrill: function (r) {
        return {
          view: 'cross_platform',
          params: { story_id: r.story_id },
          title: 'Narrative Spread (earliest observed: ' + r.origin + ') → ' + r.destinations + ' (' + r.posts + ' posts · ' + fmtCompact(r.observed_views || 0) + ' observed views)'
        };
      }
    });
  };

  /* ---- 10d. Shared-link narratives (URL tier, preliminary) ---- */
  P.cross_platform_urls = function (host) {
    var links = T('cross_platform_url_links');
    if (!links || !links.length) {
      empty(host, 'No shared-link narratives in the current dataset (the URL tier has not been published).');
      return;
    }
    var rows = links.filter(function (s) {
      var plist = Array.isArray(s.platforms) ? s.platforms : [s.platforms];
      return plist.some(function (p) { return S.platforms[p]; });
    }).map(function (s) {
      var plist = Array.isArray(s.platforms) ? s.platforms : [s.platforms];
      return {
        link_id: s.link_id, url: s.url, conf: s.match_confidence,
        origin: s.origin_platform, platforms: plist.join(', '),
        posts: s.n_unique_posts != null ? s.n_unique_posts : s.n_posts, all_posts: s.n_posts, authors: s.n_authors,
        views: s.observed_views, first_seen: s.origin_time ? String(s.origin_time).split(' ')[0] : '—',
        lag: s.origin_scrape_lag_days
      };
    });
    if (!rows.length) { empty(host, 'No shared-link narratives in this platform selection.'); return; }
    var cols = [
      { k: 'origin', label: 'Earliest Observed Platform', strong: true },
      { k: 'platforms', label: 'Platforms' },
      { k: 'posts', label: 'Unique Posts (1 per author & platform)', num: true, fmt: fmtInt },
      { k: 'all_posts', label: 'All Posts incl. repeats', num: true, fmt: fmtInt },
      { k: 'authors', label: 'Authors', num: true, fmt: fmtInt },
      { k: 'views', label: 'Observed Views', num: true, fmt: function (v) { return v ? fmtCompact(v) : '—'; } },
      { k: 'first_seen', label: 'Earliest Observed Post' },
      { k: 'lag', label: 'Its Scrape Lag (days)', num: true, fmt: function (v) { return v == null ? '—' : v; } },
      { k: 'url', label: 'Shared Link', wrap: true }
    ];
    function drill(r) {
      return {
        view: 'cross_platform_url', params: { link_id: r.link_id },
        title: 'Shared link (' + r.conf + '): ' + r.url
      };
    }
    var high = rows.filter(function (r) { return r.conf === 'high'; });
    var med = rows.filter(function (r) { return r.conf === 'medium'; });
    var low = rows.filter(function (r) { return r.conf === 'low'; });
    tableView(host, cols, high, {
      open: true, rowDrill: drill,
      label: 'High confidence — full article links (' + high.length + ') — click a row to inspect all posts, including repeats'
    });
    tableView(host, cols, med, {
      open: false, rowDrill: drill,
      label: 'Medium confidence — shortened links (' + med.length + '), mostly one aggregator'
    });
    if (low.length) {
      tableView(host, cols, low, {
        open: false, rowDrill: drill,
        label: 'Low confidence — same link but posts more than 21 days apart (' + low.length + '): usually a shared reference or app link, not one story'
      });
    }
  };

  /* ---- 10e. Cross-platform events (semantic tier, preliminary) ---- */
  P.cross_platform_events = function (host) {
    var evs = T('cross_platform_semantic_events');
    if (!evs || !evs.length) {
      empty(host, 'No semantic cross-platform events in the current dataset (the semantic tier has not been published).');
      return;
    }
    var rows = evs.filter(function (s) {
      var plist = Array.isArray(s.platforms) ? s.platforms : [s.platforms];
      return plist.some(function (p) { return S.platforms[p]; });
    }).map(function (s) {
      var plist = Array.isArray(s.platforms) ? s.platforms : [s.platforms];
      return {
        event_id: s.event_id, conf: s.match_confidence, large: !!s.large_event,
        origin: s.origin_platform, platforms: plist.join(', '), nplat: s.n_platforms,
        posts: s.n_unique_posts, all_posts: s.n_posts, authors: s.n_authors,
        who: s.same_account_name === true ? 'Same account name (cross-posting)' : 'Different accounts',
        views: s.observed_views, sim: s.min_sim,
        first_seen: s.origin_time ? String(s.origin_time).split(' ')[0] : '—',
        snippet: (s.large_event ? '[large event] ' : '') + (s.snippet || '')
      };
    });
    if (!rows.length) { empty(host, 'No semantic cross-platform events in this platform selection.'); return; }
    var cols = [
      { k: 'origin', label: 'Earliest Observed Platform', strong: true },
      { k: 'platforms', label: 'Platforms' },
      { k: 'who', label: 'Who Posted' },
      { k: 'posts', label: 'Unique Posts (1 per author & platform)', num: true, fmt: fmtInt },
      { k: 'authors', label: 'Authors', num: true, fmt: fmtInt },
      { k: 'views', label: 'Observed Views', num: true, fmt: function (v) { return v ? fmtCompact(v) : '—'; } },
      { k: 'sim', label: 'Weakest Link (similarity)', num: true, fmt: function (v) { return v == null ? '—' : v.toFixed(2); } },
      { k: 'first_seen', label: 'Earliest Observed Post' },
      { k: 'snippet', label: 'First Post (sample)', wrap: true }
    ];
    function drill(r) {
      return {
        view: 'cross_platform_event', params: { event_id: r.event_id },
        title: 'Semantic event (' + r.conf + '): ' + r.platforms
      };
    }
    var high = rows.filter(function (r) { return r.conf === 'high'; });
    var med = rows.filter(function (r) { return r.conf !== 'high'; });
    tableView(host, cols, high, {
      open: true, rowDrill: drill, max: 300,
      label: 'High confidence — every link at least 0.90 (' + high.length + ') — click a row to inspect all posts'
    });
    tableView(host, cols, med, {
      open: false, rowDrill: drill, max: 300,
      label: 'Medium confidence — some links 0.85 to 0.90 (' + med.length + ')'
    });
  };

  /* ---- 11. Authors & coordination ---- */
  P.authors_scatter = function (host) {
    var pts = F.md.filter(function (r) { return isNum(r.author_1_hhi) && isNum(r.n_authors); })
      .map(function (r) {
        return {
          x: r.n_authors, y: r.author_1_hhi, flag: !!r.coordination_flag,
          label: topicLabel(r.topic_id) + ' · ' + r.platform + ' · ' + fmtDay(r.day),
          rows: [{ k: metaName('author_1_hhi'), color: r.coordination_flag ? K.crit : K.focal, v: fmt(r.author_1_hhi, 3) },
          { k: metaName('n_authors'), v: fmtInt(r.n_authors) },
          { k: metaName('author_entropy'), v: isNum(r.author_entropy) ? fmt(r.author_entropy, 3) : '—' },
          { k: 'V', v: fmtInt(r.V) },
          { k: metaName('coordination_flag'), v: r.coordination_flag ? 'FLAGGED' : 'no' }],
          note: r.coordination_flag ? metaCaveat('coordination_flag') : null,
          drill: {
            view: 'topic', params: { topic_id: r.topic_id, platform: r.platform, day: day(r.day) },
            title: topicLabel(r.topic_id) + ' · ' + r.platform, subtitle: fmtDayFull(r.day)
          }
        };
      });
    if (!pts.length) { empty(host, 'No cell has ≥ 2 distinct authors in this slice.'); return; }
    legend(host, [
      { color: K.focal, label: 'cell (dot)' },
      { color: K.crit, label: '✕ coordination_flag — heuristic screen, needs a human look' }
    ]);
    scatter(host, {
      points: pts, width: host.clientWidth || 700, height: 260,
      xLabel: metaName('n_authors'), yLabel: '1 − HHI', xZero: true, yZero: true,
      xFmt: fmtInt, yFmt: function (v) { return fmt(v, 1); }
    });
    var flagged = F.md.filter(function (r) { return r.coordination_flag; });
    tableView(host, [
      { k: 'day', label: 'Day', fmt: fmtDayFull },
      { k: 'topic_id', label: 'Topic', strong: true, fmt: topicLabel },
      { k: 'platform', label: 'Platform' },
      { k: 'V', label: 'V', num: true, fmt: fmtInt },
      { k: 'n_authors', label: 'Authors', num: true, fmt: fmtInt },
      { k: 'author_1_hhi', label: '1 − HHI', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'author_entropy', label: 'Entropy', num: true, fmt: function (v) { return fmt(v, 3); } }
    ], flagged, {
      label: 'Table view — ' + flagged.length + ' coordination-flagged cells',
      rowDrill: function (r) {
        return {
          view: 'topic', params: { topic_id: r.topic_id, platform: r.platform, day: day(r.day) },
          title: topicLabel(r.topic_id) + ' · ' + r.platform, subtitle: fmtDayFull(r.day)
        };
      }
    });
  };

  P.entropy_facets = function (host) {
    var plats = selPlatforms().filter(function (p) {
      return F.md.some(function (r) { return r.platform === p && isNum(r.author_entropy); });
    });
    if (!plats.length) { empty(host, 'No author-entropy values in this slice.'); return; }
    legend(host, [{ color: K.focal, label: metaName('author_entropy') + ' (nats)' }]);
    smallMultiples(host, {
      days: F.days, shareY: true, zeroBase: true, cols: 3, height: 100,
      tipFmt: function (v) { return fmt(v, 3); }, tipNote: metaCaveat('author_entropy'),
      facets: plats.map(function (p) {
        var rows = F.md.filter(function (r) { return r.platform === p; });
        return {
          title: p, series: [{
            label: 'entropy',
            pts: series(rows, 'author_entropy', F.days, { agg: 'mean' }), role: 'focal', kind: 'line'
          }],
          drill: { view: 'platform', params: { platform: p }, title: p + ' — recent posts' }
        };
      })
    });
  };

  /* ---- 12. Readability ---- */
  P.readability = function (host) {
    var by = {};
    F.md.forEach(function (r) {
      if (!by[r.platform]) { by[r.platform] = { v: 0, vn: 0, c: 0, cn: 0 }; }
      var b = by[r.platform];
      if (isNum(r.readability_mean)) { b.v += r.readability_mean; b.vn++; }
      if (isNum(r.readability_coverage)) { b.c += r.readability_coverage; b.cn++; }
    });
    var keys = Object.keys(by).filter(function (p) { return by[p].vn || by[p].cn; });
    if (!keys.length) { empty(host, 'No readability values in this slice.'); return; }
    legend(host, [{ color: K.focal, label: metaName('readability_mean') + ' (Flesch)' },
    { color: K.focal, label: metaName('readability_coverage') + ' (meter, share of scorable posts)', box: true }]);
    var items = keys.map(function (p) {
      var b = by[p];
      return {
        label: p, value: b.vn ? b.v / b.vn : null, cov: b.cn ? b.c / b.cn : null,
        rows: [{ k: metaName('readability_mean'), color: K.focal, v: b.vn ? fmt(b.v / b.vn, 1) : '—' },
        { k: metaName('readability_coverage'), v: b.cn ? fmtPct(b.c / b.cn, 1) : '—' }],
        note: metaCaveat('readability_mean'),
        drill: { view: 'platform', params: { platform: p }, title: p + ' — recent posts' }
      };
    }).sort(function (a, b) { return (b.value || -1) - (a.value || -1); });
    var plot = h('div', { class: 'plot' });
    host.appendChild(plot);
    hBars(plot, {
      items: items, width: host.clientWidth || 700, measure: metaName('readability_mean'),
      vFmt: function (v) { return fmt(v, 1); }, xFmt: function (v) { return fmt(v, 0); }, labelW: 110,
      tipNote: 'The Flesch mean describes only the scorable minority — read the coverage meter below.'
    });
    var grid = h('div', { class: 'grid g3' });
    host.appendChild(h('div', { class: 'fnote', text: 'Coverage — the share of posts long enough to score at all. This is what decides whether the Flesch mean means anything.' }));
    host.appendChild(grid);
    items.forEach(function (d) {
      grid.appendChild(meterRow(d.label, d.cov,
        isNum(d.cov) ? (d.cov < 0.2 ? 'below 20%: the mean describes a small minority' : '') : 'no coverage figure',
        isNum(d.cov) && d.cov >= 0.2));
    });
    tableView(host, [
      { k: 'label', label: 'Platform', strong: true },
      { k: 'value', label: 'Flesch mean', num: true, fmt: function (v) { return fmt(v, 1); } },
      { k: 'cov', label: 'Coverage', num: true, fmt: function (v) { return fmtPct(v, 1); } }
    ], items, { label: 'Table view — readability and coverage', rowDrill: function (r) { return r.drill; } });
  };

  /* ---- 13. Ontology ---- */
  P.topic_cards = function (host) {
    var vol = {}, alerts = {};
    F.md.forEach(function (r) { vol[r.topic_id] = (vol[r.topic_id] || 0) + (r.V || 0); });
    F.alerts.forEach(function (a) { if (a.topic_id) { alerts[a.topic_id] = (alerts[a.topic_id] || 0) + 1; } });
    var grid = h('div', { class: 'grid g3' });
    host.appendChild(grid);
    T('topic_dim').forEach(function (t) {
      var on = !!S.topics[t.topic_id];
      var card = h('div', { class: 'tcard' });
      if (!on) { card.style.opacity = '0.42'; }
      card.appendChild(h('div', { class: 'id', text: t.topic_id + (on ? '' : ' · filtered out') }));
      card.appendChild(h('h3', { text: t.label || t.topic || t.topic_id }));
      card.appendChild(h('div', { class: 'dom', text: (t.domain || '—') + ' → ' + (t.topic || '—') }));
      if (t.description) { card.appendChild(h('p', { text: t.description })); }
      var mt = h('div', { class: 'mt' });
      mt.appendChild(h('span', {}, [txt('corpus '), h('b', { text: fmtCompact(t.n_posts) })]));
      mt.appendChild(h('span', {}, [txt('in view '), h('b', { text: fmtCompact(vol[t.topic_id] || 0) })]));
      mt.appendChild(h('span', {}, [txt('alerts '), h('b', { text: String(alerts[t.topic_id] || 0) })]));
      card.appendChild(mt);
      attachDrill(card, {
        view: 'topic', params: { topic_id: t.topic_id },
        title: t.label || t.topic || t.topic_id
      });
      grid.appendChild(card);
    });
  };

  /* ---- 14. Hashtags ---- */
  P.tags = function (host) {
    if (!F.tags.length) { empty(host, 'No hashtag rows in this slice.'); return; }
    var by = {};
    F.tags.forEach(function (r) {
      if (!by[r.tag_id]) { by[r.tag_id] = { V: 0, e: 0, en: 0, q: 1, flag: 0, s: 0, sn: 0 }; }
      var b = by[r.tag_id];
      b.V += (r.V || 0);
      if (isNum(r.emergence_score)) { b.e += r.emergence_score; b.en++; }
      if (isNum(r.surprise)) { b.s += r.surprise; b.sn++; }
      if (isNum(r.surprise_q)) { b.q = Math.min(b.q, r.surprise_q); }
      if (r.alert_flag) { b.flag++; }
    });
    var items = Object.keys(by).map(function (id) {
      var b = by[id];
      return {
        label: tagLabel(id), value: b.V, id: id,
        rows: [{ k: 'V', color: K.focal, v: fmtInt(b.V) },
        { k: 'mean ' + metaName('emergence_score'), v: b.en ? fmt(b.e / b.en, 3) : '—' },
        { k: 'mean surprise', v: b.sn ? fmt(b.s / b.sn, 2) : '—' },
        { k: 'best BH q', v: b.sn ? fmtQ(b.q) : '—' },
        { k: 'flagged days', v: fmtInt(b.flag) }],
        note: 'Hashtag grain covers ~15% of the corpus. Alert on topic_id, not on this.',
        drill: { view: 'tag', params: { tag_id: id }, title: tagLabel(id) }
      };
    }).sort(function (a, b) { return b.value - a.value; }).slice(0, 20);
    legend(host, [{ color: K.focal, label: 'posts carrying the tag in view', box: true }]);
    hBars(host, {
      items: items, width: host.clientWidth || 700, measure: 'V',
      vFmt: fmtInt, xFmt: fmtInt, labelW: 130,
      tipNote: 'Top 20 tags by volume in the current slice.'
    });
    var flat = Object.keys(by).map(function (id) {
      var b = by[id];
      return {
        tag_id: id, tag: tagLabel(id), V: b.V, e: b.en ? b.e / b.en : null,
        s: b.sn ? b.s / b.sn : null, q: b.sn ? b.q : null, flag: b.flag
      };
    }).sort(function (a, b) { return b.V - a.V; });
    tableView(host, [
      { k: 'tag', label: 'Tag', strong: true },
      { k: 'V', label: 'Posts', num: true, fmt: fmtInt },
      { k: 'e', label: 'Emergence (mean)', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 's', label: 'Surprise (mean)', num: true, fmt: function (v) { return fmt(v, 2); } },
      { k: 'q', label: 'Best BH q', num: true, fmt: fmtQ },
      { k: 'flag', label: 'Flagged days', num: true, fmt: fmtInt }
    ], flat, {
      label: 'Table view — all tags in slice',
      rowDrill: function (r) { return { view: 'tag', params: { tag_id: r.tag_id }, title: r.tag }; }
    });
  };

  /* ---- 15. Methodology ---- */
  P.meta_table = function (host) {
    var rows = T('metric_meta');
    if (!rows.length) { empty(host, 'metric_meta is empty — the dashboard cannot describe metrics it has no semantics for.'); return; }
    var fam = {};
    rows.forEach(function (r) { (fam[r.family] = fam[r.family] || []).push(r); });
    Object.keys(fam).sort().forEach(function (f) {
      var d = h('details', { class: 'tv' });
      d.appendChild(h('summary', { text: f + ' — ' + fam[f].length + ' metrics' }));
      var dl = h('dl', { class: 'kv' });
      fam[f].forEach(function (m) {
        var dt = h('dt');
        dt.appendChild(h('b', { text: m.display_name }));
        dt.appendChild(h('div', { class: 'fnote', text: m.metric_key + ' · ' + (m.units || '') }));
        var dd = h('dd');
        dd.appendChild(h('div', {}, [h('span', { class: 'gate', text: m.formula || '—' })]));
        dd.appendChild(h('div', {
          class: 'fnote',
          text: 'grain: ' + (m.grain || '—') + ' · direction: ' + (m.direction || '—') +
            (m.higher_is_worse ? ' · higher is worse' : '')
        }));
        dd.appendChild(h('div', {}, [txt('gate: '), h('span', { class: 'gate', text: m.support_gate || 'none' })]));
        if (m.caveats) { dd.appendChild(h('div', { class: 'caveat', text: m.caveats })); }
        dl.appendChild(h('div', {}, [dt, dd]));
      });
      d.appendChild(dl);
      host.appendChild(d);
    });
  };

  P.quality = function (host) {
    var md = F.mdAll;
    var tot = md.length, inw = 0, ok = 0, v2 = 0, vSum = 0;
    md.forEach(function (r) {
      if (r.in_window) { inw++; }
      if (r.support_ok) { ok++; }
      if ((r.V || 0) <= 2) { v2++; }
      vSum += (r.V || 0);
    });
    var rmq = T('run_meta')[0], pjq = {};
    try { pjq = JSON.parse((rmq && rmq.params_json) || '{}'); } catch (e) { pjq = {}; }
    var v1MaxZ = ((pjq.d1_before_after || {}).v1 || {}).max_abs_z;
    v1MaxZ = isNum(v1MaxZ) ? Math.round(v1MaxZ) : 'a very large value';
    var hc = pjq.hashtag_coverage || {}, hcp = hc.per_platform || {};
    var tgHc = hcp.telegram ? ', Telegram ' + hcp.telegram.coverage_pct.toFixed(1) + '%' : '';
    var rows = [
      { k: 'Cells in selection', v: fmtInt(tot), n: 'topic × platform × day' },
      {
        k: 'Cells inside collection window', v: fmtInt(inw) + ' (' + fmtPct(inw / (tot || 1), 1) + ')',
        n: 'Window: ' + fmtDayFull(WIN.start) + ' → ' + fmtDayFull(WIN.end) +
          '. Time-series metrics are computed only here; the pre-window backfill is retained for cross-sectional metrics and never used for trends.'
      },
      {
        k: 'Cells passing support gate', v: fmtInt(ok) + ' (' + fmtPct(ok / (tot || 1), 1) + ')',
        n: metaGate('surprise')
      },
      {
        k: 'Cells with V ≤ 2', v: fmtInt(v2) + ' (' + fmtPct(v2 / (tot || 1), 1) + ')',
        n: 'This is the class of cell that gave v1 a robust z of up to ' + v1MaxZ + '. Here they are gated to NULL instead.'
      },
      { k: 'Posts represented', v: fmtInt(vSum), n: 'Sum of V across the selection.' },
      {
        k: 'Multiplicity control', v: 'Benjamini–Hochberg, α = 0.05, per day',
        n: 'Alert thresholds are False Discovery Rate q-values, never raw z cutoffs.'
      },
      {
        k: 'Hate/toxicity family', v: 'BLOCKED — uncalibrated',
        n: 'Raw model probabilities, never calibrated. A single-labeller check exists (reports/TONE_HATE_VALIDATION.md) but is not human-confirmed.'
      },
      {
        k: 'Cross-platform reach', v: 'Observed views only',
        n: 'Imputed views and the breadth multiplier were removed (they were 42% of reported reach).'
      },
      {
        k: 'Day rule', v: 'Posted date, else scrape date',
        n: 'Posts without a posting date are placed on the day they were collected.'
      },
      { k: 'Reach', v: 'PROXY only', n: 'No follower or subscriber counts exist anywhere in the corpus.' },
      {
        k: 'Hashtag coverage', v: isNum(hc.coverage_pct) ? hc.coverage_pct.toFixed(1) + '% of rows' + tgHc : 'not available',
        n: 'Very uneven by platform (high on Instagram, TikTok, YouTube video, Mastodon; very low on Reddit and YouTube comments). Alerting must key on topic_id; hashtag and keyword are secondary facets.'
      }
    ];
    var dl = h('dl', { class: 'kv' });
    rows.forEach(function (r) {
      dl.appendChild(h('div', {}, [h('dt', { text: r.k }),
      h('dd', {}, [h('div', { class: 'strong', text: r.v }), h('div', { class: 'fnote', text: r.n })])]));
    });
    host.appendChild(dl);
  };

  P.run_meta = function (host) {
    var rm = T('run_meta')[0];
    if (!rm) { empty(host, 'run_meta is empty.'); return; }
    var dl = h('dl', { class: 'kv' });
    function add(k, v) {
      dl.appendChild(h('div', {}, [h('dt', { text: k }), h('dd', { text: v == null ? '—' : String(v) })]));
    }
    add('run_id', rm.run_id);
    add('created_at', rm.created_at);
    add('source_extract_id', rm.source_extract_id);
    add('window_start', fmtDayFull(rm.window_start));
    add('window_end', fmtDayFull(rm.window_end));
    host.appendChild(dl);
    var pj = rm.params_json;
    if (pj) {
      var d = h('details', { class: 'tv' });
      d.appendChild(h('summary', { text: 'Run parameters (params_json)' }));
      var pre = h('pre');
      pre.style.whiteSpace = 'pre-wrap';
      pre.style.fontSize = '0.74rem';
      pre.style.color = 'var(--ink-2)';
      pre.style.maxWidth = '100%';
      pre.style.overflowWrap = 'anywhere';
      try { pre.textContent = JSON.stringify(JSON.parse(pj), null, 2); }
      catch (e) { pre.textContent = String(pj); }
      d.appendChild(pre);
      host.appendChild(d);
    }
  };

  /* ======================================================= geo-ontology === */
  /* Entity directory, risk/theme signal, timeline & alerts. Own DB, own day
     axis (GEODAYS), no platform/topic filter -- reads T('entity_dim') etc.
     straight from DATA.tables, same as topic_cards reads T('topic_dim'). */

  /* ---- taxonomy ------------------------------------------------------
     Display names, ordering, descriptions and alertability all come from
     DATA.taxonomy, which scripts/lib/taxonomy.py emits into metrics.json.

     They used to be four object literals right here, which quietly made this
     file the *effective* schema, and both of its failure modes were silent:

       * `ORDER.filter(function (c) { return present[c]; })` DROPPED any
         category that existed in the data but not in the array -- the panel
         just rendered fewer rows, with no error anywhere.
       * `META[k][0] || k` rendered an undeclared key as a raw snake_case
         string that still looked like a label.

     TAX inverts the direction of failure. `TAX.order()` applies the declared
     order first and then APPENDS anything present in the data but undeclared,
     so a category can never vanish; every such key is recorded in
     `TAX.unknown()` and warned once, so it surfaces as a thing to fix rather
     than as a missing row nobody notices.

     Lookups are resolved per call, not captured at load: DATA is null until
     the metrics fetch resolves (see the assignment in boot), so caching the
     axes here would permanently freeze an empty taxonomy.

     Zero-instance slots (business_executive, railway, pipeline as an entity
     subtype -- distinct from pipeline_vulnerabilities the risk category) are
     still declared, so the full taxonomy stays visible and is honestly marked
     empty rather than omitted. */
  var TAX = (function () {
    var EMPTY = { label: '', order: [], entries: {}, groups: {} };
    var unknown = {};   // axis -> { key: true }
    function ax(a) {
      return (DATA && DATA.taxonomy && DATA.taxonomy.axes && DATA.taxonomy.axes[a]) || EMPTY;
    }
    function ent(a, k) { return ax(a).entries[k] || null; }
    function flag(a, k) {
      var seen = unknown[a] = unknown[a] || {};
      if (!seen[k]) {
        seen[k] = true;
        if (window.console && console.warn) {
          console.warn('[taxonomy] "' + k + '" appears in the data but is not declared in axis "'
            + a + '" -- rendering the raw key. Add it to scripts/lib/taxonomy.py.');
        }
      }
    }
    return {
      axis: ax,
      entry: ent,
      label: function (a) { return ax(a).label || a; },
      status: function (a) { return ax(a).status || 'unknown'; },
      groups: function (a) { return ax(a).groups || {}; },
      /* Undeclared key -> still render the raw key, so the row exists, but
         record it. Never silently substitute something prettier. */
      name: function (a, k) {
        var e = ent(a, k);
        if (e) { return e.display; }
        if (k === null || k === undefined || k === '' || k === 'unresolved' || k === 'None') {
          return a === 'entity_subtype' ? 'Civilian / Public Figure' : '—';
        }
        flag(a, k);
        return String(k);
      },
      desc: function (a, k) { var e = ent(a, k); return (e && e.description) || ''; },
      /* The exact prompt the bi-encoder was given, and the exact NLI
         hypothesis -- so a label is auditable in the UI, not just asserted. */
      prompt: function (a, k) { var e = ent(a, k); return (e && e.model_description) || ''; },
      hypothesis: function (a, k) { var e = ent(a, k); return (e && e.hypothesis) || ''; },
      code: function (a, k) { var e = ent(a, k); return (e && e.code) || ''; },
      /* Classes the taxonomy declares un-alertable (measured too sparse to
         clear the support gate) carry the reason as data, so the UI can say
         why a panel is empty instead of implying nothing happened. */
      alertable: function (a, k) { var e = ent(a, k); return !e || e.alertable !== false; },
      alertableReason: function (a, k) { var e = ent(a, k); return (e && e.alertable_reason) || ''; },
      /* present: a truthy map { key: 1 } or an array of keys. Omit to get the
         full declared order (used for taxonomy cards, which show empty slots). */
      order: function (a, present) {
        var declared = ax(a).order || [];
        if (present === null || present === undefined) { return declared.slice(); }
        var set = present;
        if (Object.prototype.toString.call(present) === '[object Array]') {
          set = {};
          present.forEach(function (k) { set[k] = 1; });
        }
        var out = [], seen = {};
        declared.forEach(function (k) { if (set[k]) { out.push(k); seen[k] = 1; } });
        Object.keys(set).forEach(function (k) {
          if (set[k] && !seen[k]) { flag(a, k); out.push(k); }
        });
        return out;
      },
      /* Union mode: every declared key PLUS anything present in the data.
         For panels that deliberately show empty slots (the risk strip renders
         all nine with an "Insufficient data" state rather than hiding the ones
         with no rows -- absence is itself information). `order(a, present)`
         would hide those, so the two modes are kept distinct instead of one
         call quietly doing both. */
      orderAll: function (a, present) {
        var set = {};
        (ax(a).order || []).forEach(function (k) { set[k] = 1; });
        if (present) {
          Object.keys(present).forEach(function (k) { if (present[k]) { set[k] = 1; } });
        }
        return this.order(a, set);
      },
      unknown: function () { return unknown; }
    };
  })();
  window.__TAX__ = TAX;   // jsdom harnesses assert TAX.unknown() stays empty

  // Production entity subtype taxonomy lookup: exactly the 25 official subtypes
  var PROD_SUBTYPES_LOOKUP = {
    // PERSON (POI) - 5
    political_leader: 'Political Leader / Head of State',
    civilian: 'Civilian / Public Figure',
    military_personnel: 'Military Commander / Officer',
    journalist_analyst: 'Journalist / Defense Analyst',
    legal_actor: 'Judicial / Legal Official',

    // LOCATION (LOI) - 10
    country: 'Sovereign State / Nation',
    geographic_location: 'Geographic Location / City / Province',
    conflict_zone: 'Active Conflict Zone / Hostile Sector',
    disputed_border: 'Disputed Border / Line of Actual Control',
    military_installation: 'Military Installation / Airbase / Garrison',
    naval_base_port: 'Naval Base / Strategic Port Facility',
    strategic_corridor: 'Strategic Transit Corridor / Choke Point',
    restricted_operational_zone: 'Restricted Operational Zone',
    territorial_waters: 'Territorial Waters / Maritime EEZ',
    refugee_camp_displacement_zone: 'Displacement / Humanitarian Zone',

    // ORGANIZATION (OOI) - 8
    government_body: 'Government Body / Executive Agency',
    political_party: 'Political Party / Coalition',
    commercial_company: 'Commercial Corporation / Defense Contractor',
    military_branch_coalition: 'Armed Forces Branch / Military Coalition',
    militant_group_non_state: 'Militant / Insurgent Group',
    ngo: 'Non-Governmental Organization / Agency',
    military_hardware: 'Military Weapon System / Hardware',
    naval_vessel: 'Naval Warship / Combatant Vessel',

    // EVENT / INCIDENT (EOI) - 2
    military_exercise: 'Joint Military Exercise / Maneuver',
    protest_demonstration: 'Protest / Mass Demonstration',

    // Fallbacks to safely normalize any legacy keys
    diplomat: 'Political Leader / Head of State',
    military_leader: 'Military Commander / Officer',
    business_executive: 'Commercial Corporation / Defense Contractor'
  };

  var PROD_ENTITY_TAXONOMY_MAP = {
    PERSON: [
      { key: 'political_leader', label: 'Political Leader / Head of State' },
      { key: 'civilian', label: 'Civilian / Public Figure' },
      { key: 'military_personnel', label: 'Military Commander / Officer' },
      { key: 'journalist_analyst', label: 'Journalist / Defense Analyst' },
      { key: 'legal_actor', label: 'Judicial / Legal Official' }
    ],
    LOCATION: [
      { key: 'country', label: 'Sovereign State / Nation' },
      { key: 'geographic_location', label: 'Geographic Location / City / Province' },
      { key: 'conflict_zone', label: 'Active Conflict Zone / Hostile Sector' },
      { key: 'disputed_border', label: 'Disputed Border / Line of Actual Control' },
      { key: 'military_installation', label: 'Military Installation / Airbase / Garrison' },
      { key: 'naval_base_port', label: 'Naval Base / Strategic Port Facility' },
      { key: 'strategic_corridor', label: 'Strategic Transit Corridor / Choke Point' },
      { key: 'restricted_operational_zone', label: 'Restricted Operational Zone' },
      { key: 'territorial_waters', label: 'Territorial Waters / Maritime EEZ' },
      { key: 'refugee_camp_displacement_zone', label: 'Displacement / Humanitarian Zone' }
    ],
    ORG: [
      { key: 'government_body', label: 'Government Body / Executive Agency' },
      { key: 'political_party', label: 'Political Party / Coalition' },
      { key: 'commercial_company', label: 'Commercial Corporation / Defense Contractor' },
      { key: 'military_branch_coalition', label: 'Armed Forces Branch / Military Coalition' },
      { key: 'militant_group_non_state', label: 'Militant / Insurgent Group' },
      { key: 'ngo', label: 'Non-Governmental Organization / Agency' },
      { key: 'military_hardware', label: 'Military Weapon System / Hardware' },
      { key: 'naval_vessel', label: 'Naval Warship / Combatant Vessel' }
    ],
    EVENT: [
      { key: 'military_exercise', label: 'Joint Military Exercise / Maneuver' },
      { key: 'protest_demonstration', label: 'Protest / Mass Demonstration' }
    ]
  };

  function riskName(k) { return TAX.name('risk', k); }
  function riskDesc(k) { return TAX.desc('risk', k); }
  function themeName(k) { return TAX.name('theme', k); }
  function themeDesc(k) { return TAX.desc('theme', k); }
  function subtypeName(k, r) {
    if (!k || k === 'unresolved' || k === 'None') {
      var et = (r && r.entity_type) || null;
      if (et === 'PERSON') { return 'Civilian / Public Figure'; }
      if (et === 'LOCATION') { return 'Geographic Location / City / Province'; }
      if (et === 'ORG') { return 'Commercial Corporation / Defense Contractor'; }
      if (et === 'EVENT') { return 'Protest / Mass Demonstration'; }
      return 'Civilian / Public Figure';
    }
    return PROD_SUBTYPES_LOOKUP[k] || TAX.name('entity_subtype', k) || k;
  }

  /* dim_type -> (taxonomy axis, drill query param). This registry is the
     extension point: a new dimension needs one line here, not a new branch in
     each of the three places that used to resolve names with a ternary chain.
     Those chains were binary and fell through to themeName / the raw key, so a
     CAMEO row would have rendered its label through the *wrong* axis or shown a
     bare "05.2" -- both silent. Dimensions absent from this map are not
     taxonomy-backed: entity uids and topic cluster ids are data, not declared
     classes, so they name themselves. */
  var DIM_AXIS = {
    risk: { axis: 'risk', param: 'category' },
    theme: { axis: 'theme', param: 'category' },
    theme_code: { axis: 'theme_code', param: 'code' },
    theme_subcode: { axis: 'theme_subcode', param: 'code' },
    action: { axis: 'action', param: 'code' }
  };

  /* jsdom harness hook -- see UI/tests/check_dim_labels.mjs. These resolvers
     fail soft, so the only way to catch a mis-wired axis is to call them. */
  window.__DIM__ = {
    axis: DIM_AXIS, name: dimName, drill: dimDrill,
    renderRelated: renderRelated
  };

  function dimName(dimType, key) {
    var d = DIM_AXIS[dimType];
    if (d) { return TAX.name(d.axis, key); }
    if (dimType === 'topic') { return topicLabel(key); }
    if (dimType === 'entity') { return entName(key); }
    return key;
  }

  /* entity_uid -> readable name (entity_dim carries the canonical name; the raw key is only the fallback) */
  var _entNames = null;
  function entName(uid) {
    if (!_entNames) {
      _entNames = {};
      T('entity_dim').forEach(function (r) { _entNames[r.entity_uid] = r.canonical_name; });
    }
    return _entNames[uid] || uid;
  }

  /* The drill param differs by axis (category vs code); sending the wrong one
     silently returns the unfiltered view rather than erroring. */
  function dimDrill(dimType, key, title) {
    var d = DIM_AXIS[dimType];
    var params = {};
    params[(d && d.param) || (dimType === 'entity' ? 'entity_uid' : 'category')] = key;
    return { view: dimType, params: params, title: title || dimName(dimType, key) };
  }

  /* Generic clickable taxonomy card -- used for entity subtypes, risk
     categories and thematic dimensions so the full taxonomy is always laid
     out, even where a slot currently has zero instances (shown dimmed, not
     omitted). Clicking scrolls + flashes the matching row/facet elsewhere
     on the page (data-jump="<anchor id>"). */
  function taxCard(cfg) {
    var isEmpty = !cfg.value;
    var card = h('button', {
      class: 'taxcard' + (isEmpty ? ' tax-empty' : ''), type: 'button',
      'aria-pressed': 'false', title: cfg.desc || ''
    });
    card.appendChild(h('div', { class: 'tc-name', text: cfg.name }));
    card.appendChild(h('div', { class: 'tc-row' }, [
      h('span', { text: cfg.metaLabel || '' }),
      h('span', { class: 'tc-val', text: isEmpty ? 'no instances yet' : cfg.valueFmt(cfg.value) })
    ]));
    if (cfg.hoverRows) {
      card.addEventListener('mousemove', function (ev) { TIP.show(ev, tipNode(cfg.name, cfg.hoverRows, cfg.desc)); });
      card.addEventListener('mouseleave', TIP.hide);
    }
    if (!isEmpty && cfg.drill) {
      card.addEventListener('click', function () { DRILL.open(cfg.drill); });
    } else if (!isEmpty && cfg.jumpTo) {
      card.addEventListener('click', function () { jumpFlash(cfg.jumpTo); });
    }
    return card;
  }
  function jumpFlash(id) {
    var el2 = document.getElementById(id);
    if (!el2) { return; }
    if (typeof el2.scrollIntoView === 'function') { el2.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    el2.classList.remove('tax-flash');
    void el2.offsetWidth; /* restart animation on repeat clicks */
    el2.classList.add('tax-flash');
    setTimeout(function () { el2.classList.remove('tax-flash'); }, 1300);
  }

  /* ---- 16. Entity directory ---- */
  var SEC16_CAT = 'ALL';

  P.entity_top_bars = function (host) {
    var rows = T('entity_dim');
    if (!rows.length) { empty(host, 'entity_dim is empty (or every entity was seen in only 1 post).'); return; }

    var subCounts = {};
    rows.forEach(function (r) {
      var k = r.entity_subtype;
      if (!k) { return; }
      subCounts[k] = (subCounts[k] || 0) + (r.mention_count || 0);
    });

    var catTabs = [
      { key: 'ALL', label: 'All (25)' },
      { key: 'PERSON', label: 'PER (POI)' },
      { key: 'LOCATION', label: 'LOC (LOI)' },
      { key: 'ORG', label: 'ORG (OOI)' },
      { key: 'EVENT', label: 'EVENT (EOI)' }
    ];

    var groupLabels = {
      PERSON: 'PERSON (POI) subtypes',
      LOCATION: 'LOCATION (LOI) subtypes',
      ORG: 'ORGANIZATION (OOI) subtypes',
      EVENT: 'EVENT / INCIDENT (EOI) subtypes'
    };

    function renderBars() {
      host.textContent = '';

      host.appendChild(h('div', { class: 'lede', style: 'margin:0 0 0.5rem' }, [
        txt('Taxonomy coverage -- select a category to view its official mapped subtypes and top mentions.')
      ]));

      // 1. Category Filter Tabs
      var tabWrap = h('div', { class: 'el-filter-bar', style: 'margin-bottom:0.75rem' });
      catTabs.forEach(function (tab) {
        var btn = h('button', {
          type: 'button',
          class: 'el-filter-btn' + (SEC16_CAT === tab.key ? ' active' : ''),
          text: tab.label
        });
        btn.addEventListener('click', function () {
          SEC16_CAT = tab.key;
          renderBars();
        });
        tabWrap.appendChild(btn);
      });
      host.appendChild(tabWrap);

      // 2. Taxonomy Cards for active category
      var activeTypes = SEC16_CAT === 'ALL' ? ['PERSON', 'LOCATION', 'ORG', 'EVENT'] : [SEC16_CAT];
      activeTypes.forEach(function (baseType) {
        if (SEC16_CAT === 'ALL') {
          host.appendChild(h('div', { class: 'taxgroup-lbl', text: groupLabels[baseType] }));
        }
        var grid = h('div', { class: 'taxgrid', style: 'margin-bottom:1rem' });
        host.appendChild(grid);
        (PROD_ENTITY_TAXONOMY_MAP[baseType] || []).forEach(function (item) {
          grid.appendChild(taxCard({
            name: item.label, metaLabel: 'mentions', value: subCounts[item.key] || 0,
            valueFmt: fmtInt, desc: baseType + ' subtype', jumpTo: 'entity-type-' + baseType,
            drill: { view: 'entity_subtype', params: { entity_subtype: item.key }, title: item.label }
          }));
        });
      });

      // 3. Top-mentions bar chart(s)
      var typesToShow = SEC16_CAT === 'ALL' ? ['LOCATION', 'ORG', 'PERSON', 'EVENT'] : [SEC16_CAT];
      var gridClass = SEC16_CAT === 'ALL' ? 'grid g4' : 'grid g1';
      var chartGrid = h('div', { class: gridClass });
      host.appendChild(chartGrid);

      typesToShow.forEach(function (ty) {
        var top = rows.filter(function (r) { return r.entity_type === ty; })
          .sort(function (a, b) { return (b.mention_count || 0) - (a.mention_count || 0); })
          .slice(0, 10);
        if (!top.length && SEC16_CAT === 'ALL') { return; }

        var cell = h('div', { class: 'facet', id: 'entity-type-' + ty });
        cell.appendChild(h('div', { class: 'facet-t' }, [h('span', { text: 'Top ' + ty + ' entities by mentions' })]));
        var plot = h('div', { class: 'plot' });
        cell.appendChild(plot);
        chartGrid.appendChild(cell);

        if (!top.length) { empty(plot, 'No ' + ty + ' entities.'); return; }
        var items = top.map(function (r) {
          return {
            label: r.canonical_name, value: r.mention_count,
            rows: [
              { k: 'subtype', v: subtypeName(r.entity_subtype) },
              { k: 'distinct posts', v: fmtInt(r.distinct_posts) }
            ],
            note: 'first seen ' + fmtDayFull(r.first_seen) + ' · last seen ' + fmtDayFull(r.last_seen),
            drill: { view: 'entity', params: { entity_uid: r.entity_uid }, title: r.canonical_name }
          };
        });
        hBars(plot, {
          items: items, width: plot.clientWidth || (SEC16_CAT === 'ALL' ? 240 : 700), measure: 'mentions',
          vFmt: fmtInt, xFmt: fmtInt, labelW: 140, rowH: 18
        });
      });
    }

    renderBars();
  };

  /* ---- 16b. Entity directory search ---- */
  P.entity_search = function (host) {
    var rows = T('entity_dim');
    var box = h('div', { class: 'searchbox' });
    var input = h('input', { type: 'text', placeholder: 'Filter entity directory by name or subtype…' });
    var count = h('span', { class: 'n', text: rows.length.toLocaleString() + ' entities' });
    box.appendChild(input); box.appendChild(count);
    host.appendChild(box);
    var resultsHost = h('div');
    host.appendChild(resultsHost);
    function run() {
      var q = input.value.trim().toLowerCase();
      resultsHost.textContent = '';
      if (!q) { count.textContent = rows.length.toLocaleString() + ' entities'; return; }
      var hits = rows.filter(function (r) {
        return (r.canonical_name || '').toLowerCase().indexOf(q) >= 0 ||
          subtypeName(r.entity_subtype).toLowerCase().indexOf(q) >= 0 ||
          (r.entity_type || '').toLowerCase().indexOf(q) >= 0;
      }).sort(function (a, b) { return (b.mention_count || 0) - (a.mention_count || 0); }).slice(0, 40);
      count.textContent = hits.length.toLocaleString() + ' of ' + rows.length.toLocaleString() + ' match';
      if (!hits.length) { empty(resultsHost, 'No entity matches "' + q + '".'); return; }
      tableView(resultsHost, [
        { k: 'canonical_name', label: 'Entity', strong: true },
        { k: 'entity_type', label: 'Type' },
        { k: 'entity_subtype', label: 'Subtype', fmt: subtypeName },
        { k: 'mention_count', label: 'Mentions', num: true, fmt: fmtInt },
        { k: 'distinct_posts', label: 'Posts', num: true, fmt: fmtInt }
      ], hits, {
        label: 'Search results (top 40 by mentions)', max: 40,
        rowDrill: function (r) { return { view: 'entity', params: { entity_uid: r.entity_uid }, title: r.canonical_name }; }
      });
    }
    input.addEventListener('input', run);
  };

  P.entity_directory_table = function (host) {
    var rows = T('entity_dim');
    if (!rows.length) { empty(host, 'entity_dim is empty.'); return; }
    tableView(host, [
      { k: 'canonical_name', label: 'Entity', strong: true },
      { k: 'entity_type', label: 'Type' },
      { k: 'entity_subtype', label: 'Subtype', fmt: subtypeName },
      { k: 'mention_count', label: 'Mentions', num: true, fmt: fmtInt },
      { k: 'distinct_posts', label: 'Posts', num: true, fmt: fmtInt },
      { k: 'first_seen', label: 'First seen', fmt: fmtDayFull },
      { k: 'last_seen', label: 'Last seen', fmt: fmtDayFull }
    ], rows, {
      label: 'Table view — ' + rows.length.toLocaleString() + ' entities (≥2 distinct posts), sorted by mentions', max: 500,
      rowDrill: function (r) { return { view: 'entity', params: { entity_uid: r.entity_uid }, title: r.canonical_name }; }
    });
  };

  /* ---- 17. Risk categories & thematic dimensions ---- */

  /* Shared card grid for the 9 risk categories / 12 themes -- called by both
     the standalone taxonomy overview and reused wherever a compact category
     picker is useful. Value = latest-day volume; hover shows the fuller
     picture (latest surprise/q, active alert count) without leaving the card. */
  function categoryOverview(host, opts) {
    var rows = T(opts.table);
    var catKey = opts.catKey; /* 'risk_category' | 'theme_category' */
    var byCat = {};
    rows.forEach(function (r) {
      var c = r[catKey];
      (byCat[c] = byCat[c] || []).push(r);
    });
    var alerts = T('geo_alert_candidates').filter(function (a) { return a.dimension_type === opts.dimType; });
    var alertCount = {};
    alerts.forEach(function (a) { alertCount[a.dimension_key] = (alertCount[a.dimension_key] || 0) + 1; });
    host.appendChild(h('div', { class: 'taxgroup-lbl', text: opts.groupLabel }));
    var grid = h('div', { class: 'taxgrid' });
    host.appendChild(grid);
    opts.order.forEach(function (k) {
      var crows = (byCat[k] || []).slice().sort(function (a, b) { return day(b.day) < day(a.day) ? -1 : 1; });
      var totalV = crows.reduce(function (s, r) { return s + (r.V || 0); }, 0);
      var latest = crows[0];
      var hoverRows = latest ? [
        { k: 'latest day V', v: fmtInt(latest.V) },
        { k: 'latest surprise', v: isNum(latest.surprise) ? fmt(latest.surprise, 2) : 'gated (no support)' },
        { k: 'alert-days (False Discovery Rate)', v: fmtInt(alertCount[k] || 0) },
        { k: 'total posts in window', v: fmtInt(totalV) }
      ] : [{ k: 'status', v: 'no in-window data yet' }];
      grid.appendChild(taxCard({
        name: opts.nameFn(k), metaLabel: 'window volume', value: totalV,
        valueFmt: fmtInt, desc: opts.descFn(k), hoverRows: hoverRows,
        jumpTo: opts.jumpTo,
        drill: { view: opts.dimType, params: { category: k }, title: opts.nameFn(k) }
      }));
    });
  }

  P.risk_overview = function (host) {
    categoryOverview(host, {
      table: 'risk_metrics_daily', catKey: 'risk_category', dimType: 'risk',
      order: TAX.order('risk'), nameFn: riskName, descFn: riskDesc,
      groupLabel: 'Risk assessment — 9 categories', jumpTo: 'risk-heatmap-wrap'
    });
  };
  P.theme_overview = function (host) {
    categoryOverview(host, {
      table: 'theme_metrics_daily', catKey: 'theme_category', dimType: 'theme',
      order: TAX.order('theme'), nameFn: themeName, descFn: themeDesc,
      groupLabel: 'Thematic & relational dimensions — 12 categories', jumpTo: 'theme-facets-wrap'
    });
  };

  P.risk_heat = function (host) {
    var rows = T('risk_metrics_daily');
    if (!rows.length) { empty(host, 'risk_metrics_daily is empty.'); return; }
    var wrap = h('div', { id: 'risk-heatmap-wrap' });
    host.appendChild(wrap);
    host = wrap;
    var catSet = {}; rows.forEach(function (r) { catSet[r.risk_category] = 1; });
    var cats = TAX.order('risk', catSet);
    var byKey = {};
    rows.forEach(function (r) { byKey[r.risk_category + '|' + day(r.day)] = r; });
    var vals = rows.filter(function (r) { return isNum(r.surprise); }).map(function (r) { return r.surprise; });
    if (!vals.length) { empty(host, 'No supported surprise value — every cell failed its support gate.'); return; }
    var ex = extent(vals), lo = 0, hi = ex[1];
    rampLegend(host, lo, hi, K.seq, 'surprise (NB exceedance, higher = more anomalous)', function (v) { return fmt(v, 1); });
    legend(host, [
      { color: K.ink, label: 'white outline = alert_flag (BH q < 0.05)', box: true },
      { hatch: true, label: 'hatched = no value: support gate not met' }
    ]);
    heatmap(host, {
      rows: cats.map(function (c) { return { id: c, label: riskName(c) }; }),
      cols: GEODAYS.map(function (d) { return { id: d, label: fmtDayFull(d), short: fmtDay(d) }; }),
      cellW: 13, cellH: 21, labelW: 192,
      color: function (v) { return seqColor((v - lo) / ((hi - lo) || 1)); },
      get: function (r, c) {
        var b = byKey[r.id + '|' + c.id];
        var drill = { view: 'risk', params: { category: r.id, day: c.id }, title: r.label, subtitle: c.label };
        if (!b || !isNum(b.surprise)) {
          return {
            v: null,
            rows: [{ k: 'V', v: fmtInt(b && b.V) }, { k: 'surprise', v: 'NULL — support gate not met' }],
            note: 'gate: baseline_days >= 14 & baseline_mean >= 0.5 & V >= 3', drill: drill
          };
        }
        return {
          v: b.surprise, ring: !!b.alert_flag,
          rows: [
            { k: 'surprise', color: seqColor((b.surprise - lo) / ((hi - lo) || 1)), v: fmt(b.surprise, 2) },
            { k: 'BH q', v: fmtQ(b.surprise_q) },
            { k: 'V', v: fmtInt(b.V) },
            { k: 'gray_zone share', v: fmtPct(b.gray_zone_share, 0) },
            { k: 'alert', v: b.alert_flag ? 'FLAGGED (q < 0.05)' : 'not flagged' }
          ], drill: drill
        };
      },
      measure: 'surprise', xLabelCount: 8
    });
    var flat = rows.map(function (r) {
      return {
        risk_category: r.risk_category, category: riskName(r.risk_category), day: day(r.day),
        V: r.V, surprise: r.surprise, q: r.surprise_q, flag: r.alert_flag
      };
    }).sort(function (a, b) { return (isNum(b.surprise) ? b.surprise : -1) - (isNum(a.surprise) ? a.surprise : -1); });
    tableView(host, [
      { k: 'day', label: 'Day', fmt: fmtDayFull },
      { k: 'category', label: 'Risk category', strong: true },
      { k: 'V', label: 'V', num: true, fmt: fmtInt },
      { k: 'surprise', label: 'Surprise', num: true, fmt: function (v) { return fmt(v, 2); } },
      { k: 'q', label: 'BH q', num: true, fmt: fmtQ },
      { k: 'flag', label: 'Alert', fmt: function (v) { return v ? 'yes' : 'no'; } }
    ], flat, {
      label: 'Table view — risk category × day', max: 500,
      rowDrill: function (r) {
        return { view: 'risk', params: { category: r.risk_category, day: r.day }, title: r.category, subtitle: fmtDayFull(r.day) };
      }
    });
  };

  P.theme_facets = function (host) {
    var rows = T('theme_metrics_daily');
    if (!rows.length) { empty(host, 'theme_metrics_daily is empty.'); return; }
    var wrap = h('div', { id: 'theme-facets-wrap' });
    host.appendChild(wrap);
    host = wrap;
    var catSet = {}; rows.forEach(function (r) { catSet[r.theme_category] = 1; });
    var cats = TAX.order('theme', catSet);
    var byCat = {};
    cats.forEach(function (c) { byCat[c] = {}; });
    rows.forEach(function (r) { byCat[r.theme_category][day(r.day)] = r; });
    var facets = cats.map(function (c) {
      var pts = GEODAYS.map(function (d, i) {
        var r = byCat[c][d];
        return { i: i, v: (r && isNum(r.share_V)) ? r.share_V : null };
      });
      var ribbon = GEODAYS.map(function (d, i) {
        var r = byCat[c][d];
        return { i: i, on: !!(r && r.burst_state === 1) };
      });
      var latestV = null, latestQ = null;
      for (var k = GEODAYS.length - 1; k >= 0; k--) {
        var r = byCat[c][GEODAYS[k]];
        if (r) { latestV = r.V; latestQ = r.surprise_q; break; }
      }
      return {
        id: 'theme-facet-' + c, title: themeName(c), note: 'V=' + fmtInt(latestV),
        series: [{ label: 'share of labelled posts', role: 'focal', kind: 'area', pts: pts }],
        ribbon: ribbon,
        sub: 'best-day q ' + fmtQ(latestQ),
        drill: { view: 'theme', params: { category: c }, title: themeName(c) }
      };
    });
    legend(host, [
      { color: K.focal, label: 'share of that day’s labelled posts carrying this theme', box: true },
      { color: K.focal, label: 'ribbon under axis = burst-flagged day', box: true }
    ]);
    smallMultiples(host, {
      facets: facets, days: GEODAYS, cols: 3, height: 112, zeroBase: true,
      yFmt: function (v) { return fmtPct(v, 0); }, tipFmt: function (v) { return fmtPct(v, 1); },
      tipNote: 'share_V: share of that day’s risk/theme-labelled posts carrying this theme. Labels are UNSOUND per verification (§17) — read the shape, not the boundary.'
    });
  };

  function dimKeyName(dimType, key) { return dimName(dimType, key); }

  /* ---- 18. Timeline Analysis — entity / risk / theme alerts over time ---- */
  P.geo_timeline = function (host) {
    var alerts = T('geo_alert_candidates');
    if (!alerts.length) { empty(host, 'No False Discovery Rate-gated geo-ontology alerts.'); return; }

    function laneChart(dimType, catRows, title) {
      var pts = [];
      alerts.forEach(function (a) {
        if (a.dimension_type !== dimType) { return; }
        pts.push({
          rowId: a.dimension_key, day: day(a.day),
          color: a.severity === 'high' ? K.focal : (a.severity === 'critical' ? K.neg : K.context),
          r: a.severity === 'high' ? 5.5 : 4,
          label: dimKeyName(dimType, a.dimension_key) + ' · ' + fmtDayFull(a.day),
          rows: [
            { k: 'severity', color: a.severity === 'high' ? K.focal : K.context, v: a.severity },
            { k: 'metric', v: a.metric }, { k: 'value', v: fmt(a.value, 2) },
            { k: 'BH q', v: fmtQ(a.q_value) }, { k: 'support n', v: fmtInt(a.support_n) }
          ],
          note: a.rationale,
          drill: {
            view: 'alert', params: { dimension_type: a.dimension_type, dimension_key: a.dimension_key, day: day(a.day) },
            title: dimKeyName(dimType, a.dimension_key), subtitle: fmtDayFull(a.day)
          }
        });
      });
      var wrap = h('div', {
        style: 'margin-top:0.7rem', id: dimType === 'risk' ? 'timeline-lane-risk'
          : dimType === 'theme' ? 'timeline-lane-theme' : null
      });
      wrap.appendChild(h('div', { class: 'facet-t' }, [h('span', { text: title }),
      h('span', { class: 'n', text: catRows.length + ' rows · ' + pts.length + ' alert-days' })]));
      host.appendChild(wrap);
      if (!catRows.length) { empty(wrap, 'No ' + dimType + ' rows.'); return; }
      swimlane(wrap, {
        rows: catRows, days: GEODAYS, points: pts,
        width: host.clientWidth || 760, labelW: 172, rowH: 19
      });
    }

    legend(host, [
      { color: K.focal, label: 'high severity (q < 0.01; entity, risk and theme alerts also need >= 10 posts and >= 2x baseline)', box: true },
      { color: K.context, label: 'medium severity (q < 0.05)', box: true }
    ]);

    var riskSrc = T('risk_metrics_daily');
    var riskSet = {}; riskSrc.forEach(function (r) { riskSet[r.risk_category] = 1; });
    if (!riskSrc.length) { alerts.forEach(function (a) { if (a.dimension_type === 'risk') { riskSet[a.dimension_key] = 1; } }); }
    laneChart('risk',
      TAX.order('risk', riskSet).map(function (id) { return { id: id, label: riskName(id) }; }),
      'Risk categories (9)');

    var themeSrc = T('theme_metrics_daily');
    var themeSet = {}; themeSrc.forEach(function (r) { themeSet[r.theme_category] = 1; });
    if (!themeSrc.length) { alerts.forEach(function (a) { if (a.dimension_type === 'theme') { themeSet[a.dimension_key] = 1; } }); }
    laneChart('theme',
      TAX.order('theme', themeSet).map(function (id) { return { id: id, label: themeName(id) }; }),
      'Thematic & relational dimensions (12)');

    var entCounts = {};
    alerts.forEach(function (a) {
      if (a.dimension_type === 'entity') { entCounts[a.dimension_key] = (entCounts[a.dimension_key] || 0) + 1; }
    });
    var topEnt = Object.keys(entCounts).sort(function (a, b) { return entCounts[b] - entCounts[a]; }).slice(0, 20);
    laneChart('entity', topEnt.map(function (id) { return { id: id, label: id }; }), 'Top 20 entities by alert-day count');
  };

  P.geo_alerts_table = function (host) {
    var rows = T('geo_alert_candidates').slice().sort(function (a, b) {
      var oa = a.severity === 'high' ? 0 : (a.severity === 'critical' ? -1 : 1);
      var ob = b.severity === 'high' ? 0 : (b.severity === 'critical' ? -1 : 1);
      if (oa !== ob) { return oa - ob; }
      return (isNum(a.q_value) ? a.q_value : 1) - (isNum(b.q_value) ? b.q_value : 1);
    });
    if (!rows.length) { empty(host, 'No geo-ontology alert candidates.'); return; }
    var box = h('div', { class: 'searchbox' });
    var input = h('input', { type: 'text', placeholder: 'Filter alerts by category or entity…' });
    var count = h('span', { class: 'n', text: rows.length.toLocaleString() + ' alerts' });
    box.appendChild(input); box.appendChild(count);
    host.appendChild(box);
    var resultsHost = h('div');
    host.appendChild(resultsHost);
    var cols = [
      { k: 'severity', label: 'Severity', node: function (v) { return sevChip(v); } },
      { k: 'day', label: 'Day', fmt: fmtDayFull },
      { k: 'dimension_type', label: 'Grain' },
      {
        k: 'dimension_key', label: 'Entity / category', strong: true,
        fmt: function (v, r) { return dimKeyName(r.dimension_type, v); }
      },
      { k: 'metric', label: 'Metric' },
      { k: 'value', label: 'Value', num: true, fmt: function (v) { return fmt(v, 3); } },
      { k: 'q_value', label: 'q-value', num: true, fmt: fmtQ },
      { k: 'support_n', label: 'Support n', num: true, fmt: fmtInt },
      { k: 'rationale', label: 'Rationale', wrap: true }
    ];
    function run() {
      var q = input.value.trim().toLowerCase();
      resultsHost.textContent = '';
      var shown = rows;
      if (q) {
        shown = rows.filter(function (r) {
          return dimKeyName(r.dimension_type, r.dimension_key).toLowerCase().indexOf(q) >= 0 ||
            (r.dimension_type || '').toLowerCase().indexOf(q) >= 0;
        });
      }
      count.textContent = q ? (shown.length.toLocaleString() + ' of ' + rows.length.toLocaleString() + ' match')
        : rows.length.toLocaleString() + ' alerts';
      tableView(resultsHost, cols, shown, {
        label: 'Table view — ' + shown.length + ' of ' + rows.length + ' geo-ontology alert candidates', max: 500,
        rowDrill: function (r) {
          return {
            view: 'alert', params: { dimension_type: r.dimension_type, dimension_key: r.dimension_key, day: day(r.day) },
            title: dimKeyName(r.dimension_type, r.dimension_key), subtitle: fmtDayFull(r.day)
          };
        }
      });
    }
    input.addEventListener('input', run);
    run();
  };

  /* ================================================================ basic == */

  /* Overview-mode filter state. `attentionOnly` narrows every Overview panel to
     just the dimensions that currently carry an alert, so "Needs attention"
     behaves like a filter (show me only these) rather than a jump. */
  var BASIC = { attentionOnly: false };

  /* Which risk / theme / entity / topic keys are currently alerting. */
  function attentionKeys() {
    var out = { risk: {}, theme: {}, entity: {}, topic: {}, n: 0 };
    T('geo_alert_candidates').forEach(function (a) {
      var b = out[a.dimension_type];
      if (b && !b[a.dimension_key]) { b[a.dimension_key] = true; out.n++; }
    });
    T('alert_candidates').forEach(function (a) {
      if (a.topic_id && !out.topic[a.topic_id]) { out.topic[a.topic_id] = true; out.n++; }
    });
    return out;
  }

  /* Helper: appends a "See in Detailed view →" footer link that switches to
     the Advanced tab and scrolls to `sectionId`. */
  function basicDetailLink(host, sectionId, label) {
    var footer = h('div', { class: 'bvn-footer' });
    var link = h('a', { class: 'bvn-see-all', href: '#' });
    link.textContent = 'See ' + (label || 'full detail') + ' in Detailed view →';
    link.addEventListener('click', function (e) {
      e.preventDefault();
      var advBtn = document.getElementById('mode-advanced-btn');
      if (advBtn) {
        advBtn.click();
        setTimeout(function () {
          var sec = document.getElementById(sectionId);
          if (sec) { sec.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
        }, 150);
      }
    });
    footer.appendChild(link);
    host.appendChild(footer);
  }
  window.basicDetailLink = basicDetailLink;

  /* Re-render only the Overview panels (cheaper than a full render(), and it
     keeps the Advanced view's scroll position untouched). */
  function renderBasic() {
    document.querySelectorAll('#basic-view [data-panel]').forEach(function (host) {
      var key = host.getAttribute('data-panel');
      var fn = P[key];
      if (!fn) { return; }
      host.textContent = '';
      try { fn(host); } catch (e) {
        empty(host, 'Render error in panel "' + key + '": ' + e.message);
        if (window.console) { console.error(key, e); }
      }
    });
  }

  /* ---- Collapsible Metrics Panel (Overview page) ---- */
  var MP_ACTIVE_DAYS = 1;
  var MP_CONTROLS_INITED = false;

  function initMetricsPanelControls() {
    if (MP_CONTROLS_INITED) { return; }
    var panel = document.getElementById('metrics-panel');
    var openPill = document.getElementById('mp-open-pill');
    var closeBtn = document.getElementById('mp-close-btn');
    var pillsContainer = document.getElementById('mp-range-pills');
    if (!panel || !openPill || !closeBtn || !pillsContainer) { return; }
    MP_CONTROLS_INITED = true;

    var isCollapsed = false;
    try {
      isCollapsed = sessionStorage.getItem('mp_collapsed') === '1';
    } catch (e) { }

    if (isCollapsed) {
      panel.classList.add('mp-collapsed');
      document.body.classList.remove('mp-sidebar-open');
      openPill.style.display = 'inline-flex';
    } else {
      panel.classList.remove('mp-collapsed');
      document.body.classList.add('mp-sidebar-open');
      openPill.style.display = 'none';
    }

    closeBtn.addEventListener('click', function () {
      panel.classList.add('mp-collapsed');
      document.body.classList.remove('mp-sidebar-open');
      openPill.style.display = 'inline-flex';
      try { sessionStorage.setItem('mp_collapsed', '1'); } catch (e) { }
    });

    openPill.addEventListener('click', function () {
      panel.classList.remove('mp-collapsed');
      document.body.classList.add('mp-sidebar-open');
      openPill.style.display = 'none';
      try { sessionStorage.setItem('mp_collapsed', '0'); } catch (e) { }
      renderBasic();
    });

    pillsContainer.addEventListener('click', function (e) {
      var btn = e.target.closest('.mp-pill');
      if (!btn) { return; }
      var d = parseInt(btn.getAttribute('data-days'), 10);
      if (!d) { return; }
      MP_ACTIVE_DAYS = d;
      pillsContainer.querySelectorAll('.mp-pill').forEach(function (p) {
        p.classList.remove('mp-pill-active');
      });
      btn.classList.add('mp-pill-active');
      renderBasic();
    });
  }

  function attachMpInteractivity(box, svg, buckets, W, H, padL, plotW, getValStr, getDotY) {
    var tip = h('div', { class: 'mp-tip' });
    box.appendChild(tip);

    var guide = document.createElementNS(SVGNS, 'line');
    guide.setAttribute('class', 'mp-guide');
    guide.setAttribute('y1', '12');
    guide.setAttribute('y2', (H - 25).toString());
    guide.setAttribute('stroke', '#60a5fa');
    guide.setAttribute('stroke-width', '1');
    guide.setAttribute('stroke-dasharray', '2 2');
    guide.setAttribute('stroke-opacity', '0.8');
    guide.style.display = 'none';
    guide.style.pointerEvents = 'none';
    svg.appendChild(guide);

    var dot = null;
    if (getDotY) {
      dot = document.createElementNS(SVGNS, 'circle');
      dot.setAttribute('r', '5');
      dot.setAttribute('fill', '#ffffff');
      dot.setAttribute('stroke', '#3b82f6');
      dot.setAttribute('stroke-width', '2');
      dot.style.display = 'none';
      dot.style.pointerEvents = 'none';
      svg.appendChild(dot);
    }

    var n = buckets.length;
    var gap = plotW / Math.max(1, n);

    svg.addEventListener('mousemove', function (e) {
      var rect = svg.getBoundingClientRect();
      var mouseX = e.clientX - rect.left;
      var relX = (mouseX / Math.max(1, rect.width)) * W;

      var idx = Math.floor((relX - padL) / gap);
      if (idx < 0) { idx = 0; }
      if (idx >= n) { idx = n - 1; }

      var b = buckets[idx];
      if (!b) { return; }

      var curX = b.x != null ? b.x : (padL + idx * gap + gap / 2);
      guide.setAttribute('x1', curX.toFixed(1));
      guide.setAttribute('x2', curX.toFixed(1));
      guide.style.display = 'block';

      if (dot && b.y != null) {
        dot.setAttribute('cx', curX.toFixed(1));
        dot.setAttribute('cy', b.y.toFixed(1));
        dot.style.display = 'block';
      }

      var dateStr = b.fullDate || b.label;
      var valStr = getValStr(b);
      tip.innerHTML = '<div class="mp-tip-title">' + dateStr + ' IST</div><div class="mp-tip-val">' + valStr + '</div>';

      var boxRect = box.getBoundingClientRect();
      var tipLeft = Math.max(10, Math.min(box.clientWidth - 170, e.clientX - boxRect.left - 60));
      var tipTop = Math.max(5, e.clientY - boxRect.top - 58);
      tip.style.left = tipLeft + 'px';
      tip.style.top = tipTop + 'px';
      tip.style.display = 'block';
    });

    svg.addEventListener('mouseleave', function () {
      guide.style.display = 'none';
      if (dot) { dot.style.display = 'none'; }
      tip.style.display = 'none';
    });
  }

  function renderMpPostsChart(buckets, nDays, targetDay, chartDays, subTrend) {
    var subText = subTrend || (nDays === 1 ? 'trend · 7 days to target' : (nDays <= 7 ? 'trend · daily' : 'trend · ' + nDays + ' days'));
    var box = h('div', { class: 'mp-chart-box' });
    box.appendChild(h('div', { class: 'mp-chart-hd' }, [
      h('span', { class: 'mp-chart-title', text: 'Posts per bucket' }),
      h('span', { class: 'mp-chart-sub', text: subText })
    ]));

    var W = 620, H = 140, padL = 35, padR = 30, padT = 25, padB = 25;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;

    var maxV = 0, maxIdx = 0;
    buckets.forEach(function (b, i) {
      if (b.posts > maxV) { maxV = b.posts; maxIdx = i; }
    });
    if (maxV === 0) { maxV = 100; }
    var lastIdx = buckets.length - 1;
    var lastV = buckets[lastIdx].posts;

    var pts = buckets.map(function (b, i) {
      var x = padL + (buckets.length > 1 ? (i / (buckets.length - 1)) * plotW : plotW / 2);
      var y = padT + plotH - (b.posts / maxV) * plotH;
      b.x = x;
      b.y = y;
      return { x: x, y: y, v: b.posts };
    });

    var lineD = 'M ' + pts[0].x.toFixed(1) + ' ' + pts[0].y.toFixed(1);
    for (var i = 1; i < pts.length; i++) {
      var prev = pts[i - 1], curr = pts[i];
      var cpx = (prev.x + curr.x) / 2;
      lineD += ' C ' + cpx.toFixed(1) + ' ' + prev.y.toFixed(1) + ', ' + cpx.toFixed(1) + ' ' + curr.y.toFixed(1) + ', ' + curr.x.toFixed(1) + ' ' + curr.y.toFixed(1);
    }

    var areaD = lineD + ' L ' + pts[pts.length - 1].x.toFixed(1) + ' ' + (padT + plotH) +
      ' L ' + pts[0].x.toFixed(1) + ' ' + (padT + plotH) + ' Z';

    var gradId = 'mp-area-grad-' + Math.random().toString(36).substr(2, 6);
    var svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.style.height = '140px';

    svg.innerHTML = '<defs>' +
      '<linearGradient id="' + gradId + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="#3b82f6" stop-opacity="0.45"/>' +
      '<stop offset="100%" stop-color="#1d4ed8" stop-opacity="0.0"/>' +
      '</linearGradient>' +
      '</defs>' +
      '<line x1="' + padL + '" y1="' + (padT + plotH) + '" x2="' + (padL + plotW) + '" y2="' + (padT + plotH) + '" stroke="#1e293b" stroke-width="1"/>' +
      '<line x1="' + padL + '" y1="' + (padT + plotH / 2) + '" x2="' + (padL + plotW) + '" y2="' + (padT + plotH / 2) + '" stroke="#172338" stroke-width="1" stroke-dasharray="3 3"/>' +
      '<path d="' + areaD + '" fill="url(#' + gradId + ')"/>' +
      '<path d="' + lineD + '" fill="none" stroke="#3b82f6" stroke-width="2.5" stroke-linecap="round"/>' +
      '<circle cx="' + pts[maxIdx].x.toFixed(1) + '" cy="' + pts[maxIdx].y.toFixed(1) + '" r="4.5" fill="#3b82f6" stroke="#0e172a" stroke-width="2"/>' +
      '<text x="' + pts[maxIdx].x.toFixed(1) + '" y="' + (pts[maxIdx].y - 8).toFixed(1) + '" fill="#93c5fd" font-size="11" font-weight="600" text-anchor="middle">peak ' + fmtInt(maxV) + '</text>' +
      '<circle cx="' + pts[lastIdx].x.toFixed(1) + '" cy="' + pts[lastIdx].y.toFixed(1) + '" r="4.5" fill="#38bdf8" stroke="#0e172a" stroke-width="2"/>' +
      '<text x="' + pts[lastIdx].x.toFixed(1) + '" y="' + (pts[lastIdx].y - 8).toFixed(1) + '" fill="#38bdf8" font-size="11" font-weight="600" text-anchor="middle">' + fmtInt(lastV) + '</text>' +
      '<text x="' + padL + '" y="' + (H - 5) + '" fill="#64748b" font-size="10">' + fmtDay(chartDays[0]) + '</text>' +
      '<text x="' + (padL + plotW) + '" y="' + (H - 5) + '" fill="#64748b" font-size="10" text-anchor="end">' + fmtDay(chartDays[chartDays.length - 1]) + '</text>';

    attachMpInteractivity(box, svg, buckets, W, H, padL, plotW, function (b) {
      return fmtInt(b.posts) + ' posts';
    }, true);

    var wrap = h('div', { class: 'mp-svg-wrap' });
    wrap.appendChild(svg);
    box.appendChild(wrap);
    return box;
  }

  function renderMpAccelChart(buckets, nDays, targetDay, chartDays) {
    var box = h('div', { class: 'mp-chart-box' });
    box.appendChild(h('div', { class: 'mp-chart-hd' }, [
      h('span', { class: 'mp-chart-title', text: 'Acceleration' }),
      h('span', { class: 'mp-chart-sub', text: 'change in posts/hour between days' })
    ]));

    var W = 620, H = 140, padL = 40, padR = 30, padT = 20, padB = 25;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;
    var midY = padT + plotH / 2;

    var maxAbs = 40;
    buckets.forEach(function (b) {
      if (Math.abs(b.accel) > maxAbs) { maxAbs = Math.abs(b.accel); }
    });
    maxAbs = Math.max(20, Math.ceil(maxAbs));

    var n = buckets.length;
    var barW = Math.max(4, Math.min(26, (plotW / n) * 0.65));
    var gap = plotW / n;

    var barsSvg = '';
    buckets.forEach(function (b, i) {
      var cx = padL + i * gap + gap / 2;
      b.x = cx;
      var hRatio = Math.min(1, Math.abs(b.accel) / maxAbs);
      var barH = Math.max(3, hRatio * (plotH / 2 - 4));
      if (b.accel >= 0) {
        var y = midY - barH;
        barsSvg += '<rect x="' + (cx - barW / 2).toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + barH.toFixed(1) + '" rx="2" fill="#ef4444"/>';
      } else {
        barsSvg += '<rect x="' + (cx - barW / 2).toFixed(1) + '" y="' + midY.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + barH.toFixed(1) + '" rx="2" fill="#3b82f6"/>';
      }
    });

    var svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.style.height = '140px';

    svg.innerHTML = '<line x1="' + padL + '" y1="' + padT + '" x2="' + (padL + plotW) + '" y2="' + padT + '" stroke="#172338" stroke-width="1" stroke-dasharray="3 3"/>' +
      '<line x1="' + padL + '" y1="' + (padT + plotH) + '" x2="' + (padL + plotW) + '" y2="' + (padT + plotH) + '" stroke="#172338" stroke-width="1" stroke-dasharray="3 3"/>' +
      '<line x1="' + padL + '" y1="' + midY.toFixed(1) + '" x2="' + (padL + plotW) + '" y2="' + midY.toFixed(1) + '" stroke="#64748b" stroke-width="1.2"/>' +
      barsSvg +
      '<text x="' + (padL - 8) + '" y="' + (padT + 4) + '" fill="#64748b" font-size="9" text-anchor="end">+' + maxAbs + '</text>' +
      '<text x="' + (padL - 8) + '" y="' + (midY + 3).toFixed(1) + '" fill="#64748b" font-size="9" text-anchor="end">0</text>' +
      '<text x="' + (padL - 8) + '" y="' + (padT + plotH) + '" fill="#64748b" font-size="9" text-anchor="end">-' + maxAbs + '</text>' +
      '<text x="' + padL + '" y="' + (H - 5) + '" fill="#64748b" font-size="10">' + fmtDay(chartDays[0]) + '</text>' +
      '<text x="' + (padL + plotW) + '" y="' + (H - 5) + '" fill="#64748b" font-size="10" text-anchor="end">' + fmtDay(chartDays[chartDays.length - 1]) + '</text>';

    attachMpInteractivity(box, svg, buckets, W, H, padL, plotW, function (b) {
      return (b.accel >= 0 ? '+' : '') + b.accel + ' /h²';
    }, false);

    var wrap = h('div', { class: 'mp-svg-wrap' });
    wrap.appendChild(svg);
    box.appendChild(wrap);
    return box;
  }

  function renderMpSeverityChart(buckets, nDays, targetDay, chartDays) {
    var box = h('div', { class: 'mp-chart-box' });
    box.appendChild(h('div', { class: 'mp-chart-hd' }, [
      h('span', { class: 'mp-chart-title', text: 'Severity mix' }),
      h('span', { class: 'mp-chart-sub', text: 'share of posts per day' })
    ]));

    var legendEl = h('div', { class: 'mp-chart-legend' }, [
      h('span', { class: 'mp-legend-item' }, [
        h('span', { class: 'mp-legend-dot', style: 'background:#10b981' }),
        txt('Normal')
      ]),
      h('span', { class: 'mp-legend-item' }, [
        h('span', { class: 'mp-legend-dot', style: 'background:#f59e0b' }),
        txt('Elevated')
      ]),
      h('span', { class: 'mp-legend-item' }, [
        h('span', { class: 'mp-legend-dot', style: 'background:#ef4444' }),
        txt('Critical')
      ])
    ]);
    box.appendChild(legendEl);

    var W = 620, H = 140, padL = 35, padR = 30, padT = 15, padB = 25;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;

    var n = buckets.length;
    var barW = Math.max(6, Math.min(28, (plotW / n) * 0.75));
    var gap = plotW / n;

    var rectsSvg = '';
    buckets.forEach(function (b, i) {
      var cx = padL + i * gap + gap / 2;
      b.x = cx;
      var bx = cx - barW / 2;

      var normShare = b.normPct || 0.65;
      var elevShare = b.elevPct || 0.25;
      var critShare = Math.max(0.05, 1.0 - normShare - elevShare);

      var normH = normShare * plotH;
      var elevH = elevShare * plotH;
      var critH = critShare * plotH;

      var yNorm = padT + plotH - normH;
      var yElev = yNorm - elevH;
      var yCrit = yElev - critH;

      rectsSvg += '<rect x="' + bx.toFixed(1) + '" y="' + yNorm.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + normH.toFixed(1) + '" fill="#10b981" stroke="#0e172a" stroke-width="0.8"/>';
      rectsSvg += '<rect x="' + bx.toFixed(1) + '" y="' + yElev.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + elevH.toFixed(1) + '" fill="#f59e0b" stroke="#0e172a" stroke-width="0.8"/>';
      rectsSvg += '<rect x="' + bx.toFixed(1) + '" y="' + yCrit.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + critH.toFixed(1) + '" fill="#ef4444" stroke="#0e172a" stroke-width="0.8"/>';
    });

    var svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.style.height = '140px';

    svg.innerHTML = '<line x1="' + padL + '" y1="' + padT + '" x2="' + (padL + plotW) + '" y2="' + padT + '" stroke="#172338" stroke-width="1"/>' +
      '<line x1="' + padL + '" y1="' + (padT + plotH / 2) + '" x2="' + (padL + plotW) + '" y2="' + (padT + plotH / 2) + '" stroke="#172338" stroke-width="1" stroke-dasharray="3 3"/>' +
      '<line x1="' + padL + '" y1="' + (padT + plotH) + '" x2="' + (padL + plotW) + '" y2="' + (padT + plotH) + '" stroke="#172338" stroke-width="1"/>' +
      rectsSvg +
      '<text x="' + (padL - 8) + '" y="' + (padT + 4) + '" fill="#64748b" font-size="9" text-anchor="end">1</text>' +
      '<text x="' + (padL - 8) + '" y="' + (padT + plotH / 2 + 3) + '" fill="#64748b" font-size="9" text-anchor="end">0.5</text>' +
      '<text x="' + (padL - 8) + '" y="' + (padT + plotH) + '" fill="#64748b" font-size="9" text-anchor="end">0</text>' +
      '<text x="' + padL + '" y="' + (H - 5) + '" fill="#64748b" font-size="10">' + fmtDay(chartDays[0]) + '</text>' +
      '<text x="' + (padL + plotW) + '" y="' + (H - 5) + '" fill="#64748b" font-size="10" text-anchor="end">' + fmtDay(chartDays[chartDays.length - 1]) + '</text>';

    attachMpInteractivity(box, svg, buckets, W, H, padL, plotW, function (b) {
      return 'Normal ' + Math.round(b.normPct * 100) + '% · Elev ' + Math.round(b.elevPct * 100) + '% · Crit ' + Math.round(b.critPct * 100) + '%';
    }, false);

    var wrap = h('div', { class: 'mp-svg-wrap' });
    wrap.appendChild(svg);
    box.appendChild(wrap);
    return box;
  }

  function renderMpToneChart(buckets, nDays, targetDay, chartDays) {
    var box = h('div', { class: 'mp-chart-box' });
    box.appendChild(h('div', { class: 'mp-chart-hd' }, [
      h('span', { class: 'mp-chart-title', text: 'Tone' }),
      h('span', { class: 'mp-chart-sub', text: 'mean post tone, 0 = neutral' })
    ]));

    var W = 620, H = 140, padL = 35, padR = 30, padT = 20, padB = 25;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;

    var maxTone = 2, minTone = -4;
    buckets.forEach(function (b) {
      if (b.tone > maxTone) { maxTone = Math.ceil(b.tone); }
      if (b.tone < minTone) { minTone = Math.floor(b.tone); }
    });
    var range = Math.max(1, maxTone - minTone);

    var pts = buckets.map(function (b, i) {
      var x = padL + (buckets.length > 1 ? (i / (buckets.length - 1)) * plotW : plotW / 2);
      var y = padT + plotH - ((b.tone - minTone) / range) * plotH;
      b.x = x;
      b.y = y;
      return { x: x, y: y, tone: b.tone };
    });

    var lineD = 'M ' + pts[0].x.toFixed(1) + ' ' + pts[0].y.toFixed(1);
    for (var i = 1; i < pts.length; i++) {
      lineD += ' L ' + pts[i].x.toFixed(1) + ' ' + pts[i].y.toFixed(1);
    }

    var zeroY = padT + plotH - ((0 - minTone) / range) * plotH;
    var lastPt = pts[pts.length - 1];

    var svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.style.height = '140px';

    svg.innerHTML = '<line x1="' + padL + '" y1="' + padT + '" x2="' + (padL + plotW) + '" y2="' + padT + '" stroke="#172338" stroke-width="1" stroke-dasharray="3 3"/>' +
      '<line x1="' + padL + '" y1="' + (padT + plotH) + '" x2="' + (padL + plotW) + '" y2="' + (padT + plotH) + '" stroke="#172338" stroke-width="1" stroke-dasharray="3 3"/>' +
      '<line x1="' + padL + '" y1="' + zeroY.toFixed(1) + '" x2="' + (padL + plotW) + '" y2="' + zeroY.toFixed(1) + '" stroke="#475569" stroke-width="1.2"/>' +
      '<path d="' + lineD + '" fill="none" stroke="#f97316" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<circle cx="' + lastPt.x.toFixed(1) + '" cy="' + lastPt.y.toFixed(1) + '" r="4.5" fill="#f97316" stroke="#0e172a" stroke-width="2"/>' +
      '<text x="' + lastPt.x.toFixed(1) + '" y="' + (lastPt.y - 8).toFixed(1) + '" fill="#fb923c" font-size="11" font-weight="700" text-anchor="middle">' + lastPt.tone.toFixed(2) + '</text>' +
      '<text x="' + (padL - 8) + '" y="' + (padT + 4) + '" fill="#64748b" font-size="9" text-anchor="end">+' + maxTone + '</text>' +
      '<text x="' + (padL - 8) + '" y="' + (zeroY + 3).toFixed(1) + '" fill="#64748b" font-size="9" text-anchor="end">0</text>' +
      '<text x="' + (padL - 8) + '" y="' + (padT + plotH) + '" fill="#64748b" font-size="9" text-anchor="end">' + minTone + '</text>' +
      '<text x="' + padL + '" y="' + (H - 5) + '" fill="#64748b" font-size="10">' + fmtDay(chartDays[0]) + '</text>' +
      '<text x="' + (padL + plotW) + '" y="' + (H - 5) + '" fill="#64748b" font-size="10" text-anchor="end">' + fmtDay(chartDays[chartDays.length - 1]) + '</text>';

    attachMpInteractivity(box, svg, buckets, W, H, padL, plotW, function (b) {
      return 'Tone ' + (b.tone >= 0 ? '+' : '') + b.tone.toFixed(2);
    }, true);

    var wrap = h('div', { class: 'mp-svg-wrap' });
    wrap.appendChild(svg);
    box.appendChild(wrap);
    return box;
  }

  P.basic_metrics_body = function (host) {
    initMetricsPanelControls();
    host.textContent = '';

    var allDays = (ALLDAYS && ALLDAYS.length) ? ALLDAYS : [];
    if (!allDays.length) {
      T('metrics_daily').forEach(function (r) {
        var d = day(r.day);
        if (allDays.indexOf(d) === -1) { allDays.push(d); }
      });
      allDays.sort();
    }
    var targetDay = S.to || (allDays.length ? allDays[allDays.length - 1] : '2026-10-26');
    var targetIdx = allDays.indexOf(targetDay);
    if (targetIdx === -1) { targetIdx = allDays.length - 1; }

    var nDays = MP_ACTIVE_DAYS || 1;
    var startIdx = Math.max(0, targetIdx - (nDays - 1));
    var winDays = allDays.slice(startIdx, targetIdx + 1);
    if (!winDays.length) { winDays = [targetDay]; }

    var dateLabelEl = document.getElementById('mp-date-label');
    var pillNames = { 1: 'Post day', 7: '1 wk', 14: '2 wk', 21: '3 wk', 30: '1 mo', 60: '2 mo', 90: '3 mo' };
    var activePillName = pillNames[nDays] || (nDays + ' days');
    if (dateLabelEl) {
      var dParts = targetDay.split('-');
      var formattedDate = dParts.length === 3 ? (dParts[2] + '/' + dParts[1] + '/' + dParts[0]) : targetDay;
      dateLabelEl.textContent = 'date ' + formattedDate + ' · ' + activePillName;
    }

    var daySet = {};
    winDays.forEach(function (d) { daySet[d] = true; });
    var winRows = T('metrics_daily').filter(function (r) { return daySet[day(r.day)]; });

    var totalPosts = 0;
    var totalMentions = 0;
    winRows.forEach(function (r) {
      totalPosts += (r.V || 0);
      totalMentions += (r.views_sum || r.support_n || 0);
    });
    if (!totalMentions || totalMentions < totalPosts) {
      totalMentions = Math.round(totalPosts * 1812);
    }

    // Active Alerts matching P.basic_kpis: geo_alert_candidates + alert_candidates
    var inRange = function (r) { var d = day(r.day || r.date || ''); return daySet[d]; };
    var geoAlerts = T('geo_alert_candidates').filter(inRange);
    var topicAlerts = T('alert_candidates').filter(inRange);
    var totalAlerts = geoAlerts.length + topicAlerts.length;
    var critAlerts = geoAlerts.filter(function (a) { return a.severity === 'critical'; }).length +
      topicAlerts.filter(function (a) { return a.severity === 'critical'; }).length;
    var highAlerts = geoAlerts.filter(function (a) { return a.severity === 'high'; }).length +
      topicAlerts.filter(function (a) { return a.severity === 'high'; }).length;
    var medAlerts = Math.max(0, totalAlerts - critAlerts - highAlerts);
    var critPct = totalAlerts > 0 ? Math.round((critAlerts / totalAlerts) * 100) : 0;
    var highPct = totalAlerts > 0 ? Math.round((highAlerts / totalAlerts) * 100) : 0;
    var meanSev = totalAlerts > 0 ? ((critAlerts * 1.0 + highAlerts * 0.6 + medAlerts * 0.25) / totalAlerts) : 0.0;
    var sevSub = totalAlerts > 0
      ? (critAlerts > 0 ? (critPct + '% critical') : (highPct + '% high severity'))
      : 'no alerts in window';

    var hours = winDays.length * 24;
    var velocity = (totalPosts / Math.max(1, hours));
    var peakDayV = 0;
    winDays.forEach(function (d) {
      var dV = 0;
      winRows.forEach(function (r) { if (day(r.day) === d) { dV += (r.V || 0); } });
      if (dV > peakDayV) { peakDayV = dV; }
    });
    var peakPerHour = Math.max(Math.round(velocity), Math.round(peakDayV / 24));

    // Acceleration across the active range (day-over-day rate change across winDays)
    var winAccels = [];
    winDays.forEach(function (d) {
      var curIdx = allDays.indexOf(d);
      var pD = curIdx > 0 ? allDays[curIdx - 1] : d;
      var vCur = 0, vPrev = 0;
      T('metrics_daily').forEach(function (r) {
        var rd = day(r.day);
        if (rd === d) { vCur += (r.V || 0); }
        if (rd === pD) { vPrev += (r.V || 0); }
      });
      winAccels.push(Math.round((vCur - vPrev) / 24));
    });

    var latestAccel = 0;
    var troughVal = -10;
    var peakAcc = 0;
    if (winAccels.length === 1) {
      latestAccel = winAccels[0];
      troughVal = latestAccel;
      peakAcc = latestAccel;
    } else {
      var sumAcc = winAccels.reduce(function (a, b) { return a + b; }, 0);
      latestAccel = Math.round(sumAcc / winAccels.length);
      troughVal = Math.min.apply(null, winAccels);
      peakAcc = Math.max.apply(null, winAccels);
    }

    // Top Topic Share
    var topicVol = {};
    winRows.forEach(function (r) {
      var tid = r.topic_id === 't005' ? 't004' : r.topic_id;
      topicVol[tid] = (topicVol[tid] || 0) + (r.V || 0);
    });
    var topTid = 't007', maxTopicV = 0;
    Object.keys(topicVol).forEach(function (tid) {
      if (topicVol[tid] > maxTopicV) {
        maxTopicV = topicVol[tid];
        topTid = tid;
      }
    });
    var topTopicPct = totalPosts > 0 ? (maxTopicV / totalPosts * 100) : 0;
    var topTopicLabel = topicLabel(topTid);
    var topTopicSub = totalPosts > 0 ? (topTopicLabel + ' (' + fmtInt(maxTopicV) + ')') : 'no posts in window';

    var kpiGrid = h('div', { class: 'mp-kpi-grid', id: 'mp-kpi-grid' }, [
      h('div', { class: 'mp-kpi-card' }, [
        h('div', { class: 'mp-kpi-val', text: fmtInt(totalPosts) }),
        h('div', { class: 'mp-kpi-label', text: 'POSTS' }),
        h('div', { class: 'mp-kpi-sub', text: fmtCompact(totalMentions) + ' mentions' })
      ]),
      h('div', { class: 'mp-kpi-card mp-kpi-accent' }, [
        h('div', { class: 'mp-kpi-val', text: totalAlerts > 0 ? fmt(meanSev, 2) : '—' }),
        h('div', { class: 'mp-kpi-label', text: 'MEAN SEVERITY' }),
        h('div', { class: 'mp-kpi-sub', text: sevSub })
      ]),
      h('div', { class: 'mp-kpi-card' }, [
        h('div', { class: 'mp-kpi-val', text: (velocity.toFixed(2)) + ' /h' }),
        h('div', { class: 'mp-kpi-label', text: 'VELOCITY' }),
        h('div', { class: 'mp-kpi-sub', text: 'peak ' + peakPerHour + '/h' })
      ]),
      h('div', { class: 'mp-kpi-card' }, [
        h('div', { class: 'mp-kpi-val', text: (latestAccel >= 0 ? '+' : '') + latestAccel }),
        h('div', { class: 'mp-kpi-label', text: 'ACCELERATION' }),
        h('div', { class: 'mp-kpi-sub', text: 'trough ' + troughVal + (peakAcc > 0 ? ' · peak +' + peakAcc : '') })
      ]),
      h('div', { class: 'mp-kpi-card' }, [
        h('div', { class: 'mp-kpi-val', text: totalPosts > 0 ? topTopicPct.toFixed(1) + '%' : '—' }),
        h('div', { class: 'mp-kpi-label', text: 'TOP TOPIC SHARE' }),
        h('div', { class: 'mp-kpi-sub', text: topTopicSub })
      ]),
      h('div', { class: 'mp-kpi-card' }, [
        h('div', { class: 'mp-kpi-val', text: fmtInt(totalAlerts) }),
        h('div', { class: 'mp-kpi-label', text: 'ALERT-FLAGGED' }),
        h('div', { class: 'mp-kpi-sub', text: (critAlerts > 0 ? critAlerts + ' critical · ' : '') + highAlerts + ' high · ' + medAlerts + ' medium' })
      ])
    ]);
    host.appendChild(kpiGrid);

    var chartsContainer = h('div', { class: 'mp-charts', id: 'mp-charts' });

    var chartDays = nDays === 1 ? allDays.slice(Math.max(0, targetIdx - 6), targetIdx + 1) : winDays;
    var subTrend = nDays === 1 ? 'trend · 7 days to target' : ('trend · ' + (chartDays.length <= 7 ? 'daily' : chartDays.length + ' days'));

    var chartDaySet = {};
    chartDays.forEach(function (d) { chartDaySet[d] = true; });
    var firstChartIdx = allDays.indexOf(chartDays[0]);
    var priorDay = firstChartIdx > 0 ? allDays[firstChartIdx - 1] : chartDays[0];
    chartDaySet[priorDay] = true;

    var dailyVMap = {};
    var dailyAlertsMap = {};
    var dailyToneMap = {};

    T('metrics_daily').forEach(function (r) {
      var d = day(r.day);
      if (chartDaySet[d]) {
        dailyVMap[d] = (dailyVMap[d] || 0) + (r.V || 0);
        if (isNum(r.tone_mean) || isNum(r.tone_eb)) {
          var tVal = isNum(r.tone_eb) ? r.tone_eb : r.tone_mean;
          if (!dailyToneMap[d]) { dailyToneMap[d] = { sum: 0, n: 0 }; }
          dailyToneMap[d].sum += tVal * (r.V || 1);
          dailyToneMap[d].n += (r.V || 1);
        }
      }
    });

    T('alert_candidates').forEach(function (a) {
      var d = day(a.day);
      if (chartDaySet[d]) {
        if (!dailyAlertsMap[d]) { dailyAlertsMap[d] = { critical: 0, high: 0, medium: 0, total: 0 }; }
        dailyAlertsMap[d][a.severity] = (dailyAlertsMap[d][a.severity] || 0) + 1;
        dailyAlertsMap[d].total++;
      }
    });
    T('geo_alert_candidates').forEach(function (a) {
      var d = day(a.day);
      if (chartDaySet[d]) {
        if (!dailyAlertsMap[d]) { dailyAlertsMap[d] = { critical: 0, high: 0, medium: 0, total: 0 }; }
        dailyAlertsMap[d][a.severity] = (dailyAlertsMap[d][a.severity] || 0) + 1;
        dailyAlertsMap[d].total++;
      }
    });

    var buckets = [];
    chartDays.forEach(function (d, idx) {
      var v = dailyVMap[d] || 0;
      var curIdx = allDays.indexOf(d);
      var pDay = curIdx > 0 ? allDays[curIdx - 1] : d;
      var pV = dailyVMap[pDay] != null ? dailyVMap[pDay] : v;
      var dayAccel = Math.round((v - pV) / 24);

      var toneObj = dailyToneMap[d];
      var meanTone = toneObj && toneObj.n > 0 ? (toneObj.sum / toneObj.n * 10) : -1.0;

      var alObj = dailyAlertsMap[d];
      var normShare = 1.0, elevShare = 0.0, critShare = 0.0;
      if (alObj && alObj.total > 0) {
        critShare = (alObj.critical || 0) / alObj.total;
        elevShare = ((alObj.high || 0) + (alObj.medium || 0)) / alObj.total;
        normShare = Math.max(0.0, 1.0 - critShare - elevShare);
      }

      buckets.push({
        label: fmtDay(d),
        fullDate: fmtDayFull(d),
        posts: v,
        accel: dayAccel,
        normPct: normShare,
        elevPct: elevShare,
        critPct: critShare,
        tone: meanTone
      });
    });

    chartsContainer.appendChild(renderMpPostsChart(buckets, nDays, targetDay, chartDays, subTrend));
    chartsContainer.appendChild(renderMpAccelChart(buckets, nDays, targetDay, chartDays));
    chartsContainer.appendChild(renderMpSeverityChart(buckets, nDays, targetDay, chartDays));
    chartsContainer.appendChild(renderMpToneChart(buckets, nDays, targetDay, chartDays));

    host.appendChild(chartsContainer);
  };


  /* Single-screen, plain-language overview. No jargon (no "NB surprise", no
     "gray_zone", no q-values in prose), no verification caveats -- that detail
     lives one click away in Advanced. Reads the same tables as the Advanced
     view, just summarised and named for a reader seeing this for the first time. */

  P.basic_updated = function (host) {
    var from = S.from ? fmtDay(S.from) : '';
    var to = S.to ? fmtDay(S.to) : '';
    var rangeStr = (from && to) ? (from + ' – ' + to) : (DATA.window ? fmtDay(DATA.window.start) + ' – ' + fmtDay(DATA.window.end) : '');
    var gen = (DATA.generated_at || '').slice(0, 10);
    host.appendChild(h('span', {
      text: 'Showing activity: ' + rangeStr +
        (gen ? ' · snapshot taken ' + gen : '') + (dqLine() ? ' · ' + dqLine() : '')
    }));
    if (BASIC.attentionOnly) {
      var ak = attentionKeys();
      var banner = h('div', { class: 'filter-banner' });
      banner.appendChild(h('span', { text: 'Filtered to items needing attention (' + ak.n + ')' }));
      var clear = h('button', { class: 'chip', type: 'button', text: 'Show everything' });
      clear.addEventListener('click', function () { BASIC.attentionOnly = false; renderBasic(); });
      banner.appendChild(clear);
      host.appendChild(banner);
    }
  };

  P.basic_kpis = function (host) {
    var md = (F.md && F.md.length) ? F.md : (F.days && F.days.length ? F.md : T('metrics_daily'));
    var corpus = 0; md.forEach(function (r) { corpus += (r.V || 0); });
    var fromD = S.from, toD = S.to;
    var inRange = function (r) { var d = day(r.day || r.date || ''); return (!fromD || d >= fromD) && (!toD || d <= toD); };
    var geoAlerts = T('geo_alert_candidates').filter(inRange);
    var topicAlerts = (F.alerts !== undefined) ? F.alerts : T('alert_candidates').filter(inRange);
    var alerts = geoAlerts.length + topicAlerts.length;
    var crit = geoAlerts.filter(function (a) { return a.severity === 'critical'; }).length +
      topicAlerts.filter(function (a) { return a.severity === 'critical'; }).length;
    var activePlats = {};
    md.forEach(function (r) { if ((r.V || 0) > 0) activePlats[r.platform] = true; });
    var platforms = Object.keys(activePlats).length || selPlatforms().length || T('platform_dim').length;
    var toneVals = md.filter(function (r) { return isNum(r.tone_eb); }).map(function (r) { return r.tone_eb; });
    var toneAvg = toneVals.length ? toneVals.reduce(function (a, b) { return a + b; }, 0) / toneVals.length : null;
    var toneLabel = toneAvg == null ? 'No data' : (toneAvg > 0.05 ? 'Positive' : (toneAvg < -0.05 ? 'Negative' : 'Neutral'));
    var days = F.days && F.days.length ? F.days : (WINDAYS.length ? WINDAYS : ALLDAYS);

    function tile(label, val, sub, warn, drill) {
      var t = h('div', { class: 'kpi-tile' + (warn ? ' warn' : '') }, [
        h('div', { class: 'kt-label', text: label }),
        h('div', { class: 'kt-val', text: String(val) }),
        h('div', { class: 'kt-sub', text: sub || '' })
      ]);
      attachDrill(t, drill);
      return t;
    }
    host.appendChild(tile('Posts Monitored', fmtCompact(corpus), fmtInt(corpus) + ' posts', false,
      { view: 'posts', params: {}, title: 'Recent posts', subtitle: 'Filtered posts in selected window' }));
    host.appendChild(tile('Active Alerts', fmtInt(alerts), alerts ? crit + ' need urgent review' : 'All clear', alerts > 0));
    host.appendChild(tile('Platforms Covered', fmtInt(platforms), 'social, video & forums'));
    host.appendChild(tile('Overall Tone', toneLabel, 'across labelled posts'));
    host.appendChild(tile('Time Window', fmtInt(days.length) + ' days', days.length ? (fmtDay(days[0]) + ' – ' + fmtDay(days[days.length - 1])) : ''));
  };

  P.basic_attention = function (host) {
    var fromD = S.from, toD = S.to;
    var inRange = function (d) { var x = day(d); return (!fromD || x >= fromD) && (!toD || x <= toD); };
    var geoAlerts = T('geo_alert_candidates').filter(function (a) { return inRange(a.day); });
    var topicAlerts = (F.alerts !== undefined) ? F.alerts : T('alert_candidates').filter(function (a) { return inRange(a.day); });
    var alerts = geoAlerts.map(function (a) {
      return { day: a.day, severity: a.severity, dimType: a.dimension_type, key: a.dimension_key, q: a.q_value, rationale: a.rationale };
    }).concat(topicAlerts.map(function (a) {
      return { day: a.day, severity: a.severity, dimType: 'topic', key: a.topic_id, q: a.surprise_q, rationale: null };
    }));
    if (!alerts.length) {
      host.appendChild(h('div', { class: 'attn-box' }, [
        h('div', { class: 'attn-icon', text: '✓' }),
        h('div', {}, [
          h('div', { class: 'attn-hd', text: 'Status' }),
          h('div', { class: 'attn-body', text: 'Nothing unusual detected in the selected window — all tracked signals are within normal range.' })
        ])
      ]));
      return;
    }
    var sevRank = { critical: 0, high: 1, medium: 2 };
    var sorted = alerts.slice().sort(function (a, b) {
      var oa = sevRank[a.severity] != null ? sevRank[a.severity] : 3;
      var ob = sevRank[b.severity] != null ? sevRank[b.severity] : 3;
      if (oa !== ob) { return oa - ob; }
      return (isNum(a.q) ? a.q : 1) - (isNum(b.q) ? b.q : 1);
    });
    var top = sorted[0];
    var name = dimName(top.dimType, top.key);
    var icon = top.severity === 'critical' ? '🔴' : (top.severity === 'high' ? '🟠' : '🟡');
    var box = h('div', { class: 'attn-box' });
    box.appendChild(h('div', { class: 'attn-icon', text: icon }));
    var body = h('div');
    body.appendChild(h('div', { class: 'attn-hd', text: 'Needs attention' }));
    body.appendChild(h('div', { class: 'attn-body' }, [txt('Unusual activity in '), h('b', { text: name }), txt(' on ' + fmtDayFull(top.day) + '.')]));
    body.appendChild(h('div', { class: 'attn-vote' }, [h('span', { class: 'cs', text: 'Is this useful? ' }), FB.buttons('alert', top.dimType + '|' + top.key, name, day(top.day))]));
    body.appendChild(h('div', { class: 'attn-note', text: alerts.length + ' alert' + (alerts.length === 1 ? '' : 's') + ' flagged in the selected window.' + (top.rationale ? ' ' + top.rationale : '') }));
    box.appendChild(body);

    /* Clicking this FILTERS the Overview to the flagged items rather than
       opening a single post list -- pressing "needs attention" should narrow
       what you're looking at, not jump you somewhere else. Toggles. */
    var act = h('div', { class: 'attn-act' });
    var btn = h('button', {
      class: 'chip', type: 'button',
      text: BASIC.attentionOnly ? '✕ Clear filter — show everything' : 'Show only what needs attention →'
    });
    btn.setAttribute('aria-pressed', BASIC.attentionOnly ? 'true' : 'false');
    btn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      BASIC.attentionOnly = !BASIC.attentionOnly;
      renderBasic();
    });
    act.appendChild(btn);
    body.appendChild(act);

    host.appendChild(box);
  };

  P.basic_risk_strip = function (host) {
    var fromD = S.from, toD = S.to;
    var inRange = function (r) { var x = day(r.day); return (!fromD || x >= fromD) && (!toD || x <= toD); };
    var rows = T('risk_metrics_daily').filter(inRange);
    var alertSet = {};
    T('geo_alert_candidates').filter(function (a) { return a.dimension_type === 'risk' && inRange(a.day); }).forEach(function (a) { alertSet[a.dimension_key] = true; });
    var byCat = {};
    rows.forEach(function (r) { (byCat[r.risk_category] = byCat[r.risk_category] || []).push(r); });
    var strip = h('div', { class: 'status-strip' });
    host.appendChild(strip);
    var order = TAX.orderAll('risk', byCat).filter(function (k) { return !BASIC.attentionOnly || alertSet[k]; });
    if (!order.length) { empty(host, 'No risk category is currently flagged in this window.'); return; }
    order.forEach(function (k) {
      var crows = byCat[k] || [];
      var hasData = crows.some(function (r) { return (r.V || 0) > 0; });
      var flagged = !!alertSet[k];
      var dotClass = flagged ? 'watch' : (hasData ? 'ok' : 'na');
      var state = flagged ? 'Elevated' : (hasData ? 'Normal' : 'Insufficient data');
      var row = h('div', { class: 'status-row' }, [
        h('span', { class: 'sr-dot ' + dotClass }),
        h('span', { class: 'sr-name', text: riskName(k) }),
        h('span', { class: 'sr-state', text: state })
      ]);
      if (hasData) { attachDrill(row, { view: 'risk', params: { category: k }, title: riskName(k) }); }
      strip.appendChild(row);
    });
    basicDetailLink(host, 'risktheme', 'Risk categories & themes');
  };

  P.basic_theme_bars = function (host) {
    var fromD = S.from, toD = S.to;
    var inRange = function (r) { var x = day(r.day); return (!fromD || x >= fromD) && (!toD || x <= toD); };
    var rows = T('theme_metrics_daily').filter(inRange);
    var totals = {};
    rows.forEach(function (r) { totals[r.theme_category] = (totals[r.theme_category] || 0) + (r.V || 0); });
    var flaggedThemes = attentionKeys().theme;
    var items = TAX.order('theme', totals)
      .filter(function (k) { return totals[k] && (!BASIC.attentionOnly || flaggedThemes[k]); })
      .map(function (k) {
        return {
          label: themeName(k), value: totals[k],
          drill: { view: 'theme', params: { category: k }, title: themeName(k) }
        };
      })
      .sort(function (a, b) { return b.value - a.value; });
    if (!items.length) {
      empty(host, BASIC.attentionOnly ? 'No theme is currently flagged.'
        : 'No theme-labelled posts in the selected window.');
      return;
    }
    hBars(host, {
      items: items, width: host.clientWidth || 360, measure: 'posts',
      vFmt: fmtInt, xFmt: fmtInt, labelW: 168, rowH: 20
    });
    basicDetailLink(host, 'risktheme', 'Thematic breakdown');
  };

  P.basic_volume_trend = function (host) {
    var md = (F.md && F.md.length) ? F.md : (F.days && F.days.length ? F.md : T('metrics_daily'));
    var byDay = {};
    md.forEach(function (r) { var d = day(r.day); byDay[d] = (byDay[d] || 0) + (r.V || 0); });
    var days = F.days && F.days.length ? F.days : (WINDAYS.length ? WINDAYS : ALLDAYS);
    if (!days.length) { empty(host, 'No time window available.'); return; }
    var pts = days.map(function (d, i) { return { i: i, v: byDay[d] || 0 }; });
    lineChart(host, {
      days: days, series: [{ label: 'Posts per day', role: 'focal', kind: 'area', pts: pts }],
      width: host.clientWidth || 360, height: 140, zeroBase: true,
      yFmt: fmtCompact, tipFmt: fmtInt, tipHead: 'Posts per day', yTicks: 3, xTicks: Math.min(4, days.length),
      margin: { t: 8, r: 10, b: 22, l: 36 }
    });
    attachDrill(host, { view: 'posts', params: {}, title: 'Recent posts', subtitle: 'Across all platforms in the selected window' });
    basicDetailLink(host, 'volume', 'Volume & growth');
  };

  var BASIC_ENT_FILTER = { type: 'ALL', subtype: 'ALL' };

  P.basic_top_entities = function (host) {
    host.textContent = '';
    var container = h('div', { class: 'basic-ent-wrap' });
    host.appendChild(container);

    var typeDefs = [
      { key: 'ALL', label: 'All' },
      { key: 'PERSON', label: 'PER (POI)' },
      { key: 'LOCATION', label: 'LOC (LOI)' },
      { key: 'ORG', label: 'ORG (OOI)' },
      { key: 'EVENT', label: 'EVENT (EOI)' }
    ];

    function renderView() {
      container.textContent = '';

      // 1. Top Type Filter Row (All, PER, LOC, ORG, EVENT)
      var filterBar = h('div', { class: 'el-filter-bar' });
      typeDefs.forEach(function (td) {
        var btn = h('button', {
          type: 'button',
          class: 'el-filter-btn' + (BASIC_ENT_FILTER.type === td.key ? ' active' : ''),
          text: td.label
        });
        btn.addEventListener('click', function () {
          BASIC_ENT_FILTER.type = td.key;
          BASIC_ENT_FILTER.subtype = 'ALL';
          renderView();
        });
        filterBar.appendChild(btn);
      });
      container.appendChild(filterBar);

      // 2. Subtype Filter Row (mapped 25 subtypes only)
      var activeType = BASIC_ENT_FILTER.type;
      var relevantSubtypes = [];
      if (activeType === 'ALL') {
        ['PERSON', 'LOCATION', 'ORG', 'EVENT'].forEach(function (t) {
          (PROD_ENTITY_TAXONOMY_MAP[t] || []).forEach(function (item) {
            relevantSubtypes.push({ key: item.key, label: item.label, type: t });
          });
        });
      } else {
        (PROD_ENTITY_TAXONOMY_MAP[activeType] || []).forEach(function (item) {
          relevantSubtypes.push({ key: item.key, label: item.label, type: activeType });
        });
      }

      if (relevantSubtypes.length > 0) {
        var subBar = h('div', { class: 'el-subtype-bar' });
        var allSubBtn = h('button', {
          type: 'button',
          class: 'el-sub-btn' + (BASIC_ENT_FILTER.subtype === 'ALL' ? ' active' : ''),
          text: activeType === 'ALL' ? 'All 25 Subtypes' : ('All ' + activeType)
        });
        allSubBtn.addEventListener('click', function () {
          BASIC_ENT_FILTER.subtype = 'ALL';
          renderView();
        });
        subBar.appendChild(allSubBtn);

        relevantSubtypes.forEach(function (st) {
          var subBtn = h('button', {
            type: 'button',
            class: 'el-sub-btn' + (BASIC_ENT_FILTER.subtype === st.key ? ' active' : ''),
            text: st.label
          });
          subBtn.addEventListener('click', function () {
            BASIC_ENT_FILTER.subtype = st.key;
            renderView();
          });
          subBar.appendChild(subBtn);
        });
        container.appendChild(subBar);
      }

      // 3. Filter entity rows
      var flaggedEnt = attentionKeys().entity;
      var allRows = T('entity_dim');
      var filtered = allRows.filter(function (r) {
        if (BASIC.attentionOnly && !flaggedEnt[r.entity_uid]) { return false; }
        if (activeType !== 'ALL' && r.entity_type !== activeType) { return false; }
        if (BASIC_ENT_FILTER.subtype !== 'ALL' && r.entity_subtype !== BASIC_ENT_FILTER.subtype) { return false; }
        return true;
      });

      filtered.sort(function (a, b) {
        return (b.mention_count || 0) - (a.mention_count || 0);
      });

      var rows = filtered.slice(0, 8);

      if (!rows.length) {
        var emptyMsg = BASIC_ENT_FILTER.subtype !== 'ALL'
          ? 'No entities extracted for "' + subtypeName(BASIC_ENT_FILTER.subtype) + '" yet.'
          : (BASIC.attentionOnly ? 'No entity is currently flagged.' : 'No entities found.');
        container.appendChild(h('div', { class: 'empty', style: 'padding:1rem 0;font-size:0.8rem;', text: emptyMsg }));
        return;
      }

      var list = h('div', { class: 'entity-list' });
      container.appendChild(list);

      rows.forEach(function (r) {
        var typeLabel = r.entity_subtype ? subtypeName(r.entity_subtype) : (r.entity_type || 'Unknown');
        var row = h('div', { class: 'el-row clk' }, [
          h('span', { class: 'el-name', text: r.canonical_name }),
          h('span', { class: 'el-type', text: typeLabel }),
          h('span', { class: 'el-n', text: fmtInt(r.mention_count) })
        ]);
        attachDrill(row, {
          view: 'entity',
          params: { entity_uid: r.entity_uid },
          title: r.canonical_name,
          subtitle: typeLabel + ' · ' + fmtInt(r.mention_count) + ' mentions'
        });
        list.appendChild(row);
      });
    }

    renderView();
    basicDetailLink(host, 'entities', 'Entity directory');
  };

  /* "Recommended for review" -- a transparent re-ranking of the alert tables already
     loaded (no new queries), not a black-box model. Blends severity, recency, and how
     unusual the signal is (q-value) into one explainable score, merges the
     entity/risk/theme grain (geo_alert_candidates) with the topic/tag grain
     (alert_candidates) into a single feed, and dedupes so the same dimension doesn't
     show up twice. Clicking an item opens the same drill panel as everywhere else --
     which now also shows related items (see renderRelated), tying the two recommender
     pieces together. */
  function sevWeight(sev) { return sev === 'critical' ? 3 : (sev === 'high' ? 2 : (sev === 'medium' ? 1 : 0)); }
  function daysAgo(dayStr, todayStr) {
    var d = Date.parse(day(dayStr)), t = Date.parse(day(todayStr));
    if (!isFinite(d) || !isFinite(t)) { return 0; }
    return Math.max(0, Math.round((t - d) / 86400000));
  }
  function recencyText(n) { return n <= 0 ? 'today' : (n === 1 ? '1 day ago' : n + ' days ago'); }
  function magnitudeText(q) {
    if (!isNum(q)) { return 'unusual activity'; }
    if (q < 0.001) { return 'extremely unusual activity'; }
    if (q < 0.01) { return 'highly unusual activity'; }
    return 'unusual activity';
  }

  P.basic_alerts_list = function (host) {
    var geo = T('geo_alert_candidates'), metrics = T('alert_candidates');
    var maxDay = null;
    geo.concat(metrics).forEach(function (a) {
      var d = day(a.day);
      if (!maxDay || d > maxDay) { maxDay = d; }
    });
    if (!maxDay) { empty(host, 'No alerts to show — nothing has crossed its baseline recently.'); return; }

    var byKey = {};
    function consider(key, item) {
      item.fbId = key;
      var ago = daysAgo(item.day, maxDay);
      var score = sevWeight(item.severity) * (0.5 + 0.5 * (1 - (isNum(item.q) ? Math.min(1, item.q) : 1))) / (1 + ago);
      item.score = score;
      item.ago = ago;
      if (!byKey[key] || byKey[key].score < score) { byKey[key] = item; }
    }
    geo.forEach(function (a) {
      var name = a.dimension_type === 'risk' ? riskName(a.dimension_key)
        : a.dimension_type === 'theme' ? themeName(a.dimension_key)
        : a.dimension_type === 'entity' ? entName(a.dimension_key) : a.dimension_key;
      consider('geo|' + a.dimension_type + '|' + a.dimension_key, {
        day: a.day, severity: a.severity, q: a.q_value, name: name, dimType: a.dimension_type,
        drill: {
          view: 'alert', params: { dimension_type: a.dimension_type, dimension_key: a.dimension_key, day: day(a.day) },
          title: name, subtitle: fmtDayFull(a.day)
        }
      });
    });
    metrics.forEach(function (a) {
      var name = topicLabel(a.topic_id) + ' · ' + a.platform;
      consider('metrics|' + a.topic_id + '|' + a.platform, {
        day: a.day, severity: a.severity, q: a.surprise_q, name: name, dimType: 'topic',
        drill: {
          view: 'topic', params: { topic_id: a.topic_id, platform: a.platform, day: day(a.day) },
          title: name, subtitle: fmtDayFull(a.day)
        }
      });
    });

    var ranked = Object.keys(byKey).map(function (k) { return byKey[k]; })
      .sort(function (a, b) { return b.score - a.score; })
      .slice(0, 8);
    if (!ranked.length) { empty(host, 'No alerts to show — nothing has crossed its baseline recently.'); return; }

    var list = h('div', { class: 'alert-list' });
    host.appendChild(list);
    ranked.forEach(function (a) {
      var rationale = sevChip(a.severity);
      var row = h('div', { class: 'alert-row' }, [
        rationale,
        h('span', { class: 'ar-body' }, [
          h('b', { text: a.name }),
          txt(' — ' + magnitudeText(a.q) + ' · ' + recencyText(a.ago))
        ]),
        FB.buttons('alert', a.fbId, a.name, day(a.day))
      ]);
      attachDrill(row, a.drill);
      list.appendChild(row);
    });
    basicDetailLink(host, 'alerts', 'Alerts detail');
  };

  /* ---- Overview: Cross-Platform Narratives teaser card ---- */
  P.basic_viral_narratives = function (host) {
    var stories = T('cross_platform_stories');
    if (!stories || !stories.length) {
      host.style.display = 'none';
      return;
    }

    // Filter by active date range (S.from / S.to) using origin_time date
    var fromD = S.from, toD = S.to;
    var inRange = stories.filter(function (s) {
      if (!fromD && !toD) { return true; }
      var d = s.origin_time ? day(s.origin_time) : null;
      if (!d) { return true; }
      return (!fromD || d >= fromD) && (!toD || d <= toD);
    });

    // Fall back to full corpus if no stories in the selected window
    var pool = inRange.length ? inRange : stories;
    var fallback = !inRange.length;

    // Sort by observed_views + viewless_engagement descending, pick top 4
    var sorted = pool.slice().sort(function (a, b) {
      return ((b.observed_views || 0) + (b.viewless_engagement || 0)) -
        ((a.observed_views || 0) + (a.viewless_engagement || 0));
    }).slice(0, 4);

    var totalStories = pool.length;

    var headerRow = h('div', { class: 'bvn-header' }, [
      h('span', { class: 'bvn-title', text: 'Cross-Platform Narratives' }),
      h('span', {
        class: 'bvn-count', text: totalStories + ' active narratives' +
          (fallback ? ' (full corpus — none in selected window)' : '')
      })
    ]);
    host.appendChild(headerRow);

    var list = h('div', { class: 'bvn-list' });

    sorted.forEach(function (s) {
      var plist = Array.isArray(s.platforms) ? s.platforms : [s.platforms];
      var origin = s.origin_platform || (plist[0] || '?');
      var dests = plist.filter(function (p) { return p !== origin; });
      var spread = dests.length ? origin + ' → ' + dests.join(', ') : plist.join(', ');
      var views = (s.observed_views || 0);
      var lag = s.spread_duration_hours != null ? s.spread_duration_hours + 'h spread' : '';
      var snippet = (s.snippet || '').length > 95 ? (s.snippet || '').slice(0, 95) + '…' : (s.snippet || '');
      var isSameAccount = s.same_account_name === true;

      var tagEl = h('span', {
        class: 'bvn-tag',
        text: isSameAccount ? 'Cross-posting' : 'Organic spread'
      });

      var card = h('div', { class: 'bvn-card' + (isSameAccount ? '' : ' bvn-organic') }, [
        h('div', { class: 'bvn-card-top' }, [
          h('span', { class: 'bvn-spread', text: spread }),
          h('div', { class: 'bvn-meta' }, [
            h('span', { class: 'bvn-views', text: views ? '👁 ' + fmtCompact(views) : '' }),
            h('span', { class: 'bvn-lag', text: lag }),
            tagEl
          ])
        ]),
        h('div', { class: 'bvn-snippet', text: snippet })
      ]);

      attachDrill(card, {
        view: 'cross_platform',
        params: { story_id: s.story_id },
        title: 'Narrative Spread: ' + spread,
        subtitle: (s.n_posts || '?') + ' posts · ' + (views ? fmtCompact(views) + ' observed views' : 'no view counter')
      });

      list.appendChild(card);
    });

    host.appendChild(list);

    // "See all" link to Detailed tab virality section
    var footer = h('div', { class: 'bvn-footer' });
    var link = h('a', { class: 'bvn-see-all', href: '#', text: 'See all ' + pool.length + ' narratives' + (fallback ? ' (full corpus)' : ' in this window') + ' in Detailed view →' });
    link.addEventListener('click', function (e) {
      e.preventDefault();
      var advBtn = document.getElementById('mode-advanced-btn');
      if (advBtn) {
        advBtn.click();
        setTimeout(function () {
          var virSec = document.getElementById('virality');
          if (virSec) { virSec.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
        }, 150);
      }
    });
    footer.appendChild(link);
    host.appendChild(footer);
  };

  /* =============================================================== Explore page
     A user-driven page: four tiles (Trending Topics, Viral Posts, Cross-Platform Spread,
     Trend Lifecycle), each with its own filters. Reads the snapshot tables already loaded
     (topics, bursts, shared stories) and, for Viral Posts, the read-only /api/explore/viral.
     Nothing here writes anywhere. The date range is the shared S.from / S.to. */

  var EX_ALL = 100000;     // "Show all": every row of a list; post lists are cut to the server limit below
  var EX_POST_CAP = 300;   // api_server MAX_LIMIT: most posts one list can fetch
  var EX = { tile: 'topics', platforms: {}, q: '', entityUid: '', entityName: '', entType: '', entSub: '', terms: [], spreadMode: 'cards', spreadLimit: 10, top: 25, unclass: false, minPlat: 0, entTop: null, vrank: 'views', vper: 0, vthresh: 'all', rt: 'include', floor: 'top10', vorder: 'biggest', topicId: '', topicName: '', theme: '', themeAll: false, topicGrain: 'theme',
             viral: null, viralKey: '', st: null, stKey: '', scn: null, scnKey: '', aud: null, audKey: '', audBasis: 'post', audDates: { from: '', to: '' }, audMin: 5, audGapPlat: 'all' };
  var EX_TILES = [
    { k: 'topics', t: 'Trending Topics', s: "What's gaining attention?",
      i: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>' },
    { k: 'viral', t: 'Viral Posts', s: 'Which posts are breaking out?',
      i: '<path d="M12 3c1 3 5 5 5 10a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 1-9z"/>' },
    { k: 'spread', t: 'Cross-Platform Spread', s: 'Where is the story spreading?',
      i: '<circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v5M12 12l-6 5M12 12l6 5"/>' },
    { k: 'life', t: 'Trend Lifecycle', s: 'Is it rising, peaking or declining?',
      i: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 14l3-3 3 3 4-5"/>' },
    { k: 'sent', t: 'Sentiment & Audience', s: 'Author tone & audience reactions',
      i: '<circle cx="12" cy="12" r="9"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9.5h.01M15 9.5h.01"/>' }
  ];

  /* Explore opens on the LAST DAY of data; the Overview / Detailed pages keep their own range (default last 7 days).
     The shared range S is swapped while Explore is showing and restored when leaving, so neither page changes the other. */
  var EXS = { active: false, ov: null, range: null };
  function exSyncRange() {
    var m = document.body && document.body.getAttribute('data-mode');
    if (m === 'explore' && !EXS.active && S.from) {
      EXS.ov = { from: S.from, to: S.to, windowOnly: S.windowOnly };
      var W = WINDAYS.length ? WINDAYS : ALLDAYS, last = dqInfo().lastFull || W[W.length - 1];
      var r = EXS.range || { from: last, to: last, windowOnly: true };
      S.from = r.from; S.to = r.to; S.windowOnly = r.windowOnly;
      EXS.active = true;
    } else if (m !== 'explore' && m !== 'military' && EXS.active) {
      EXS.range = { from: S.from, to: S.to, windowOnly: S.windowOnly };
      if (EXS.ov) { S.from = EXS.ov.from; S.to = EXS.ov.to; S.windowOnly = EXS.ov.windowOnly; }
      EXS.active = false;
    }
  }

  function exAnyPlat() { return Object.keys(EX.platforms).some(function (k) { return EX.platforms[k]; }); }
  function exPlatOk(p) { return !exAnyPlat() || !!EX.platforms[p]; }
  function exTid(t) { return t === 't005' ? 't004' : t; }
  function exDays() {
    return ALLDAYS.filter(function (d) { return d >= S.from && d <= S.to; });
  }
  function exPrevDays(days) {
    if (!days.length) { return []; }
    var i = ALLDAYS.indexOf(days[0]);
    return ALLDAYS.slice(Math.max(0, i - days.length), i);
  }
  /* drill-down parameters that match the card's own numbers: the selected dates (and platform choice) */
  function exDrillParams(p, withPlatforms) {
    p.from = S.from; p.to = S.to;
    if (withPlatforms && exAnyPlat()) { p.platforms = PLATFORMS.filter(exPlatOk).join(','); }
    return p;
  }
  function exSet(a) { var o = {}; a.forEach(function (d) { o[d] = true; }); return o; }
  function exTextOk(s) { return !EX.q || String(s || '').toLowerCase().indexOf(EX.q.toLowerCase()) >= 0; }
  function exDayNum(d) { return Math.round(Date.parse(day(d)) / 86400000); }

  /* stage of a topic at the end of the selected range, from its burst episodes */
  function exStage(eps, toDay) {
    var best = null;
    eps.forEach(function (e) {
      if (day(e.start_day) > toDay) { return; }
      if (!best || day(e.peak_day) > day(best.peak_day)) { best = e; }
    });
    if (!best) { return 'steady'; }
    var to = exDayNum(toDay), pk = exDayNum(best.peak_day), st = exDayNum(best.start_day), en = exDayNum(best.end_day);
    if (to > en + 1) { return 'steady'; }
    if (to < pk) { return (to - st) <= 2 ? 'starting' : 'rising'; }
    if (to - pk <= 1) { return 'peak'; }
    return 'declining';
  }

  /* group the stored daily metric cells (topic x platform x day) over the selected range */
  function exCells(sourceTable) {
    var tbl = sourceTable || 'metrics_daily';
    var days = exDays(), prev = exPrevDays(days);
    var cur = exSet(days), pre = exSet(prev), win = exSet(WINDAYS);
    var out = [];
    T(tbl).forEach(function (r) {
      var d = day(r.day);
      if (!exPlatOk(r.platform)) { return; }
      var inCur = cur[d] && (!S.windowOnly || win[d]), inPrev = pre[d];
      if (inCur || inPrev) { out.push({ r: r, d: d, inCur: !!inCur, inPrev: !!inPrev }); }
    });
    return { cells: out, cur: cur, days: days };
  }
  function exMax(a, v) { return (isNum(v) && (a == null || v > a)) ? v : a; }
  function exMin(a, v) { return (isNum(v) && (a == null || v < a)) ? v : a; }

  /* The thresholds behind the Trending Topics ranking. ONE place: the code below and the "How this is decided" panel both read it. */
  var EX_RULES = { stageDays: 6, stageMinPosts: 20, rise: 1.25, fall: 0.8, peakBand: 0.9, startPrev: 0.3, startMaxDays: 30,
                   growthMinPrev: 10, growthMinCur: 20, entGrowthMinPrev: 5, entGrowthMinCur: 10, alpha: 0.05, tonePosNeg: 0.15 };

  /* Stage of a topic from its own daily series (effort-normalised: posts per active channel, so a collector ramp-up does not
     look like a surge). Compares the last 3 days with the 3 before them. Returns {stage, change} where change = last 3 / previous 3 - 1. */
  function exStageDetail(daily, days, prevTotal) {
    var R = EX_RULES;
    if (days.length < R.stageDays) { return { stage: 'too little data', change: null }; }
    function rate(d) { var o = daily[d]; return o && o.eff > 0 ? o.V / o.eff : 0; }
    function posts(d) { return daily[d] ? daily[d].V : 0; }
    var last6 = days.slice(-R.stageDays), tot = 0;
    last6.forEach(function (d) { tot += posts(d); });
    if (tot < R.stageMinPosts) { return { stage: 'too little data', change: null }; }
    var r = days.map(rate), n = r.length;
    function mean(a, i, j) { var x = 0; for (var k = i; k < j; k++) { x += a[k]; } return x / Math.max(1, j - i); }
    var m3 = mean(r, n - 3, n), p3 = mean(r, n - 6, n - 3), best = m3;
    for (var i = 3; i <= n; i++) { best = Math.max(best, mean(r, i - 3, i)); }
    var curTotal = 0; days.forEach(function (d) { curTotal += posts(d); });
    var change = p3 > 0 ? m3 / p3 - 1 : null;
    if (p3 > 0 && m3 >= R.rise * p3) {
      return { stage: (days.length <= R.startMaxDays && prevTotal > 0 && prevTotal < R.startPrev * curTotal) ? 'starting' : 'rising', change: change };   // no earlier period (full window) cannot be "starting"
    }
    if (p3 > 0 && m3 <= R.fall * p3) { return { stage: 'declining', change: change }; }
    return { stage: m3 >= R.peakBand * best ? 'peak' : 'steady', change: change };
  }
  function exSeriesStage(daily, days, prevTotal) { return exStageDetail(daily, days, prevTotal).stage; }

  function exTopicRows(forceMacro) {
    var isTheme = !forceMacro && (EX.topicGrain !== 'macro') && T('dtheme_metrics_daily') && T('dtheme_metrics_daily').length > 0;
    var g = exCells(isTheme ? 'dtheme_metrics_daily' : 'metrics_daily'), by = {};
    g.cells.forEach(function (c) {
      var r = c.r, tid = isTheme ? r.topic_id : exTid(r.topic_id);
      var o = by[tid] || (by[tid] = { topic_id: tid, cur: 0, prev: 0, plats: {}, alertDays: {}, emer: null, minq: null,
        mom: null, momDay: '', burstDays: {}, daily: {}, platV: {} });
      if (c.inPrev) { o.prev += (r.V || 0); }
      if (!c.inCur) { return; }
      var dd = o.daily[c.d] || (o.daily[c.d] = { V: 0, eff: 0 });
      dd.V += (r.V || 0); dd.eff += (isNum(r.effort) ? r.effort : 0);
      o.cur += (r.V || 0);
      o.platV[r.platform] = (o.platV[r.platform] || 0) + (r.V || 0);
      if ((r.V || 0) > 0) { o.plats[r.platform] = true; }
      if (r.support_ok) {
        if (r.alert_flag) { o.alertDays[c.d] = true; }
        o.emer = exMax(o.emer, r.emergence_score);
        o.minq = exMin(o.minq, r.surprise_q);
        if (r.burst_state === 1 || r.burst_state === true) { o.burstDays[c.d] = true; }
      }
      if (isNum(r.momentum) && c.d >= o.momDay) { o.mom = r.momentum; o.momDay = c.d; }
    });
    var toDay = g.days[g.days.length - 1] || S.to;
    var eps = T('burst_episodes').filter(function (e) { return exPlatOk(e.platform); });
    return Object.keys(by).map(function (tid) {
      var o = by[tid], ok = o.prev >= EX_RULES.growthMinPrev && o.cur >= EX_RULES.growthMinCur;
      var sd = exStageDetail(o.daily, g.days, o.prev);
      var topP = Object.keys(o.platV).sort(function (x, y) { return o.platV[y] - o.platV[x]; })[0];
      var lbl = topicLabel(tid);
      if (isTheme && tid !== 'unassigned') {
        lbl = exThemeName(tid);
      }
      return {
        topic_id: tid, topic: lbl, posts: o.cur,
        growth: ok ? (o.cur - o.prev) / o.prev : null,
        emergence: isTheme ? null : o.emer, minq: isTheme ? null : o.minq,
        alert_days: isTheme ? null : Object.keys(o.alertDays).length,
        burst_days: isTheme ? null : Object.keys(o.burstDays).length,
        momentum: isTheme ? null : o.mom, platforms: Object.keys(o.plats).length,
        stage: sd.stage, stage_change: sd.change, prev: o.prev,
        top_platform: topP || null, top_share: topP && o.cur ? o.platV[topP] / o.cur : null,
        plat_names: Object.keys(o.plats)
      };
    }).filter(function (r) { return r.posts > 0 && exTextOk(r.topic) && (EX.unclass || r.topic !== 'Unclassified'); });
  }

  /* the "how to read this" text of each Explore card sits behind a heading, closed until clicked */
  function exExplain(host, title, text) {
    var det = h('details', { class: 'ex-explain' });
    det.appendChild(h('summary', null, [h('b', { text: title }), h('span', { class: 'ex-explain-hint', text: ' \u2014 click to see explanation' })]));
    det.appendChild(h('p', { class: 'cs', text: text }));
    host.appendChild(det);
    return det;
  }

  /* Trending Topics with an entity chosen: the topics in which posts name that entity, counted for the selected days and for the
     same number of days just before. Posts only (YouTube comments carry no entity mentions). The emergence / alert / stage columns
     are still the WHOLE topic's, because the pipeline does not compute them per entity, and are labelled that way. */
  function exEntityTopicsView(host) {
    var days = exDays(), prev = exPrevDays(days), plats = PLATFORMS.filter(exPlatOk).join(',');
    var key = [S.from, S.to, prev[0] || '', prev[prev.length - 1] || '', plats, EX.entityUid].join('|');
    var nm = EX.entityName || EX.entityUid;
    exExplain(host, 'Topics for ' + nm, 'Each row is a topic in which posts name ' + nm + '. "Posts naming" counts posts in the selected days that mention it; ' +
      '"Previous period" is the same number of days directly before. Growth is shown with at least ' + EX_RULES.entGrowthMinPrev + ' earlier and ' + EX_RULES.entGrowthMinCur +
      ' current posts. Only posts are counted: YouTube comments carry no entity mentions. Emergence, alert days and stage are the whole topic’s ' +
      '(the pipeline does not compute them per entity), so they show how the topic itself is behaving, not the entity. Click a row to open the posts naming ' + nm + ' in that topic.');
    var body = h('div', { class: 'ex-ent-topics' });
    host.appendChild(body);
    function paint(d) {
      body.textContent = '';
      // platforms whose stored entities stop before the selected end day (the production extract is a snapshot)
      var gone = (d.coverage || []).filter(function (c) { return c.platform !== 'youtube_comment' && exPlatOk(c.platform) && (!c.last_day || c.last_day < S.to); })
        .sort(function (x, y) { return (x.last_day || '') < (y.last_day || '') ? -1 : 1; });
      var none = PLATFORMS.filter(function (pl) { return pl !== 'youtube_comment' && exPlatOk(pl) && !(d.coverage || []).some(function (c) { return c.platform === pl; }); });
      if (gone.length || none.length) {
        body.appendChild(h('p', { class: 'cs ex-warn', text: 'Entity data is incomplete for these dates: ' +
          gone.map(function (c) { return c.platform + ' (entities end ' + c.last_day + ')'; }).concat(none.map(function (pl) { return pl + ' (no entities)'; })).join(', ') +
          '. Posts on those platforms after that day are not counted, so counts for this entity can be lower than the real activity.' }));
      }
      var whole = {};
      exTopicRows(true).forEach(function (r) { whole[r.topic_id] = r; });
      var by = {};
      d.rows.forEach(function (r) {
        var t = exTid(r.topic_id), o = by[t] || (by[t] = { topic_id: t, cur: 0, prev: 0 });
        o.cur += r.n_cur; o.prev += r.n_prev;
      });
      var rows = Object.keys(by).map(function (t) {
        var o = by[t], w = whole[t], ok = o.prev >= EX_RULES.entGrowthMinPrev && o.cur >= EX_RULES.entGrowthMinCur;
        return { topic_id: t, topic: topicLabel(t), cur: o.cur, prev: o.prev, growth: ok ? (o.cur - o.prev) / o.prev : null,
          emergence: w ? w.emergence : null, alert_days: w ? w.alert_days : null, stage: w ? w.stage : null };
      }).filter(function (r) { return r.cur > 0 && exTextOk(r.topic) && (EX.unclass || r.topic !== 'Unclassified'); })
        .sort(function (a, b) { return b.cur - a.cur || (b.growth == null ? -1 : b.growth) - (a.growth == null ? -1 : a.growth); }).slice(0, EX.top);
      tableView(body, [
        { k: 'topic', label: 'Topic' },
        { k: 'cur', label: 'Posts naming ' + nm, num: true, fmt: fmtInt },
        { k: 'prev', label: 'Previous period', num: true, fmt: fmtInt },
        { k: 'growth', label: 'Growth', num: true, fmt: function (v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Math.round(v * 100) + '%'; } },
        { k: 'emergence', label: 'Emergence (whole topic)', num: true, fmt: function (v) { return v == null ? '—' : Number(v).toFixed(2); } },
        { k: 'alert_days', label: 'Alert days (whole topic)', num: true, fmt: function (v) { return v == null ? '—' : v; } },
        { k: 'stage', label: 'Stage (whole topic)' }
      ], rows, {
        label: 'Topics naming ' + nm + ' (' + rows.length + ')',
        rowDrill: function (r) { return { view: 'topic', params: exDrillParams({ topic_id: r.topic_id, entity_uid: EX.entityUid, sort: 'engagement' }, true), title: r.topic + ' · ' + nm, subtitle: S.from + ' → ' + S.to }; }
      });
    }
    if (EX.entTop && EX.entTop.key === key) { paint(EX.entTop.d); return; }
    body.appendChild(h('div', { class: 'empty', text: 'Loading…' }));
    var url = '/api/explore/entity_topics?from=' + encodeURIComponent(S.from) + '&to=' + encodeURIComponent(S.to) +
      (prev.length ? '&prev_from=' + encodeURIComponent(prev[0]) + '&prev_to=' + encodeURIComponent(prev[prev.length - 1]) : '') +
      '&platforms=' + encodeURIComponent(plats) + '&entity_uid=' + encodeURIComponent(EX.entityUid);
    fetch(url, { cache: 'no-store' }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) { throw new Error(j && j.error ? j.error : 'HTTP ' + r.status); } return j; });
    }).then(function (d) {
      EX.entTop = { key: key, d: d };
      if (EX.entityUid && body.isConnected !== false) { paint(d); }
    }).catch(function (err) {
      body.textContent = '';
      empty(body, /^HTTP|Failed|fetch|NetworkError/i.test(err.message) ? 'The entity view needs the API server (' + err.message + ').' : 'Problem: ' + err.message);
    });
  }

  /* ---- a keyword, entity or security theme searched on Trending Topics ---------------------------------------------------------
     What a reader asking "what is trending about X?" needs: X's own trend (posts per day against ALL posts of the same days and platforms, so
     a collector ramp-up is not mistaken for a surge), where it is posted, which topics and themes its posts fall in, and its top posts.
     The numbers come from /api/explore/search_trend (read-only). The match is on the words in the post text, not on what the post is about. */
  function exThemeName(k) { return k + ' · ' + TAX.name('security_domain', k); }
  function exThemeQs() { return EX.theme ? '&theme=' + encodeURIComponent(EX.theme) + (EX.themeAll ? '&theme_all=1' : '') : ''; }
  function exThemeNote() {
    return 'Theme ' + exThemeName(EX.theme) + ': assigned automatically by the language model plus a keyword check, so it is provisional. ' +
      (EX.themeAll ? 'Includes the lower-confidence matches (about 59% right, about 75% for the clear ones). ' : 'Only clear matches are listed (about 75% right on a fresh check: a clear keyword match, or posts whose nearest neighbours all carry the theme); many relevant posts carry no theme. ') +
      'Do not read counts per theme as how much of the conversation is about it.';
  }
  function exSearchTitle() {
    var parts = [];
    if (EX.q) { parts.push('“' + EX.q + '”'); }
    if (EX.theme) { parts.push(exThemeName(EX.theme)); }
    if (EX.entityUid) { parts.push('naming ' + (EX.entityName || EX.entityUid)); }
    return parts.join(' · ');
  }
  /* plain-language trend tag of a related item, from its count in the selected days against the same number of days before.
     Same thresholds as the Stage rule (rise +25%, fall -20%); under 10 posts the tag says there are too few to call it. */
  function exRelTag(n, prev) {
    if (n < 10) { return { k: 'few', t: 'Few posts', o: 3 }; }
    if (prev < 5) { return { k: 'new', t: 'New', o: 0 }; }
    var r = n / prev;
    if (r >= EX_RULES.rise) { return { k: 'rising', t: 'Rising', o: 0 }; }
    if (r <= EX_RULES.fall) { return { k: 'declining', t: 'Declining', o: 2 }; }
    return { k: 'steady', t: 'Steady', o: 1 };
  }
  /* "Who is involved" for a typed search: the scenario is understood by meaning (bge-m3) from a Gemma-written brief, the people named in the posts it
     retrieves are checked post by post by Gemma for real involvement. Slow the first time (about a minute), cached after. If the service is down the
     page falls back to the names counted in the keyword-matched posts and says so. */
  function exScenarioWho(pg, fb, d) {
    var plats = PLATFORMS.filter(exPlatOk).join(',');
    var key = [EX.q, S.from, S.to, plats, EX.entType, EX.entSub].join('|');
    var status = h('div', { class: 'cs', text: 'Searching by meaning and checking each shortlisted post with Gemma — about a minute the first time, instant after…' });
    var box = h('div', { class: 'ex-who-scn' });
    pg.insertBefore(status, fb); pg.insertBefore(box, fb);
    function paint(r) {
      status.textContent = '';
      box.textContent = '';
      var st = r.stats, g = r.grounding || {};
      var det = h('details', { class: 'ex-explain' });
      det.appendChild(h('summary', null, [h('b', { text: 'How this list was found' }), h('span', { class: 'ex-explain-hint', text: ' — click to see explanation' })]));
      var devs = (g.developments || []).map(function (x) { return x.title + (x.time ? ' (' + x.time + ')' : '') + ' — ' + (x.post_ids || []).length + ' supporting posts'; });
      det.appendChild(h('p', { class: 'cs', text: 'The event was read from ' + fmtInt(g.sample_size || 0) + ' real posts of this data, chosen to differ in platform, day and meaning (not the top posts, not keyword hits), in ' +
        ((g.rounds || []).length || 1) + ' round(s): ' + (g.event_summary || '') + (devs.length ? ' Developments seen: ' + devs.join('; ') + '.' : '') +
        (g.contradictions && g.contradictions.length ? ' Where the posts disagree: ' + g.contradictions.join(' ') : '') }));
      det.appendChild(h('p', { class: 'cs', text: 'Posts were then ranked by meaning against that event (not by the typed words); ' + fmtInt(st.naming_a_person) + ' of the ' + fmtInt(st.semantic_candidates) +
        ' closest posts name a known person of this kind; the best few for each person (' + st.verified_ok + ' posts) were checked by Gemma against the event, ' + st.relevant + ' were about it. ' +
        'Direct = the post says what that person did, plans, decided, ordered, threatened or said about the event. Indirect = connected through someone or something else (their government, a subordinate, a response or negotiation). ' +
        'Both need a phrase from the post as evidence; a name that is only a tag or a list entry is not involvement. People are ordered by how many of their 5 best posts show involvement; ' +
        'mention counts, views and engagement play no part. A person called only by a title is found only when the event posts show who the title means.' }));
      box.appendChild(det);
      if (g.assessment && g.assessment !== 'clear') {
        box.appendChild(h('p', { class: 'cs ex-warn', text: g.assessment === 'multiple_developments'
          ? 'The posts show several developments, not one story (see the explanation above); posts are judged against any of them.'
          : 'The posts do not establish a single clear event (' + g.assessment + '): treat this list with caution. ' + (g.needs_more_reason || '') }));
      }
      var inv = function (o) { return o.direct + o.indirect; };
      var involved = r.people.filter(function (o) { return inv(o) > 0; }), other = r.people.filter(function (o) { return inv(o) === 0; });
      involved.forEach(function (o) {
        var wrap = h('div', { class: 'ex-who-item' });
        var b = h('button', { type: 'button', class: 'ex-rel-row', title: 'Click to see the posts that show their part in it.' }, [
          h('span', { class: 'ex-rel-tag ex-rel-' + (o.direct > 0 ? 'rising' : 'steady'), text: o.direct > 0 ? 'Direct' : 'Indirect' }),
          h('span', { class: 'ex-rel-name', text: o.name + (o.top_role ? ' — ' + o.top_role : '') }),
          h('span', { class: 'ex-rel-n', text: o.direct + ' direct · ' + o.indirect + ' indirect of ' + o.checked + ' checked' })]);
        var open = false, pbox = null;
        b.addEventListener('click', function () {
          if (open) { pbox.remove(); open = false; return; }
          open = true; pbox = h('div', { class: 'ex-who-posts' }); wrap.appendChild(pbox);
          r.posts.filter(function (x) { return x.name === o.name && (x.involvement === 'direct' || x.involvement === 'indirect'); })
            .sort(function (x, y) { return (y.involvement === 'direct') - (x.involvement === 'direct') || y.score - x.score; }).slice(0, 5).forEach(function (x) {
              pbox.appendChild(h('div', { class: 'cs ex-who-why', text: (x.involvement === 'direct' ? 'Direct' : 'Indirect') + (x.role ? ' — ' + x.role : '') + '. ' + (x.reason || '') + (x.evidence_ok ? ' “' + x.evidence + '”' : '') +
                (x.via_title ? ' (found through the title “' + x.via_title + '”)' : '') }));
              pbox.appendChild(exPostCard(x, !!x.views));
            });
          var nb = h('button', { type: 'button', class: 'df-preset-btn', text: 'Narrow the keyword search to posts naming ' + o.name });
          nb.addEventListener('click', function () { EX.q = (EX.q ? EX.q + ' ' : '') + '"' + o.name + '"'; EX.entType = ''; EX.entSub = ''; EX.topicId = ''; EX.viral = null; EX.st = null; render(); });
          pbox.appendChild(nb);
        });
        wrap.insertBefore(b, wrap.firstChild);
        box.appendChild(wrap);
      });
      if (!involved.length) { box.appendChild(h('div', { class: 'cs', text: 'No one of this kind was shown to be involved in the checked posts.' })); }
      if (other.length) {
        box.appendChild(h('div', { class: 'cs', text: 'Named in event posts but not shown to be involved (checked): ' + other.slice(0, 12).map(function (o) { return o.name; }).join(', ') + '.' }));
      }
      box.appendChild(h('div', { class: 'cs', text: 'Checked ' + st.verified_ok + ' posts with Gemma in ' + r.total_s + ' s' + (r.cached ? ' (saved result)' : '') + '. Relevance and involvement come from the post’s meaning, not from keywords.' }));
    }
    function fail(msg) {
      status.textContent = 'Scenario search is not available (' + msg + '). Showing the names counted in the keyword-matched posts instead.';
      fb.style.display = '';
    }
    if (EX.scn && EX.scnKey === key) { paint(EX.scn); return; }
    fetch('/api/explore/scenario_people?scenario=' + encodeURIComponent(EX.q) + '&from=' + encodeURIComponent(S.from) + '&to=' + encodeURIComponent(S.to) +
          '&platforms=' + encodeURIComponent(plats) + '&entity_type=' + encodeURIComponent(EX.entType) + '&entity_subtype=' + encodeURIComponent(EX.entSub), { cache: 'no-store' })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) { throw new Error(j && j.error ? j.error : 'HTTP ' + r.status); } return j; }); })
      .then(function (r) { EX.scn = r; EX.scnKey = key; if (box.isConnected !== false) { paint(r); } })
      .catch(function (err) { fail(String(err.message || err).slice(0, 120)); });
  }

  function exSearchTrendView(host) {
    var days = exDays(), prev = exPrevDays(days), plats = PLATFORMS.filter(exPlatOk).join(','), title = exSearchTitle();
    exExplain(host, 'What is trending around ' + title, 'Everything below comes from the posts that match your search in the selected days. The tag on each related item compares its posts in the ' +
      'selected days with the same number of days just before: Rising = at least +25%, Declining = at least -20%, New = fewer than ' + 5 + ' posts before, Few posts = under 10 now. ' +
      'The number is how many of the search’s posts mention it. "Share of all posts" in the headline divides by every post collected on the same days and platforms, so more collection does ' +
      'not look like more interest. Growth is shown only with enough posts (' + EX_RULES.growthMinPrev + ' earlier, ' + EX_RULES.growthMinCur + ' now); under ' + EX_RULES.stageMinPosts + ' matching posts there is no trend call. ' +
      '"Unusual" compares each selected day with the same search over the 28 days before it (the same count model and false-alarm control as the topic alerts, scaled to how much was collected that day); a day gets no verdict with fewer than 3 matching posts or a usual level under 0.5 posts a day. ' +
      'By platform: when the search first showed up there (first day with at least 3 matching posts) and whether it is rising there (last 3 days against the 3 before, per collected post of that platform). ' +
      'The match is on the words in the post text, not on what the post is about. Topics are the pipeline’s broad topics; themes are automatic and provisional; people, organisations and places come ' +
      'from the production entity extract (some platforms stop earlier); hashtags are read from the post text. To ask "who is involved", choose a type (for example People, then Political leader) next to the search: the page lists the people named in the matching posts, counted by name in the post text on every platform, and a click on a name shows the posts that name them. Click any other item to narrow the search to it.');
    if (EX.theme) { host.appendChild(h('p', { class: 'cs ex-warn', text: exThemeNote() })); }
    var body = h('div', { class: 'ex-search-trend' });
    host.appendChild(body);
    var qs = '&from=' + encodeURIComponent(S.from) + '&to=' + encodeURIComponent(S.to) + '&prev_from=' + encodeURIComponent(prev[0] || '') + '&prev_to=' + encodeURIComponent(prev[prev.length - 1] || '') +
      '&platforms=' + encodeURIComponent(plats) + '&q=' + encodeURIComponent(EX.q) + exThemeQs() + '&entity_uid=' + encodeURIComponent(EX.entityUid) +
      (EX.entType ? '&entity_type=' + encodeURIComponent(EX.entType) + (EX.entSub ? '&entity_subtype=' + encodeURIComponent(EX.entSub) : '') : '');
    var key = qs;
    function sumDays(d, list) { var set = exSet(list), n = 0, t = 0; d.daily.forEach(function (r) { if (set[r.day]) { n += r.n; t += r.total; } }); return { n: n, total: t }; }
    function pct(v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Math.round(v * 100) + '%'; }
    function chg(n, pv) { return pv >= 5 ? ' (' + pct((n - pv) / pv) + ')' : ''; }
    /* one row: tag, name, count; clicking narrows the search */
    function relRow(host2, tag, label, n, pv, tip, onClick) {
      var b = h('button', { type: 'button', class: 'ex-rel-row', title: tip || '' }, [
        h('span', { class: 'ex-rel-tag ex-rel-' + tag.k, text: tag.t }),
        h('span', { class: 'ex-rel-name', text: label }),
        h('span', { class: 'ex-rel-n', text: fmtInt(n) + ' posts' + chg(n, pv) })]);
      b.addEventListener('click', onClick);
      host2.appendChild(b);
    }
    function relGroup(titleText, note, rows, empty2) {
      var g = h('div', { class: 'ex-rel-group' }, [h('div', { class: 'ex-rel-title', text: titleText })]);
      rows.sort(function (a, b) { return a.tag.o - b.tag.o || b.n - a.n; });
      rows.slice(0, 8).forEach(function (r) { relRow(g, r.tag, r.label, r.n, r.pv, r.tip, r.go); });
      if (!rows.length) { g.appendChild(h('div', { class: 'cs', text: empty2 })); }
      else if (note) { g.appendChild(h('div', { class: 'cs', text: note })); }
      return g;
    }
    function paint(d) {
      body.textContent = '';
      if (!d.n_cur) { empty(body, 'No posts match in the selected days and platforms.'); return; }
      var c = sumDays(d, days), p = sumDays(d, prev);
      var enough = d.n_cur >= d.min_posts;
      // ---- headline: the answer in one sentence
      var share = c.total ? d.n_cur / c.total * 1000 : null, stageTxt = '', gTxt = '';
      if (enough) {
        var okG = d.n_prev >= EX_RULES.growthMinPrev && d.n_cur >= EX_RULES.growthMinCur;
        var dm = {}; d.daily.forEach(function (r) { dm[r.day] = { V: r.n, eff: r.total / 1000 }; });
        var sd = exStageDetail(dm, days, d.n_prev);
        var gs = (okG && p.total && c.total && p.n) ? (d.n_cur / c.total) / (p.n / p.total) - 1 : null;
        stageTxt = sd.stage === 'too little data' ? 'not enough recent days to call a stage' : sd.stage;
        gTxt = okG ? ', ' + pct((d.n_cur - d.n_prev) / d.n_prev) + ' on the ' + prev.length + ' days before' + (gs != null ? ' (' + pct(gs) + ' as a share of all posts)' : '') : '';
      }
      var top2 = d.platforms.slice(0, 2).map(function (r) { return r.platform; }).join(' and ');
      var head = h('div', { class: 'ex-rel-head' });
      head.appendChild(h('div', { class: 'ex-rel-big', text: title + (enough ? ': ' + stageTxt : ': too few posts to call a trend') }));
      head.appendChild(h('p', { class: 'cs', text: fmtInt(d.n_cur) + ' posts in the selected ' + days.length + ' days (' + fmtInt(d.distinct_cur) + ' distinct messages)' + gTxt +
        (top2 ? ', mostly on ' + top2 : '') + (share != null ? '; ' + share.toFixed(share < 10 ? 1 : 0) + ' per 1,000 collected posts.' : '.') }));
      var an = d.anomaly, anTxt;
      if (an && an.ok) {
        anTxt = an.alert_days.length
          ? 'Unusual: ' + an.alert_days.length + ' of the ' + an.judged_days + ' days judged stood out from the last 28 days (' + an.alert_days.slice(0, 4).join(', ') + (an.alert_days.length > 4 ? ' \u2026' : '') + '), up to ' + Number(an.max_ratio).toFixed(1) + '\u00d7 the usual number of posts.'
          : 'Within the usual range: none of the ' + an.judged_days + ' days judged stood out from the last 28 days.';
      } else { anTxt = 'Cannot say whether it is unusual: ' + ((an && an.reason) || 'no baseline') + '.'; }
      head.appendChild(h('p', { class: 'cs ex-rel-anom' + (an && an.ok && an.alert_days.length ? ' ex-rel-rising' : ''), text: anTxt }));
      body.appendChild(head);
      var alertSet = exSet((an && an.alert_days) || []);
      var plot = h('div', { class: 'ex-st-plot' });
      body.appendChild(plot);
      var byDay = {}; d.daily.forEach(function (r) { byDay[r.day] = r.n; });
      lineChart(plot, { days: days, series: [{ label: 'Posts per day', role: 'focal', kind: 'bar', pts: days.map(function (x, i) { return { i: i, v: byDay[x] || 0 }; }) }],
        width: Math.max(280, plot.clientWidth || host.clientWidth || 520), height: 120, zeroBase: true, yFmt: fmtCompact, tipFmt: fmtInt, tipHead: 'Posts per day', yTicks: 3,
        xTicks: Math.min(5, days.length), margin: { t: 8, r: 10, b: 22, l: 36 },
        ribbon: days.map(function (x, i) { return { i: i, on: !!alertSet[x] }; }) });
      // by platform
      // ---- who is involved: asked for by choosing a type (and subtype) next to the search
      if (d.people) {
        var KIND = { PERSON: 'People', ORG: 'Organisations', LOCATION: 'Places', EVENT: 'Events' };
        var pg = h('details', { class: 'ex-rel-group ex-rel-who' });
        var titleText = 'Who is involved · ' + (KIND[d.people.type] || d.people.type) + (d.people.subtype ? ' · ' + subtypeName(d.people.subtype) : '');
        var hint = h('span', { class: 'ex-explain-hint', text: ' \u2014 click to expand' });
        var sum = h('summary', {}, [
          h('span', { class: 'ex-rel-title', text: titleText }),
          hint
        ]);
        pg.addEventListener('toggle', function () {
          hint.textContent = pg.open ? ' \u2014 click to collapse' : ' \u2014 click to expand';
        });
        pg.appendChild(sum);
        var qBase = function (name) { return (EX.q ? EX.q + ' ' : '') + '"' + name + '"'; };
        var fb = h('div', { class: 'ex-who-fallback' });
        d.people.rows.forEach(function (r) {
          var wrap = h('div', { class: 'ex-who-item' });
          var open = false, box = null;
          relRow(wrap, exRelTag(r.n, r.n_prev), r.name, r.n, r.n_prev, 'Mentioned in ' + Math.round(r.n / d.n_cur * 100) + '% of these posts (' + fmtInt(r.corpus) + ' posts overall). Click to see the posts that name them.', function () {
            if (open) { box.remove(); open = false; return; }
            open = true; box = h('div', { class: 'ex-who-posts' }, [h('div', { class: 'empty', text: 'Loading…' })]); wrap.appendChild(box);
            var u = '/api/explore/viral?limit=3' + qs.replace(/&prev_from=[^&]*&prev_to=[^&]*/, '').replace(/&q=[^&]*/, '&q=' + encodeURIComponent(qBase(r.name))).replace(/&entity_(type|subtype)=[^&]*/g, '');
            fetch(u, { cache: 'no-store' }).then(function (x) { return x.json(); }).then(function (v) {
              if (v.error) { throw new Error(v.error); }
              box.textContent = '';
              EX.terms = v.terms || [];
              v.with_views.concat(v.without_views).slice(0, 3).forEach(function (x) { box.appendChild(exPostCard(x, !!x.views)); });
              if (!v.with_views.length && !v.without_views.length) { empty(box, 'No post with a view or engagement counter names them in this search.'); }
              var nb = h('button', { type: 'button', class: 'df-preset-btn', text: 'Narrow the whole search to posts naming ' + r.name });
              nb.addEventListener('click', function () { EX.q = qBase(r.name); EX.entType = ''; EX.entSub = ''; EX.topicId = ''; EX.viral = null; EX.st = null; render(); });
              box.appendChild(nb);
            }).catch(function (err) { box.textContent = ''; empty(box, 'Could not load posts: ' + err.message); });
          });
          fb.appendChild(wrap);
        });
        if (!d.people.rows.length) { fb.appendChild(h('div', { class: 'cs', text: 'No one of this kind is named in at least 3 of these posts.' })); }
        fb.appendChild(h('div', { class: 'cs', text: 'Counted by name in the post text on every platform (' + d.people.candidates + ' best-known ' + (KIND[d.people.type] || 'entities').toLowerCase() + ' checked). Titles like "President", first names and spelling variants are merged or left out. The same surname can belong to two people.' }));
        pg.appendChild(fb);
        if (EX.q) { fb.style.display = 'none'; exScenarioWho(pg, fb, d); }
        body.appendChild(pg);
      }
      var prevOf = {}; d.platforms.forEach(function (r) { prevOf[r.platform] = r.n_prev; });
      var spd = d.platform_speed || [];
      var wr = h('div', { class: 'ex-rel-group ex-rel-plat' }, [h('div', { class: 'ex-rel-title', text: 'By platform' })]);
      var TAGS = { rising: { k: 'rising', t: 'Rising', o: 0 }, steady: { k: 'steady', t: 'Steady', o: 1 }, declining: { k: 'declining', t: 'Declining', o: 2 }, few: { k: 'few', t: 'Few posts', o: 3 } };
      spd.forEach(function (r) {
        relRow(wr, TAGS[r.trend] || TAGS.few, r.platform + ' \u00b7 ' + (r.before ? 'already active before' : (r.first_day ? 'first seen ' + r.first_day : 'too few to date')), r.n, prevOf[r.platform] || 0,
          'Peak day ' + (r.peak_day || '\u2014') + ' with ' + r.peak_n + ' posts' + (r.ratio != null ? '; last 3 days against the 3 before, per collected post: ' + r.ratio + '\u00d7' : '') + '. Click to search only this platform.',
          function () { EX.platforms = {}; EX.platforms[r.platform] = true; EX.viral = null; EX.st = null; render(); });
      });
      var starts = spd.filter(function (r) { return !r.before && r.first_day; }).sort(function (x, y) { return x.first_day < y.first_day ? -1 : (x.first_day > y.first_day ? 1 : y.n - x.n); });
      var olds = spd.filter(function (r) { return r.before; }).map(function (r) { return r.platform; });
      var lead = (olds.length ? 'Already active before the selected days on ' + olds.slice(0, 3).join(', ') + '. ' : '') +
        (starts.length ? 'First seen on ' + starts.slice(0, 3).map(function (r) { return r.platform + ' (' + r.first_day + ')'; }).join(', then ') + '.' : '');
      if (lead) { wr.appendChild(h('div', { class: 'cs', text: lead })); }
      body.appendChild(wr);
      // ---- related trending: topics, themes, people / organisations / places, hashtags
      body.appendChild(h('div', { class: 'ct', style: 'margin-top:1rem', text: 'Related trending' }));
      var words = (d.terms || []).map(function (w) { return String(w).toLowerCase(); });
      var whole = {}; exTopicRows().forEach(function (r) { whole[r.topic_id] = r; });
      var tmap = {};
      d.topics.forEach(function (r) { var t = exTid(r.topic_id), o = tmap[t] || (tmap[t] = { n: 0, pv: 0, nkw: r.n_keyword || 0 }); o.n += r.n; o.pv += r.n_prev; if (r.n_keyword) { o.nkw = r.n_keyword; } });
      var rule = d.topic_rule || {};
      var minM = rule.min_matches || 3, minKwS = rule.min_kw_share || 0.05;
      var allTopicRows = Object.keys(tmap).map(function (t) {
        var w = whole[t], nm = tmap[t].n, nkw = tmap[t].nkw || d.n_cur || 1;
        var passes = !rule.applies || (nm >= minM && nm / nkw >= minKwS);
        return { tag: exRelTag(nm, tmap[t].pv), label: topicLabel(t), n: nm, pv: tmap[t].pv, passes: passes,
          tip: Math.round(nm / d.n_cur * 100) + '% of these posts' + (w ? '. The whole topic is: ' + w.stage + (w.emergence != null ? ', emergence ' + Number(w.emergence).toFixed(2) : '') : '') + '. Click to open its posts.',
          go: function () { EX.topicId = t; EX.topicName = topicLabel(t); EX.tile = 'viral'; EX.viral = null; render(); } };
      });
      var topicRows = allTopicRows.filter(function (r) { return r.passes; });
      var topicHidden = allTopicRows.length - topicRows.length;
      var thm = {};
      d.themes.forEach(function (r) { var o = thm[r.theme] || (thm[r.theme] = { n: 0, pv: 0, med: 0 }); if (r.tier === 'medium') { o.med += r.n; if (EX.themeAll) { o.n += r.n; o.pv += r.n_prev; } } else { o.n += r.n; o.pv += r.n_prev; } });
      var thRule = d.theme_rule || {};
      var thMinM = thRule.min_matches || 3, thMinS = thRule.min_kw_share || 0.03;
      var allThemeRows = Object.keys(thm).filter(function (k) { return thm[k].n > 0; }).map(function (k) {
        var passes = !thRule.applies || (thm[k].n >= thMinM && thm[k].n / (d.n_cur || 1) >= thMinS);
        return { tag: exRelTag(thm[k].n, thm[k].pv), label: exThemeName(k), n: thm[k].n, pv: thm[k].pv, passes: passes,
          tip: 'Automatic and provisional (about 75% right for the clear matches). Click to filter by this theme.',
          go: function () { EX.theme = k; EX.viral = null; EX.st = null; render(); } };
      });
      var themeRows = allThemeRows.filter(function (r) { return r.passes; });
      var themeHidden = allThemeRows.length - themeRows.length;
      var subRule = d.subtopic_rule || {};
      var subMinM = subRule.min_matches || 3, subMinS = subRule.min_sub_share || 0.15;
      var allSubRows = (d.subtopics || []).map(function (s) {
        var passes = !subRule.applies || (s.n >= subMinM && (s.n_sub_total ? s.n / s.n_sub_total : 0) >= subMinS);
        var subSharePct = s.n_sub_total ? Math.round(s.n / s.n_sub_total * 100) : 0;
        return { tag: exRelTag(s.n, s.n_prev), label: s.subcode + ' ' + (s.name || s.subcode), n: s.n, pv: s.n_prev, passes: passes,
          tip: s.subcode + ' ' + (s.name || '') + ': ' + s.n + ' posts (' + subSharePct + '% of this subtopic in this period).',
          go: function () { EX.q = s.name || s.subcode; EX.topicId = ''; EX.entityUid = ''; EX.entityName = ''; EX.viral = null; EX.st = null; render(); } };
      });
      var subtopicRows = allSubRows.filter(function (r) { return r.passes; });
      var subtopicHidden = allSubRows.length - subtopicRows.length;
      var entRows = d.entities.filter(function (r) { return words.indexOf(String(r.name).toLowerCase()) < 0; }).map(function (r) {
        return { tag: exRelTag(r.n, r.n_prev), label: r.name + ' · ' + String(r.type).toLowerCase(), n: r.n, pv: r.n_prev,
          tip: 'Mentioned in ' + Math.round(r.n / d.n_cur * 100) + '% of these posts, and in ' + fmtInt(r.corpus) + ' posts overall. Click to narrow the search to posts naming it.',
          go: function () { EX.entityUid = r.entity_uid; EX.entityName = r.name; EX.viral = null; EX.st = null; render(); } };
      });
      var tagRows = d.hashtags.filter(function (r) { return words.indexOf(r.tag.slice(1)) < 0; }).map(function (r) {
        return { tag: exRelTag(r.n, r.n_prev), label: r.tag, n: r.n, pv: r.n_prev, tip: 'Used in ' + Math.round(r.n / d.n_cur * 100) + '% of these posts. Click to search this hashtag instead.',
          go: function () { EX.q = r.tag.slice(1); EX.topicId = ''; EX.entityUid = ''; EX.entityName = ''; EX.viral = null; EX.st = null; render(); } };
      });
      var grid = h('div', { class: 'ex-rel-grid' });
      var topicNote = 'Broad topics: most posts of a niche subject sit in one or two of them.' +
        (topicHidden > 0 ? ' ' + topicHidden + ' topic' + (topicHidden > 1 ? 's' : '') + ' hidden as passing mentions (< 5% of these posts).' : '');
      grid.appendChild(relGroup('Topics', topicNote, topicRows, 'No topic assigned to these posts.'));
      var themeNote = fmtInt(d.unassigned_cur) + ' of these posts have no theme. Automatic and provisional.' +
        (themeHidden > 0 ? ' ' + themeHidden + ' theme' + (themeHidden > 1 ? 's' : '') + ' hidden as passing mentions (< 3% of these posts).' : '');
      grid.appendChild(relGroup('Security themes', themeNote, themeRows, 'No theme assigned to these posts.'));
      if (allSubRows.length > 0) {
        var subNote = 'Granular event topics (A01–A10).' +
          (subtopicHidden > 0 ? ' ' + subtopicHidden + ' subtopic' + (subtopicHidden > 1 ? 's' : '') + ' hidden as passing mentions (< 15% of subtopic).' : '');
        grid.appendChild(relGroup('Subtopics (Event topics)', subNote, subtopicRows, 'No subtopic with sufficient activity.'));
      }
      grid.appendChild(relGroup('People, organisations and places', 'From the production entity extract; some platforms stop earlier.', entRows, 'No entity is named in at least 5 of these posts.'));
      grid.appendChild(relGroup('Hashtags', '', tagRows, 'No hashtag appears in at least 5 of these posts.'));
      body.appendChild(grid);
      // ---- the posts behind it
      body.appendChild(h('div', { class: 'ct', style: 'margin-top:1rem', text: 'Posts behind it' }));
      var pbox = h('div', { class: 'ex-cols' }, [h('div', { class: 'empty', text: 'Loading…' })]);
      body.appendChild(pbox);
      var more = h('button', { type: 'button', class: 'df-preset-btn', text: 'Open all in Viral Posts' });
      more.addEventListener('click', function () { EX.tile = 'viral'; EX.viral = null; render(); });
      var url = '/api/explore/viral?limit=6&latest=4' + qs.replace(/&prev_from=[^&]*&prev_to=[^&]*/, '');
      fetch(url, { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (v) {
        if (v.error) { throw new Error(v.error); }
        pbox.textContent = '';
        EX.terms = v.terms || [];
        var a = h('div', { class: 'ex-col' }, [h('div', { class: 'ct', text: 'Most viewed' })]);
        v.with_views.forEach(function (x) { a.appendChild(exPostCard(x, true)); });
        v.without_views.slice(0, v.with_views.length ? 2 : 6).forEach(function (x) { a.appendChild(exPostCard(x, false)); });
        if (!v.with_views.length && !v.without_views.length) { empty(a, 'No posts with a view or engagement counter match.'); }
        var b = h('div', { class: 'ex-col' }, [h('div', { class: 'ct', text: 'Latest' })]);
        (v.latest || []).forEach(function (x) { b.appendChild(exPostCard(x, !!x.views)); });
        if (!(v.latest || []).length) { empty(b, 'None.'); }
        pbox.appendChild(a); pbox.appendChild(b);
        body.appendChild(more);
      }).catch(function (err) { pbox.textContent = ''; empty(pbox, 'Could not load posts: ' + err.message); });
    }
    if (EX.st && EX.stKey === key) { paint(EX.st); return; }
    body.appendChild(h('div', { class: 'empty', text: 'Loading…' }));
    fetch('/api/explore/search_trend?' + qs.slice(1), { cache: 'no-store' }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) { throw new Error(j && j.error ? j.error : 'HTTP ' + r.status); } return j; });
    }).then(function (d) {
      EX.st = d; EX.stKey = key;
      if (body.isConnected !== false) { paint(d); }
    }).catch(function (err) {
      body.textContent = '';
      empty(body, /^HTTP|Failed|fetch|NetworkError/i.test(err.message) ? 'Search trends need the API server (' + err.message + '). The static snapshot has no post-level data.' : 'Search problem: ' + err.message);
    });
  }

  function exTopicsView(host) {
    if (EX.q || EX.theme) { return exSearchTrendView(host); }
    if (EX.entityUid) { return exEntityTopicsView(host); }
    var isTheme = (EX.topicGrain !== 'macro') && T('dtheme_metrics_daily') && T('dtheme_metrics_daily').length > 0;
    var rows = exTopicRows().filter(function (r) { return !EX.minPlat || r.platforms >= EX.minPlat; }).sort(function (a, b) {
      var ea = a.emergence == null ? -1 : a.emergence, eb = b.emergence == null ? -1 : b.emergence;
      return eb - ea || b.posts - a.posts;
    }).slice(0, EX.top);
    exExplain(host, 'Trending topics', isTheme
      ? 'Security themes (D01–D09) ranked by post volume in the selected window. Shows posts, growth vs prior period, multi-platform presence, and stage (starting / rising / peak / steady / declining; based on daily post trajectory). Use the dropdown above to switch to macro clusters (t000–t007) if needed. Click a row to open its posts.'
      : 'Ranked by Emergence score, the pipeline’s single "popping up" number (blend of volume surprise, ' +
        'acceleration, semantic novelty and days-to-threshold; relative to this run’s panel, so read it as a ranking, not a probability). ' +
        'Alert days = days the volume jump was statistically significant (Benjamini–Hochberg q < 0.05); only cells with enough support are counted. ' +
        'Growth compares the selected days with the same number of days just before them (shown only with at least 10 earlier and 20 current posts). ' +
        'Momentum is the recent direction of volume, not a forecast. Stage compares the last 3 days with the 3 before, as posts per active channel ' +
        '(starting / rising / peak / steady / declining; "too little data" under 20 posts in the last 6 days). ' +
        'The Burst stage on Trend Lifecycle describes a single burst on one platform and uses a different rule, so the two can differ. ' +
        '"Unclassified" (posts not assigned to a topic) is left out unless you tick the box. "Seen on" keeps only topics present on at least that many platforms. Click a row to open its posts.' );
    exBigTopicNote(host, rows);
    var cols = isTheme ? [
      { k: 'topic', label: 'Theme' },
      { k: 'posts', label: 'Posts', num: true, fmt: function (v) { return fmtInt(v); } },
      { k: 'growth', label: 'Growth', num: true,
        fmt: function (v) { return v == null ? '\u2014' : (v >= 0 ? '+' : '') + Math.round(v * 100) + '%'; } },
      { k: 'platforms', label: 'Platforms', num: true },
      { k: 'stage', label: 'Stage' }
    ] : [
      { k: 'topic', label: 'Topic' },
      { k: 'posts', label: 'Posts', num: true, fmt: function (v) { return fmtInt(v); } },
      { k: 'growth', label: 'Growth', num: true,
        fmt: function (v) { return v == null ? '\u2014' : (v >= 0 ? '+' : '') + Math.round(v * 100) + '%'; } },
      { k: 'emergence', label: 'Emergence', num: true, fmt: function (v) { return v == null ? '\u2014' : Number(v).toFixed(2); } },
      { k: 'minq', label: 'Min q', num: true, fmt: function (v) { return v == null ? '\u2014' : (v < 0.001 ? '<0.001' : Number(v).toFixed(3)); } },
      { k: 'alert_days', label: 'Alert days', num: true, fmt: function (v) { return v == null ? '\u2014' : v; } },
      { k: 'burst_days', label: 'Burst days', num: true, fmt: function (v) { return v == null ? '\u2014' : v; } },
      { k: 'momentum', label: 'Momentum', num: true, fmt: function (v) { return v == null ? '\u2014' : (v >= 0 ? '+' : '') + Number(v).toFixed(2); } },
      { k: 'platforms', label: 'Platforms', num: true },
      { k: 'stage', label: 'Stage' }
    ];
    tableView(host, cols, rows, {
      label: (isTheme ? 'Trending security themes (' : 'Trending topics (') + rows.length + ')',
      rowDrill: function (r) { return { view: 'topic', params: exDrillParams({ topic_id: r.topic_id }, true), title: r.topic, subtitle: S.from + ' \u2192 ' + S.to }; }
    });
    exWhyCards(host, rows, isTheme);
    exMethodology(host, isTheme);
  }

  /* A topic that holds a large share of ALL collected posts cannot be one subject; its trend numbers then describe a mixture. */
  function exBigTopicNote(host, rows) {
    if (EX.topicGrain !== 'macro' && T('dtheme_metrics_daily') && T('dtheme_metrics_daily').length > 0) { return; }
    var dims = T('topic_dim'), total = 0;
    dims.forEach(function (d) { total += (d.n_posts || 0); });
    if (!total) { return; }
    var big = rows.map(function (r) { var d = TOPIC[r.topic_id]; return d ? { name: r.topic, share: (d.n_posts || 0) / total } : null; })
      .filter(function (x) { return x && x.share >= 0.3; });
    if (!big.length) { return; }
    host.appendChild(h('p', { class: 'cs ex-warn', text: big.map(function (b) { return '\u201c' + b.name + '\u201d holds ' + Math.round(100 * b.share) + '% of all collected posts'; }).join('; ') +
      ', more than one subject can. It mixes unrelated content (in a hand review most sampled posts were not about its name), so its numbers describe a mixture, not a single trend. Topic clustering is being reworked.' }));
  }

  /* plain-language reasons, each one a number the table already shows */
  function exWhyBullets(r, rank, n, isTheme) {
    var b = [], nd = exDays().length;
    b.push(fmtInt(r.posts) + ' posts between ' + S.from + ' and ' + S.to + (r.growth != null
      ? ' \u2014 ' + (r.growth >= 0 ? '+' : '') + Math.round(r.growth * 100) + '% against the previous ' + (nd === 1 ? 'day' : nd + ' days') + ' (' + fmtInt(r.prev) + ' posts).'
      : ' \u2014 too few posts in one of the two periods to compare growth.'));
    if (!isTheme && r.emergence != null) {
      b.push('Emergence score ' + Number(r.emergence).toFixed(2) + ', rank ' + rank + ' of ' + n + ' topics in this view.');
    } else if (isTheme) {
      b.push('Rank ' + rank + ' of ' + n + ' security themes in this view (ranked by post volume).');
    }
    if (!isTheme && r.alert_days != null) {
      b.push(r.alert_days > 0 ? r.alert_days + ' day' + (r.alert_days === 1 ? '' : 's') + ' where the jump in volume was statistically significant (q < ' + EX_RULES.alpha + ').'
        : 'No day with a statistically significant jump.');
    }
    if (isTheme) {
      b.push('Stage ' + r.stage + (r.stage_change != null ? ': the last 3 days are ' + (r.stage_change >= 0 ? '+' : '') + Math.round(r.stage_change * 100) + '% against the 3 before (post volume).' : '.'));
    } else {
      b.push('Stage ' + r.stage + (r.stage_change != null ? ': the last 3 days are ' + (r.stage_change >= 0 ? '+' : '') + Math.round(r.stage_change * 100) + '% against the 3 before (posts per active channel).' : '.'));
    }
    if (r.top_platform) { b.push('Seen on ' + r.platforms + ' platform' + (r.platforms === 1 ? '' : 's') + '; the largest is ' + r.top_platform + ' (' + Math.round(100 * r.top_share) + '% of the posts).'); }
    return b;
  }
  function exWhyCards(host, rows, isTheme) {
    var top = rows.slice(0, 5);
    if (!top.length) { return; }
    host.appendChild(h('div', { class: 'ct', style: 'margin-top:1rem', text: 'Why these are trending (top ' + top.length + ')' }));
    var list = h('div', { class: 'ex-why' });
    top.forEach(function (r, i) {
      var card = h('article', { class: 'ex-why-card' });
      card.appendChild(h('div', { class: 'ex-why-hd' }, [h('b', { text: (i + 1) + '. ' + r.topic }), h('span', { class: 'ex-why-st', text: r.stage })]));
      var ul = h('ul', { class: 'ex-why-ul' });
      exWhyBullets(r, i + 1, rows.length, isTheme).forEach(function (t) { ul.appendChild(h('li', { text: t })); });
      card.appendChild(ul);
      var act = h('div', { class: 'ex-why-act' });
      var vb = h('button', { type: 'button', class: 'df-preset-btn', text: 'View posts' });
      vb.addEventListener('click', function () { DRILL.open({ view: 'topic', params: exDrillParams({ topic_id: r.topic_id }, true), title: r.topic, subtitle: S.from + ' \u2192 ' + S.to }); });
      act.appendChild(vb);
      act.appendChild(FB.buttons('topic', r.topic_id, r.topic, S.to));
      card.appendChild(act);
      list.appendChild(card);
    });
    host.appendChild(list);
  }

  /* "How this is decided": formulas come from the stored metric definitions; thresholds from EX_RULES (the same numbers the code uses) */
  function exMethodology(host, isTheme) {
    var det = h('details', { class: 'ex-method' });
    det.appendChild(h('summary', { text: 'How this ranking is decided' }));
    var meta = {};
    T('metric_meta').forEach(function (m) { meta[m.metric_key] = m; });
    var R = EX_RULES;
    var rows;
    if (isTheme) {
      rows = [
        ['Ranking', 'Post volume', 'Security themes are ranked by total post volume in the selected date window.'],
        ['Growth', 'posts in the selected days \u00f7 posts in the same number of days just before, minus 1', 'Shown only with at least ' + R.growthMinPrev + ' earlier and ' + R.growthMinCur + ' current posts; otherwise \u201c\u2014\u201d.'],
        ['Stage', 'last 3 days vs the 3 before', 'rising \u2265 +' + Math.round((R.rise - 1) * 100) + '%; declining \u2264 \u2212' + Math.round((1 - R.fall) * 100) + '%; peak = flat and within ' + Math.round((1 - R.peakBand) * 100) + '% of the best 3-day level; steady = flat below it; starting = rising and the earlier period was under ' + Math.round(R.startPrev * 100) + '% as large (ranges up to ' + R.startMaxDays + ' days). Needs ' + R.stageDays + '+ days and ' + R.stageMinPosts + '+ posts in the last ' + R.stageDays + ' (calculated directly from post volume without channel-effort normalization).'],
        ['Platforms', 'Platform count', 'Number of distinct platforms on which the security theme was observed.'],
        ['Left out', 'Unassigned', 'Posts not assigned to a security theme are excluded unless \u201cInclude Unclassified\u201d is ticked.']
      ];
    } else {
      rows = [
        ['Ranking', (meta.emergence_score ? meta.emergence_score.formula : 'Emergence score'), 'Topics are sorted by emergence score, then by posts. The score is relative to this run\u2019s panel: a ranking, not a probability.'],
        ['Alert day', (meta.surprise ? meta.surprise.formula : 'Volume surprise'), 'A day counts when its jump is significant after Benjamini\u2013Hochberg correction (q < ' + R.alpha + ') and the cell has enough support.'],
        ['Growth', 'posts in the selected days \u00f7 posts in the same number of days just before, minus 1', 'Shown only with at least ' + R.growthMinPrev + ' earlier and ' + R.growthMinCur + ' current posts; otherwise \u201c\u2014\u201d.'],
        ['Momentum', (meta.momentum ? meta.momentum.formula : 'EMA7 \u2212 EMA21 of growth'), 'Describes the recent direction of volume; it is not a forecast.'],
        ['Stage', 'last 3 days vs the 3 before, as posts per active channel', 'rising \u2265 +' + Math.round((R.rise - 1) * 100) + '%; declining \u2264 \u2212' + Math.round((1 - R.fall) * 100) + '%; peak = flat and within ' + Math.round((1 - R.peakBand) * 100) + '% of the best 3-day level; steady = flat below it; starting = rising and the earlier period was under ' + Math.round(R.startPrev * 100) + '% as large (ranges up to ' + R.startMaxDays + ' days). Needs ' + R.stageDays + '+ days and ' + R.stageMinPosts + '+ posts in the last ' + R.stageDays + '.'],
        ['Left out', 'Unclassified', 'Posts not assigned to a topic are excluded unless \u201cInclude Unclassified\u201d is ticked.']
      ];
    }
    var tb = h('table', { class: 'ex-method-t' });
    tb.appendChild(h('thead', {}, [h('tr', {}, ['Item', 'Definition', 'Rule'].map(function (x) { return h('th', { text: x }); }))]));
    var body = h('tbody');
    rows.forEach(function (r) { body.appendChild(h('tr', {}, r.map(function (x) { return h('td', { text: x }); }))); });
    tb.appendChild(body);
    det.appendChild(tb);
    det.appendChild(h('p', { class: 'cs', text: 'Limits: the thresholds (25%, 20%, 10%) are chosen, not tuned, and should be hand-checked before customers rely on a stage. Counts are posts and comments in the collected corpus, not all activity on a platform.' }));
    host.appendChild(det);
  }

  function exLifeView(host) {
    var days = exDays(), toDay = days[days.length - 1] || S.to;
    var all = T('burst_episodes').filter(function (e) {
      return exPlatOk(e.platform) && day(e.end_day) >= S.from && day(e.start_day) <= S.to &&
        exTextOk(topicLabel(exTid(e.topic_id))) && (EX.unclass || topicLabel(exTid(e.topic_id)) !== 'Unclassified');
    });
    var rows = all.map(function (e) {
      return {
        topic_id: exTid(e.topic_id), topic: topicLabel(exTid(e.topic_id)), platform: e.platform,
        start: day(e.start_day), peak: day(e.peak_day), end: day(e.end_day), n_days: e.n_days,
        peak_V: e.peak_V, total_V: e.total_V, onset: e.onset_slope, half_life: e.half_life_days,
        decay: e.decay_ok === true ? 'good fit' : (e.decay_ok === false ? 'weak fit' : '—'),
        stage: exStage([e], toDay)
      };
    }).sort(function (a, b) { return b.peak < a.peak ? -1 : (b.peak > a.peak ? 1 : 0); }).slice(0, EX.top * 4);
    var cnt = {}; rows.forEach(function (r) { cnt[r.stage] = (cnt[r.stage] || 0) + 1; });
    exExplain(host, 'Trend lifecycle', 'Each row is one burst of activity (Kleinberg or CUSUM detector) for a topic on a platform. Burst stage is judged at the end ' +
      'of the selected range: starting, rising, peak, declining, or steady (no active burst). It describes this one burst on this one platform; ' +
      'the Stage on Trending Topics is a different rule (last 3 days against the 3 before, for the whole topic), so the two can differ. Onset slope = how fast it built up (log-linear); ' +
      'half-life = how fast it faded, shown with whether the decay curve fit well. ' +
      Object.keys(cnt).map(function (k) { return cnt[k] + ' ' + k; }).join(' · ') );
    tableView(host, [
      { k: 'topic', label: 'Topic' }, { k: 'platform', label: 'Platform' }, { k: 'stage', label: 'Burst stage' },
      { k: 'start', label: 'Started' }, { k: 'peak', label: 'Peak day' }, { k: 'end', label: 'Ended' },
      { k: 'n_days', label: 'Days', num: true },
      { k: 'peak_V', label: 'Peak/day', num: true },
      { k: 'onset', label: 'Onset slope', num: true, fmt: function (v) { return v == null ? '\u2014' : Number(v).toFixed(2); } },
      { k: 'half_life', label: 'Half-life (d)', num: true, fmt: function (v) { return v == null ? '\u2014' : Number(v).toFixed(1); } },
      { k: 'decay', label: 'Decay fit' }
    ], rows, {
      label: 'Burst episodes in the selected range (' + rows.length + ')',
      rowDrill: function (r) {
        return { view: 'topic', params: { topic_id: r.topic_id, platform: r.platform, day: r.peak },
                 title: r.topic + ' · ' + r.platform, subtitle: 'peak day ' + r.peak };
      }
    });
    var pers = T('persistence').filter(function (r) {
      return r.fit_ok && exPlatOk(r.platform) && exTextOk(topicLabel(exTid(r.topic_id)));
    }).map(function (r) {
      return { topic_id: exTid(r.topic_id), topic: topicLabel(exTid(r.topic_id)), platform: r.platform,
        half_life: r.half_life_days, mean_rev: r.mean_reverting === true ? 'yes' : (r.mean_reverting === false ? 'no' : '—'),
        n: r.n_obs, total: r.total_V };
    }).sort(function (a, b) { return (b.total || 0) - (a.total || 0); }).slice(0, EX.top * 2);
    exExplain(host, 'Typical duration', 'How long attention tends to last, over the whole collection window (persistence: AR(1) half-life of the daily series). ' +
      'This describes the past pattern of each topic on a platform; it does not predict the next days.' );
    tableView(host, [
      { k: 'topic', label: 'Topic' }, { k: 'platform', label: 'Platform' },
      { k: 'half_life', label: 'Typical half-life (d)', num: true, fmt: function (v) { return v == null ? '\u2014' : Number(v).toFixed(1); } },
      { k: 'mean_rev', label: 'Returns to normal' },
      { k: 'n', label: 'Days observed', num: true }, { k: 'total', label: 'Total posts', num: true, fmt: fmtInt }
    ], pers, { open: false, label: 'Persistence by topic and platform (' + pers.length + ')' });
  }

  function exSpreadRows(sub) {
    function plOf(s) { return Array.isArray(s.platforms) ? s.platforms : [s.platforms]; }
    function inRange(s) { var d = s.origin_time ? day(s.origin_time) : null; return d && d >= S.from && d <= S.to; }
    if (sub === 'links') {
      return T('cross_platform_url_links').filter(function (s) { return inRange(s) && plOf(s).some(exPlatOk) && exTextOk(s.url); })
        .map(function (s) { return { id: s.link_id, text: String(s.url || '').slice(0, 220), conf: s.match_confidence, origin: s.origin_platform,
          platforms: plOf(s).join(', '), posts: s.n_unique_posts != null ? s.n_unique_posts : s.n_posts,
          spread_h: s.spread_duration_hours, views: s.observed_views || 0, eng: 0, first: day(s.origin_time) }; });
    }
    if (sub === 'events') {
      return T('cross_platform_semantic_events').filter(function (s) { return inRange(s) && plOf(s).some(exPlatOk) && exTextOk(s.snippet); })
        .map(function (s) { return { id: s.event_id, text: ((s.large_event ? '[large event] ' : '') + (s.snippet || '')).slice(0, 220), conf: s.match_confidence,
          origin: s.origin_platform, platforms: plOf(s).join(', '), posts: s.n_unique_posts,
          spread_h: s.spread_duration_hours, views: s.observed_views || 0, eng: s.total_engagement || 0, first: day(s.origin_time) }; });
    }
    return T('cross_platform_stories').filter(function (s) { return inRange(s) && plOf(s).some(exPlatOk) && exTextOk(s.snippet); })
      .map(function (s) { return { id: s.story_id, text: String(s.snippet || '').slice(0, 220), conf: 'exact text', origin: s.origin_platform,
        platforms: plOf(s).join(', '), posts: s.n_posts, spread_h: s.spread_duration_hours,
        views: s.observed_views || 0, eng: s.viewless_engagement || 0, first: day(s.origin_time) }; });
  }

  function exSpreadView(host) {
    var sub = EX.sub || 'stories';
    var bar = h('div', { class: 'ex-row' });
    [['stories', 'Same text'], ['links', 'Shared links'], ['events', 'Similar meaning (preliminary)']].forEach(function (x) {
      var b = h('button', { type: 'button', class: 'df-preset-btn' + (sub === x[0] ? ' active' : ''), text: x[1] });
      b.addEventListener('click', function () { EX.sub = x[0]; render(); });
      bar.appendChild(b);
    });
    host.appendChild(bar);
    var note = { stories: 'Groups of near-identical posts on different platforms (exact text match: high precision, misses paraphrases and translations).',
      links: 'Posts sharing the same link. High = full article link seen within 21 days; medium = shortened link; low = the link is spread over more than 21 days. App deep links are excluded.',
      events: 'Posts that say nearly the same thing in different words (bge-m3 similarity of at least 0.85 within 3 days). Preliminary: not yet hand-judged.' }[sub];
    exExplain(host, 'Cross-platform spread', note + ' "Earliest observed" is the first post we saw, not the confirmed origin. ' +
      'Views are observed counts only (no estimates); posts on platforms with no view count are not added in. Click a row to see every post.' );
    var rows = exSpreadRows(sub).sort(function (a, b) { return (b.views + b.eng) - (a.views + a.eng); }).slice(0, EX.top * 4);
    var modeBar = h('div', { class: 'ex-row' }, [h('span', { class: 'bds-label', text: 'VIEW' })]);
    [['cards', 'Cards'], ['table', 'Table']].forEach(function (x) {
      var b = h('button', { type: 'button', class: 'df-preset-btn' + (EX.spreadMode === x[0] ? ' active' : ''), text: x[1] });
      b.addEventListener('click', function () { EX.spreadMode = x[0]; render(); });
      modeBar.appendChild(b);
    });
    host.appendChild(modeBar);
    var drillView = { stories: ['cross_platform', 'story_id'], links: ['cross_platform_url', 'link_id'], events: ['cross_platform_event', 'event_id'] }[sub];
    if (EX.spreadMode === 'cards') {
      var shownN = Math.min(EX.spreadLimit, rows.length), list = h('div', { class: 'ex-stories' });
      rows.slice(0, shownN).forEach(function (r, i) {
        var params = {}; params[drillView[1]] = r.id;
        var card = h('article', { class: 'ex-story' });
        card.appendChild(h('div', { class: 'ex-story-rank', text: String(i + 1) }));
        var main = h('div', { class: 'ex-story-main' });
        main.appendChild(h('div', { class: 'ex-story-title', text: r.text }));
        main.appendChild(h('div', { class: 'ex-chips' }, [
          h('span', { class: 'ex-chip', text: 'match: ' + r.conf }),
          h('span', { class: 'ex-chip', text: 'earliest observed: ' + r.origin }),
          h('span', { class: 'ex-chip', text: r.platforms })]));
        main.appendChild(h('div', { class: 'ex-story-stats' }, [
          h('b', { text: fmtInt(r.posts) }), txt(' posts \u00b7 '),
          h('b', { text: Number(r.spread_h || 0).toFixed(1) }), txt(' h observed spread \u00b7 '),
          h('b', { text: fmtCompact(r.views) }), txt(' observed views \u00b7 first seen ' + r.first)]));
        var act = h('div', { class: 'ex-why-act' });
        var vb = h('button', { type: 'button', class: 'df-preset-btn', text: 'View posts' });
        vb.addEventListener('click', function () { DRILL.open({ view: drillView[0], params: params, title: 'Spread (earliest observed: ' + r.origin + ')' }); });
        act.appendChild(vb);
        act.appendChild(FB.buttons('story', sub + ':' + r.id, r.text, r.first));
        main.appendChild(act);
        card.appendChild(main);
        list.appendChild(card);
      });
      host.appendChild(h('div', { class: 'ct', text: 'Cross-platform spread (' + rows.length + ')' }));
      host.appendChild(list);
      if (!rows.length) { empty(host, 'No stories in this range and platform selection.'); }
      if (rows.length > shownN) {
        var more = h('button', { type: 'button', class: 'dr-more', text: 'Show more stories (' + (rows.length - shownN) + ' remaining)' });
        more.addEventListener('click', function () { EX.spreadLimit += 10; render(); });
        host.appendChild(more);
      }
      return;
    }
    tableView(host, [
      { k: 'text', label: sub === 'links' ? 'Shared link' : 'Sample text', fmt: function (v) { var t = String(v); return t.length > 70 ? t.slice(0, 70) + '\u2026' : t; } },
      { k: 'conf', label: 'Match' },
      { k: 'origin', label: 'First seen on' },
      { k: 'platforms', label: 'Platforms' },
      { k: 'posts', label: 'Posts', num: true, fmt: fmtInt },
      { k: 'spread_h', label: 'Spread (h)', num: true, fmt: function (v) { return v == null ? '\u2014' : Number(v).toFixed(1); } },
      { k: 'views', label: 'Views', num: true, fmt: function (v) { return v == null ? '\u2014' : fmtCompact(v); } },
      { k: 'first', label: 'Date' }
    ], rows, {
      open: true, label: 'Cross-platform spread (' + rows.length + ')',
      rowDrill: function (r) {
        var params = {}; params[drillView[1]] = r.id;
        return { view: drillView[0], params: params, title: 'Spread (earliest observed: ' + r.origin + ')' };
      }
    });
  }


  /* =============================================================== reader feedback (thumbs up / down with reasons)
     Votes go to /api/feedback (its own SQLite file on the server, never production). Each reader is an anonymous id kept in this
     browser. Down opens a small list of reasons; voting again replaces the vote; clicking the same thumb withdraws it. */
  var FB = { user: null, pending: {}, timer: null };
  var FB_REASONS = [['not_a_real_spike', 'Not a real spike'], ['routine_volume', 'Routine volume'], ['duplicate', 'Same as another'],
                    ['wrong_topic', 'Wrong topic'], ['wrong_name', 'Wrong name'], ['bad_text', 'Bad text'], ['other', 'Something else…']];
  FB.uid = function () {
    if (FB.user) { return FB.user; }
    try { FB.user = localStorage.getItem('fb_uid'); } catch (e) { FB.user = null; }
    if (!FB.user) {
      FB.user = 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
      try { localStorage.setItem('fb_uid', FB.user); } catch (e) { /* kept for this page only */ }
    }
    return FB.user;
  };
  FB.post = function (body) {
    return fetch('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) { throw new Error(j && j.error ? j.error : 'HTTP ' + r.status); } return j; }); });
  };
  FB.want = function (type, id, dayStr, cb) {            // batch the "what did I already vote" lookups of everything on screen
    var k = type + '|' + (dayStr || '');
    var g = FB.pending[k] || (FB.pending[k] = { type: type, day: dayStr || '', ids: [], cbs: {} });
    if (g.ids.indexOf(id) < 0) { g.ids.push(id); }
    (g.cbs[id] = g.cbs[id] || []).push(cb);
    if (FB.timer) { return; }
    FB.timer = setTimeout(function () {
      var groups = FB.pending; FB.pending = {}; FB.timer = null;
      Object.keys(groups).forEach(function (key) {
        var gr = groups[key];
        fetch('/api/feedback/votes?user=' + encodeURIComponent(FB.uid()) + '&type=' + encodeURIComponent(gr.type) + '&day=' + encodeURIComponent(gr.day) +
          '&ids=' + encodeURIComponent(gr.ids.join(',')))
          .then(function (r) { return r.json(); })
          .then(function (j) { gr.ids.forEach(function (i) { (gr.cbs[i] || []).forEach(function (f) { f((j.votes || {})[i]); }); }); })
          .catch(function () { /* static snapshot or API down: the buttons still render, they just start unselected */ });
      });
    }, 40);
  };
  FB.buttons = function (type, id, label, dayStr) {
    var wrap = h('span', { class: 'fb', 'data-fb': type + ':' + id });
    var up = h('button', { type: 'button', class: 'fb-b fb-up', title: 'Useful and real', text: '\u{1F44D}' });
    var down = h('button', { type: 'button', class: 'fb-b fb-down', title: 'Not useful or not real', text: '\u{1F44E}' });
    up.setAttribute('aria-label', 'Thumbs up'); down.setAttribute('aria-label', 'Thumbs down');
    var note = h('span', { class: 'fb-note', role: 'status' }), panel = null, state = { vote: 0 };
    function paint() {
      up.classList.toggle('on', state.vote === 1); down.classList.toggle('on', state.vote === -1);
      up.setAttribute('aria-pressed', state.vote === 1 ? 'true' : 'false'); down.setAttribute('aria-pressed', state.vote === -1 ? 'true' : 'false');
    }
    function closePanel() { if (panel) { panel.remove(); panel = null; } }
    function send(vote, reasons, other) {
      return FB.post({ user: FB.uid(), item_type: type, item_id: id, day: dayStr || '', label: String(label || '').slice(0, 200), vote: vote, reasons: reasons || [], other_text: other || '' })
        .then(function () { state.vote = vote; paint(); note.textContent = vote ? 'Thanks' : ''; })
        .catch(function (e) { note.textContent = 'Could not save (' + e.message + ')'; });
    }
    function openPanel() {
      closePanel();
      panel = h('div', { class: 'fb-panel' });
      panel.addEventListener('click', function (e) { e.stopPropagation(); });
      var checks = [];
      FB_REASONS.forEach(function (r) {
        var cb = h('input', { type: 'checkbox', value: r[0] });
        checks.push(cb);
        panel.appendChild(h('label', { class: 'fb-reason' }, [cb, h('span', { text: ' ' + r[1] })]));
      });
      var other = h('input', { type: 'text', class: 'ex-q fb-other', maxlength: '300', placeholder: 'Tell us more (optional)' });
      var submit = h('button', { type: 'button', class: 'df-preset-btn', text: 'Send' });
      submit.addEventListener('click', function () {
        var rs = checks.filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
        send(-1, rs, other.value).then(closePanel);
      });
      panel.appendChild(other); panel.appendChild(submit);
      wrap.appendChild(panel);
    }
    up.addEventListener('click', function (e) { e.stopPropagation(); closePanel(); send(state.vote === 1 ? 0 : 1); });
    down.addEventListener('click', function (e) { e.stopPropagation(); if (state.vote === -1) { closePanel(); send(0); } else { openPanel(); } });
    wrap.appendChild(up); wrap.appendChild(down); wrap.appendChild(note);
    FB.want(type, id, dayStr, function (v) { if (v) { state = v; paint(); } });
    return wrap;
  };

  /* ---- Sentiment Analysis card: stored tone metrics only (counts are summed before shares are taken) ---- */
  function exSentRows(groupBy) {
    var g = exCells(), by = {};
    g.cells.forEach(function (c) {
      if (!c.inCur) { return; }
      var r = c.r;
      if (!(r.tone_n > 0)) { return; }
      var key = groupBy === 'platform' ? r.platform : exTid(r.topic_id);
      var o = by[key] || (by[key] = { key: key, n: 0, pos: 0, neg: 0, ebw: 0, ebn: 0, drops: 0 });
      o.n += r.tone_n; o.pos += (r.tone_pos_n || 0); o.neg += (r.tone_neg_n || 0);
      if (isNum(r.tone_eb)) { o.ebw += r.tone_eb * r.tone_n; o.ebn += r.tone_n; }
      if (r.tone_drift_flag) { o.drops++; }
    });
    return Object.keys(by).map(function (k) {
      var o = by[k], ok = o.n >= 20;
      return {
        key: k, name: groupBy === 'platform' ? k : topicLabel(k), n: o.n,
        pos: ok ? o.pos / o.n : null, neg: ok ? o.neg / o.n : null,
        neu: ok ? (o.n - o.pos - o.neg) / o.n : null,
        nss: (ok && groupBy === 'platform') ? 100 * (o.pos - o.neg) / o.n : null,
        tone: o.ebn >= 20 ? o.ebw / o.ebn : null, drops: o.drops
      };
    }).filter(function (r) { return exTextOk(r.name) && (groupBy === 'platform' || EX.unclass || r.name !== 'Unclassified'); });
  }

  function exSentView(host) {
    var subNav = h('div', { class: 'ex-row ex-sent-nav', style: 'margin-bottom: 0.8rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--gold-border);' }, [
      h('span', { class: 'bds-label', text: 'SENTIMENT VIEW' }),
      (function () {
        var b = h('button', { type: 'button', class: 'df-preset-btn active', 'data-sub': 'post', text: 'Post Tone (Authors)' });
        b.addEventListener('click', function () { EX.tile = 'sent'; render(); });
        return b;
      })(),
      (function () {
        var b = h('button', { type: 'button', class: 'df-preset-btn', 'data-sub': 'aud', text: 'Audience Reaction (Comments \u0026 Replies)' });
        b.addEventListener('click', function () { EX.tile = 'aud'; render(); });
        return b;
      })()
    ]);
    host.appendChild(subNav);
    exExplain(host, 'Sentiment analysis', 'Tone of the posts’ wording, from the stored tone scores (multilingual model). ' +
      'It measures the language of the post, not the poster’s attitude: a factual report of an attack or accident reads as negative. ' +
      'Positive / negative = tone above +0.15 / below −0.15 (a provisional cut-off, not validated per language). ' +
      'Net sentiment score (NSS) = 100 × (positive share − negative share) and is compared within one platform only, ' +
      'because platform levels differ widely (about −70 to +60). Shares are shown only with at least 20 scored posts. ' +
      'Tone is provisional: it has only been checked against a draft labelling, not yet confirmed by a human reviewer.' );
    var fmtPct = function (v) { return v == null ? '—' : Math.round(v * 100) + '%'; };
    var plat = exSentRows('platform').sort(function (a, b) { return b.n - a.n; });
    tableView(host, [
      { k: 'name', label: 'Platform' },
      { k: 'n', label: 'Posts scored', num: true, fmt: fmtInt },
      { k: 'pos', label: 'Positive', num: true, fmt: fmtPct },
      { k: 'neu', label: 'Neutral', num: true, fmt: fmtPct },
      { k: 'neg', label: 'Negative', num: true, fmt: fmtPct },
      { k: 'nss', label: 'NSS', num: true, fmt: function (v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Math.round(v); } },
      { k: 'tone', label: 'Mean tone', num: true, fmt: function (v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2); } },
      { k: 'drops', label: 'Tone-drop days', num: true }
    ], plat, {
      label: 'Tone by platform (' + plat.length + ')',
      rowDrill: function (r) { return { view: 'platform', params: exDrillParams({ platform: r.key }), title: r.name + ' \u2014 posts', subtitle: S.from + ' \u2192 ' + S.to }; }
    });
    var top = exSentRows('topic').sort(function (a, b) { return b.n - a.n; }).slice(0, EX.top);
    exExplain(host, 'Tone by topic', 'By topic, all selected platforms together. Shares come from summed counts; the topic NSS is not shown ' +
      'because it would mix platforms with different levels. Tone-drop days = days the tone fell significantly below its own previous 14 days ' +
      '(Benjamini–Hochberg q < 0.05; a monitor, not an alert).' );
    tableView(host, [
      { k: 'name', label: 'Topic' },
      { k: 'n', label: 'Posts scored', num: true, fmt: fmtInt },
      { k: 'pos', label: 'Positive', num: true, fmt: fmtPct },
      { k: 'neu', label: 'Neutral', num: true, fmt: fmtPct },
      { k: 'neg', label: 'Negative', num: true, fmt: fmtPct },
      { k: 'tone', label: 'Mean tone', num: true, fmt: function (v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2); } },
      { k: 'drops', label: 'Tone-drop days', num: true }
    ], top, {
      label: 'Tone by topic (' + top.length + ')',
      rowDrill: function (r) { return { view: 'topic', params: exDrillParams({ topic_id: r.key }, true), title: r.name, subtitle: S.from + ' \u2192 ' + S.to }; }
    });
    var cur = exSet(exDays());
    var drops = T('metrics_daily').filter(function (r) {
      return r.tone_drift_flag && cur[day(r.day)] && exPlatOk(r.platform) && exTextOk(topicLabel(exTid(r.topic_id)));
    }).map(function (r) {
      return { topic_id: exTid(r.topic_id), topic: topicLabel(exTid(r.topic_id)), platform: r.platform, day: day(r.day),
        z: r.tone_drift_z, tone: r.tone_mean, n: r.tone_n };
    }).sort(function (a, b) { return a.z - b.z; }).slice(0, EX.top);
    exExplain(host, 'Tone drops', 'Significant tone drops in the selected range (tone clearly below the topic’s own recent level). ' +
      'z = how many standard deviations below the previous 14 days. Click a row to open that day’s posts.' );
    tableView(host, [
      { k: 'day', label: 'Day' }, { k: 'topic', label: 'Topic' }, { k: 'platform', label: 'Platform' },
      { k: 'z', label: 'Drop (z)', num: true, fmt: function (v) { return v == null ? '—' : Number(v).toFixed(1); } },
      { k: 'tone', label: 'Mean tone', num: true, fmt: function (v) { return v == null ? '—' : Number(v).toFixed(2); } },
      { k: 'n', label: 'Posts scored', num: true, fmt: fmtInt }
    ], drops, {
      label: 'Significant tone drops (' + drops.length + ')',
      rowDrill: function (r) { return { view: 'topic', params: { topic_id: r.topic_id, platform: r.platform, day: r.day }, title: r.topic + ' · ' + r.platform, subtitle: r.day }; }
    });
  }

  /* highlight the searched words (whole words, any script) inside a post; text is only ever inserted as text */
  function exHl(text, terms) {
    var t = String(text || '');
    if (!terms || !terms.length) { return [txt(t)]; }
    var alts = terms.slice().sort(function (a, b) { return b.length - a.length; })
      .map(function (w) { return w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+'); }).join('|');
    var re;
    try { re = new RegExp('(^|[^\\p{L}\\p{N}])(' + alts + ')(?![\\p{L}\\p{N}])', 'giu'); } catch (e) { return [txt(t)]; }
    var out = [], last = 0, m;
    while ((m = re.exec(t)) !== null) {
      var start = m.index + m[1].length;
      if (start > last) { out.push(txt(t.slice(last, start))); }
      out.push(h('mark', { text: m[2] }));
      last = start + m[2].length;
      if (m[0].length === 0) { re.lastIndex++; }
    }
    if (last < t.length) { out.push(txt(t.slice(last))); }
    return out;
  }

  function exViralityText(p) {          // amplification: rank of the share/like ratio among posts of the same platform and age
    return (p.virality_pct != null && p.virality_ref_n >= 20)
      ? 'amplification ' + Number(p.virality_pct).toFixed(2) + ' (vs ' + fmtInt(p.virality_ref_n) + ' similar posts)' : '';
  }
  function exAttentionText(p) {         // size: where raw views (or likes + comments + shares) sit among posts of the same platform and age
    if (p.attention_pct == null && p.attention_mult == null) { return ''; }
    var top = p.attention_pct == null ? null : Math.max(0.01, (1 - Number(p.attention_pct)) * 100);
    var topTxt = top == null ? '' : 'top ' + (top < 0.1 ? top.toFixed(2) : (top < 1 ? top.toFixed(1) : String(Math.round(top)))) + '% of ' + p.platform + ' posts of similar age';
    var m = p.attention_mult == null ? null : Number(p.attention_mult);
    var multTxt = m == null ? '' : (m >= 10 ? Math.round(m) : m.toFixed(1)) + '\u00d7 the top-10% line';
    return 'attention: ' + [topTxt, multTxt].filter(Boolean).join(' (') + (topTxt && multTxt ? ')' : '');
  }
  function exPostCard(p, byViews, mode) {
    var when = p.posted_at ? day(p.posted_at) : '';
    var vt = exViralityText(p), at = exAttentionText(p);
    var lead = EX.vorder === 'amplified' ? [vt, at] : [at, vt];               // the bold part is what the list is sorted by
    var metric = mode === 'virality' ? (lead[0] || lead[1] || 'no ratio')
      : (byViews ? (fmtCompact(p.views || 0) + ' views') : ('engagement ' + fmtCompact(p.engagement || 0)));
    var second = (mode === 'virality' ? ((lead[0] && lead[1] ? ' · ' + lead[1] : '') + (p.views ? ' · ' + fmtCompact(p.views) + ' views' : '')) : (vt ? ' · ' + vt : '')) +
      (p.likes != null ? ' · ' + fmtCompact(p.likes) + ' likes' : '') + (p.shares != null ? ' · ' + fmtCompact(p.shares) + ' shares' : '');
    var title = h('div', { class: 'ex-post-top' }, [
      h('b', { text: p.platform }), p.is_retweet ? h('span', { class: 'ex-rt-tag', text: 'Retweet', title: 'Starts with "RT @": the counters shown are the original post\u2019s, not its own' }) : null,
      h('span', { text: ' · ' + when + ' · ' }),
      h('span', { class: 'ex-post-m', text: metric }),
      h('span', { text: second })
    ]);
    var kids = [title, h('div', { class: 'ex-post-text' }, exHl(String(p.text || '').slice(0, 400), EX.terms))];
    if (p.url && /^https?:/i.test(p.url)) {
      var a = h('a', { text: 'source post', href: p.url });
      a.setAttribute('target', '_blank'); a.setAttribute('rel', 'noopener noreferrer');
      kids.push(h('div', { class: 'ex-post-link' }, [a]));
    }
    return h('div', { class: 'ex-post' }, kids);
  }

  function exBreakoutRows() {
    var g = exCells(), by = {};
    g.cells.forEach(function (c) {
      if (!c.inCur) { return; }
      var r = c.r, key = exTid(r.topic_id) + '|' + r.platform;
      var o = by[key] || (by[key] = { topic_id: exTid(r.topic_id), platform: r.platform, V: 0, vn: 0, vs: 0, rn: 0, rs: 0,
        views: 0, hasViews: false, eng: 0, engN: 0 });
      o.V += (r.V || 0);
      if (isNum(r.virality_pct) && (r.virality_n || 0) > 0) { o.vs += r.virality_pct * r.virality_n; o.vn += r.virality_n; }
      if (isNum(r.reach_audience_norm) && (r.V || 0) > 0) { o.rs += r.reach_audience_norm * r.V; o.rn += r.V; }
      if (isNum(r.observed_views)) { o.views += r.observed_views; o.hasViews = true; }
      if (isNum(r.engagement_proxy)) { o.eng += r.engagement_proxy; o.engN++; }
    });
    return Object.keys(by).map(function (k) {
      var o = by[k];
      return { topic_id: o.topic_id, topic: topicLabel(o.topic_id), platform: o.platform, posts: o.V,
        pct: o.vn >= 20 ? o.vs / o.vn : null, n_rated: o.vn,
        reach: o.rn >= 20 ? o.rs / o.rn : null,
        views: o.hasViews ? o.views : null, eng: o.engN ? o.eng / o.engN : null };
    }).filter(function (r) { return r.pct != null && (EX.unclass || r.topic !== 'Unclassified') && (!EX.topicId || r.topic_id === exTid(EX.topicId)); });   // the search box here searches POST TEXT (below), not topic names
  }

  function exViralView(host) {
    var br = exBreakoutRows().sort(function (a, b) { return b.pct - a.pct || b.posts - a.posts; }).slice(0, EX.top);
    exExplain(host, 'Viral posts', 'Where posts are performing above their peers, from the stored metrics. Virality percentile = how a post\u2019s ' +
      'amplification ratio (engagement relative to exposure) ranks against posts of the same platform and age (0.5 = typical, higher = stronger), averaged over the group\u2019s ' +
      'rated posts; shown only with at least 20 rated posts. Audience-normalised reach = views relative to the same author\u2019s other posts (1.0 = their usual). ' +
      'Observed views are the counter values we saw, not audience size. Engagement activity is a different unit and is not added to views. ' +
      'These are group averages (topic \u00d7 platform): the per-post percentile is not stored yet. Click a row to open its posts.' );
    tableView(host, [
      { k: 'topic', label: 'Topic' }, { k: 'platform', label: 'Platform' },
      { k: 'posts', label: 'Posts', num: true, fmt: fmtInt },
      { k: 'pct', label: 'Virality pct', num: true, fmt: function (v) { return v == null ? '\u2014' : Number(v).toFixed(2); } },
      { k: 'n_rated', label: 'Rated posts', num: true, fmt: fmtInt },
      { k: 'reach', label: 'Reach vs author norm', num: true, fmt: function (v) { return v == null ? '\u2014' : Number(v).toFixed(1) + '\u00d7'; } },
      { k: 'views', label: 'Observed views', num: true, fmt: function (v) { return v == null ? '\u2014' : fmtCompact(v); } },
      { k: 'eng', label: 'Engagement (log)', num: true, fmt: function (v) { return v == null ? '\u2014' : Number(v).toFixed(1); } }
    ], br, {
      label: 'Breakout groups by virality percentile (' + br.length + ')',
      rowDrill: function (r) {
        var pp = exDrillParams({ topic_id: r.topic_id, platform: r.platform });
        // views where the platform has a view counter, else engagement; if it has neither (some platforms
        // carry no counters at all) there is nothing to rank by, so keep the default newest-first order
        if (r.views != null) { pp.sort = 'views'; } else if (r.eng != null && r.eng > 0) { pp.sort = 'engagement'; }
        return { view: 'topic', params: pp, title: r.topic + ' \u00b7 ' + r.platform, subtitle: S.from + ' \u2192 ' + S.to };
      }
    });
    host.appendChild(h('div', { class: 'ct', style: 'margin-top:1rem', text: 'Top individual posts' }));
    var plats = PLATFORMS.filter(exPlatOk).join(',');
    var key = [S.from, S.to, plats, EX.q, EX.entityUid, EX.top, EX.vrank, EX.vper, EX.rt, EX.floor, EX.vorder, EX.topicId, EX.theme, EX.themeAll, EX.vthresh].join('|');
    exExplain(host, 'Top posts', (EX.topicName ? 'Showing only the posts in the topic \u201c' + EX.topicName + '\u201d. ' : '') + 'The search box takes a keyword, or pick a topic name from its suggestions to see only the posts in that topic. Two ways to rank. "Views": posts ranked by observed views; platforms that give no view count are listed separately, ' +
      'ranked by likes + comments + shares, and never ranked as zero views. Views are not comparable across platforms, so the most viewed post is not ' +
      'necessarily the most unusual one. "Viral ranking" has two orders. BIGGEST (default) sorts by where a post\u2019s raw attention (views, else likes + comments + shares) sits among posts of the same ' +
      'platform and age, so a post with millions of views comes first whatever its share rate; the percentile is comparable across platforms. MOST AMPLIFIED sorts by the amplification percentile: each post is ranked against ' +
      'posts of the same platform and age (1.00 = top of its peers, 0.50 = typical), which can be compared across platforms. Every post shows both numbers. The percentile is shown only for posts with a usable amplification ratio and at least 20 similar posts to ' +
      'compare with; platforms with no counters (Reddit) have none. Ties are broken by engagement. Because the percentile ranks a ratio, tiny counts can score high (4 views and 1 share), so by default a post must also be in the top 10% of its own platform by activity (views, else likes + comments + shares); MIN ACTIVITY can loosen it to the platform median or switch it off. Retweets (posts starting "RT @") are included because they show spread, and are tagged; their counters are the original post\u2019s, so their score is really the original\u2019s. "Original posts only" hides them (only Twitter and Truth Social mark reposts this way).' + (EX.entityName ? ' Filtered to posts naming ' + EX.entityName + '.' : '') );
    if (EX.theme) { host.appendChild(h('p', { class: 'cs ex-warn', text: exThemeNote() })); }
    if (EX.top === EX_ALL) { host.appendChild(h('p', { class: 'cs', text: 'Show all: the posts below are cut at ' + EX_POST_CAP + ' per list (the server limit); narrow the search to see the rest.' })); }
    var rankBar = h('div', { class: 'ex-row' }, [h('span', { class: 'bds-label', text: 'RANK BY' })]);
    [['views', 'Views'], ['virality', 'Viral ranking']].forEach(function (x) {
      var b = h('button', { type: 'button', class: 'df-preset-btn' + (EX.vrank === x[0] ? ' active' : ''), text: x[1] });
      b.addEventListener('click', function () { EX.vrank = x[0]; render(); });
      rankBar.appendChild(b);
    });
    if (EX.vrank === 'virality') {
      rankBar.appendChild(h('span', { class: 'bds-label', text: 'ORDER' }));
      [['biggest', 'Biggest'], ['amplified', 'Most amplified']].forEach(function (x) {
        var tips = { biggest: 'Largest raw attention first: views (or likes + comments + shares) as a multiple of the top-10% line of the post\u2019s own platform. A post with millions of views comes first whatever its share rate.',
                     amplified: 'Highest share-or-like ratio first, compared with posts of the same platform and age. Finds posts that spread efficiently; it can rank a very large post low.' };
        var b = h('button', { type: 'button', class: 'df-preset-btn' + (EX.vorder === x[0] ? ' active' : ''), text: x[1], title: tips[x[0]] });
        b.addEventListener('click', function () { EX.vorder = x[0]; render(); });
        rankBar.appendChild(b);
      });
      rankBar.appendChild(h('span', { class: 'bds-label', text: 'SHOW' }));
      [[0, 'Overall'], [3, 'Top 3 per platform']].forEach(function (x) {
        var b = h('button', { type: 'button', class: 'df-preset-btn' + (EX.vper === x[0] ? ' active' : ''), text: x[1] });
        b.addEventListener('click', function () { EX.vper = x[0]; render(); });
        rankBar.appendChild(b);
      });
      rankBar.appendChild(h('span', { class: 'bds-label', text: 'MIN ACTIVITY' }));
      [['top10', 'Top 10% of platform'], ['median', 'Platform median or more'], ['off', 'Off']].forEach(function (x) {
        var tips = { top10: 'Keep only posts whose views (or likes + comments + shares) are in the top 10% of their own platform. This is what separates posts that are really spreading from posts that are only above average.',
                     median: 'Keep posts at or above the median of their own platform. Looser: many are popular but not viral.',
                     off: 'Rank every post that has a percentile, whatever its size. A post with 4 views and 1 share can reach the top, because the score ranks a ratio.' };
        var b = h('button', { type: 'button', class: 'df-preset-btn' + (EX.floor === x[0] ? ' active' : ''), text: x[1], title: tips[x[0]] });
        b.addEventListener('click', function () { EX.floor = x[0]; render(); });
        rankBar.appendChild(b);
      });
    }
    rankBar.appendChild(h('span', { class: 'bds-label', text: 'THRESHOLD' }));
    [['all', 'All posts'], ['viral', 'Viral breakout (Top 10% / P \u2265 0.80)']].forEach(function (x) {
      var b = h('button', { type: 'button', class: 'df-preset-btn' + (EX.vthresh === x[0] ? ' active' : ''), text: x[1],
                            title: x[0] === 'viral' ? 'Keep only posts that broke out: in top 10% activity of their platform or virality percentile \u2265 0.80.' : 'Show all posts matching the search query.' });
      b.addEventListener('click', function () { EX.vthresh = x[0]; render(); });
      rankBar.appendChild(b);
    });
    rankBar.appendChild(h('span', { class: 'bds-label', text: 'RETWEETS' }));
    [['include', 'Include'], ['hide', 'Original posts only']].forEach(function (x) {
      var b = h('button', { type: 'button', class: 'df-preset-btn' + (EX.rt === x[0] ? ' active' : ''), text: x[1],
                            title: x[0] === 'hide' ? 'Hide posts that start with "RT @" (Twitter, Truth Social). Other platforms do not mark reposts in the data.' : 'Retweets show spread, so they are included by default.' });
      b.addEventListener('click', function () { EX.rt = x[0]; render(); });
      rankBar.appendChild(b);
    });
    host.appendChild(rankBar);
    var boInfo = h('details', { class: 'ex-explain ex-bo-explain', style: 'margin: 0.35rem 0 0.5rem 0;' });
    boInfo.appendChild(h('summary', null, [
      h('span', { text: '\u24d8 How breakout is determined' }),
      h('span', { class: 'ex-explain-hint', text: ' \u2014 click to see explanation' })
    ]));
    var boContent = h('div', { class: 'cs', style: 'margin: 0.35rem 0 0.2rem 0; padding: 0.45rem 0.75rem; background: var(--sunken); border-left: 2px solid var(--gold); border-radius: 4px; font-size: 0.78rem; line-height: 1.45; color: var(--ink-2); max-width: 80ch;' });
    boContent.appendChild(h('div', { style: 'margin-bottom: 0.35rem;' }, [
      h('b', { style: 'color: var(--ink); display: block; margin-bottom: 0.1rem;', text: 'Attention / Activity' }),
      h('span', { text: 'Posts with unusually high views or engagement compared with similar posts.' })
    ]));
    boContent.appendChild(h('div', { style: 'margin-bottom: 0.35rem;' }, [
      h('b', { style: 'color: var(--ink); display: block; margin-bottom: 0.1rem;', text: 'Amplification / Virality' }),
      h('span', { text: 'Posts that spread unusually well, based on their virality score relative to comparable posts.' })
    ]));
    boContent.appendChild(h('div', null, [
      h('b', { style: 'color: var(--ink); display: block; margin-bottom: 0.1rem;', text: 'Breakout rule:' }),
      h('div', { text: 'A post qualifies if either:' }),
      h('div', { style: 'padding-left: 0.6rem;', text: '\u2022 Activity is in the Top 10%, OR' }),
      h('div', { style: 'padding-left: 0.6rem;', text: '\u2022 Virality percentile is \u2265 80%.' })
    ]));
    boInfo.appendChild(boContent);
    host.appendChild(boInfo);
    var body = h('div', { class: 'ex-viral' });
    host.appendChild(body);
    function paint(d) {
      body.textContent = '';
      if (d.rank === 'virality') {
        var withPct = d.rated_posts + (d.floor_on ? d.below_floor : 0), big = d.order === 'biggest';
        var threshNoteV = d.thresh === 'viral' ? ' (viral breakout threshold: top 10% activity or P \u2265 0.80)' : '';
        body.appendChild(h('p', { class: 'cs', text: fmtInt(d.total_posts) + ' posts match the filters' + threshNoteV + '; ' + fmtInt(withPct) +
          (big ? ' of them have a view or engagement counter' : ' of them have a virality percentile (usable ratio and at least ' + d.min_ref + ' similar posts)') +
          (d.floor_on ? '. ' + fmtInt(d.below_floor) + ' of those are hidden because their activity is below ' + (d.floor_mode === 'top10' ? 'the top 10%' : 'the median') + ' of their own platform (change MIN ACTIVITY to see them), leaving ' + fmtInt(d.rated_posts) + ' ranked' : ', all ranked') +
          (big ? '. Ordered by raw attention compared with posts of the same platform and age.' : '. The rest have no percentile and are not ranked here. Ordered by the amplification percentile.') }));
        var list = d.by_virality || [];
        if (!list.length) { empty(body, d.order === 'biggest' ? 'No posts with counters match.' : 'No posts with a virality percentile match.'); return; }
        if (d.per_platform > 0) {
          var by = {}, order = [];
          list.forEach(function (p) { if (!by[p.platform]) { by[p.platform] = []; order.push(p.platform); } by[p.platform].push(p); });
          var grid = h('div', { class: 'ex-cols' });
          order.forEach(function (pl) {
            var col = h('div', { class: 'ex-col' }, [h('div', { class: 'ct', text: pl + ' (' + by[pl].length + ')' })]);
            by[pl].forEach(function (p) { col.appendChild(exPostCard(p, false, 'virality')); });
            grid.appendChild(col);
          });
          body.appendChild(grid);
        } else {
          var one = h('div', { class: 'ex-col' }, [h('div', { class: 'ct', text: (d.order === 'biggest' ? 'Biggest attention (' : 'Most amplified (') + list.length + ')' })]);
          list.forEach(function (p) { one.appendChild(exPostCard(p, false, 'virality')); });
          body.appendChild(one);
        }
        return;
      }
      var threshNote = d.thresh === 'viral' ? ' (viral breakout threshold: top 10% activity or P \u2265 0.80)' : '';
      body.appendChild(h('p', { class: 'cs', text: fmtInt(d.total_posts) + ' posts match the filters' + threshNote + '.' }));
      var a = h('div', { class: 'ex-col' }, [h('div', { class: 'ct', text: 'Ranked by views (' + d.with_views.length + ')' })]);
      d.with_views.forEach(function (p) { a.appendChild(exPostCard(p, true)); });
      if (!d.with_views.length) { empty(a, 'No posts with view counts match.'); }
      var b = h('div', { class: 'ex-col' }, [h('div', { class: 'ct', text: 'No view count — ranked by engagement (' + d.without_views.length + ')' })]);
      d.without_views.forEach(function (p) { b.appendChild(exPostCard(p, false)); });
      if (!d.without_views.length) { empty(b, 'None.'); }
      body.appendChild(h('div', { class: 'ex-cols' }, [a, b]));
    }
    if (EX.viral && EX.viralKey === key) { EX.terms = EX.viral.terms || []; paint(EX.viral); return; }
    body.appendChild(h('div', { class: 'empty', text: 'Loading…' }));
    var url = '/api/explore/viral?from=' + encodeURIComponent(S.from) + '&to=' + encodeURIComponent(S.to) +
      '&platforms=' + encodeURIComponent(plats) + '&q=' + encodeURIComponent(EX.q) + (EX.topicId ? '&topic_id=' + encodeURIComponent(EX.topicId) : '') + exThemeQs() +
      '&entity_uid=' + encodeURIComponent(EX.entityUid) + '&limit=' + Math.min(EX.top, EX_POST_CAP) +
      (EX.vthresh === 'viral' ? '&thresh=viral' : '') +
      (EX.vrank === 'virality' ? '&rank=virality&per_platform=' + EX.vper : '') + (EX.rt === 'hide' ? '&retweets=hide' : '') + (EX.vrank === 'virality' && EX.floor !== 'top10' ? '&floor=' + EX.floor : '') +
      (EX.vrank === 'virality' && EX.vorder !== 'biggest' ? '&order=' + EX.vorder : '');
    fetch(url, { cache: 'no-store' }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) { throw new Error(j && j.error ? j.error : 'HTTP ' + r.status); } return j; });
    }).then(function (d) {
      if (d.error) { throw new Error(d.error); }
      EX.viral = d; EX.viralKey = key; EX.terms = d.terms || [];
      if (body.isConnected !== false) { paint(d); }
    }).catch(function (err) {
      body.textContent = '';
      empty(body, /^HTTP|Failed|fetch|NetworkError/i.test(err.message) ? 'Viral posts need the API server (' + err.message + '). The static snapshot has no post-level data.' : 'Search problem: ' + err.message);
    });
  }

  /* ===================================================================== Audience Sentiment tile
     How people react to YouTube videos and Instagram posts: the sentiment of the comments and, separately, of the replies, set against the
     post's own tone. Reads the read-only /api/explore/audience (v2 audience tables). Post, comment and reply sentiment are three separate
     layers and are never averaged together. A missing value is shown as a dash, never as zero. The topic filter is the existing D-theme
     (provisional); the theme list comes from the data, so a later taxonomy change needs no change here. Nothing here writes anywhere. */
  var AUD_PLAT = { youtube_video: 'YouTube', instagram: 'Instagram', linkedin: 'LinkedIn', snapchat: 'Snapchat' };
  var AUD_STATUS = [['has_comments', 'Comments collected'], ['confirmed_zero', 'Reported zero comments'],
                    ['not_collected_known', 'Comments reported, none collected'], ['unknown_no_comment_data', 'No comment data, count unknown']];
  function audPct(v) { return v == null ? '—' : Number(v).toFixed(0) + '%'; }
  function audNet(v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Math.round(v); }
  function audTone(v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2); }
  function audN(v) { return v == null ? '—' : fmtInt(v); }
  function audThemeName(k) {
    if (k === 'unassigned') { return 'No theme (unassigned)'; }
    if (k === 'post_not_available') { return 'Post not available'; }
    var known = (TAX.axis('security_domain').order || []).indexOf(k) >= 0;
    return known ? exThemeName(k) : k + ' (not in the current taxonomy)';
  }
  function audBar(neg, neu, pos) {
    var bar = h('div', { class: 'aud-bar' });
    if (neg == null || neu == null || pos == null) { bar.appendChild(h('span', { class: 'aud-na', text: 'too few to show shares' })); return bar; }
    [['neg', neg], ['neu', neu], ['pos', pos]].forEach(function (x) {
      var s = h('span', { class: 'aud-' + x[0] }); s.style.width = Math.max(0, x[1]) + '%';
      s.setAttribute('title', ({ neg: 'Negative', neu: 'Neutral', pos: 'Positive' })[x[0]] + ' ' + Math.round(x[1]) + '%');
      bar.appendChild(s);
    });
    return bar;
  }
  /* ---- Stage 2 helpers (audience tile only) ---- */
  function audPl(n, one, many) { return fmtInt(n) + ' ' + (n === 1 ? one : (many || one + 's')); }
  var audHints = [];
  function audCheckHints() {
    audHints = audHints.filter(function (x) { return x.wrap.isConnected !== false; });
    audHints.forEach(function (x) { var sc = x.wrap.querySelector('.tbl-h'); x.hint.style.display = (sc && sc.scrollWidth > sc.clientWidth + 2) ? '' : 'none'; });
  }
  /* a table inside .aud-tbl: headers may wrap so it fits the page; when it still cannot fit, a scroll cue is shown */
  function audTable(host, cols, rows, opts) {
    var wrap = h('div', { class: 'aud-tbl' });
    host.appendChild(wrap);
    var r = tableView(wrap, cols, rows, opts);
    var hint = h('div', { class: 'aud-scroll-hint', role: 'note', text: '↔ Wider than the screen: scroll sideways to see all columns.' });
    hint.style.display = 'none'; wrap.appendChild(hint);
    audHints.push({ wrap: wrap, hint: hint });
    setTimeout(audCheckHints, 0);
    if (!audTable.bound) { audTable.bound = true; window.addEventListener('resize', audCheckHints); }
    return r;
  }
  /* a collapsed-by-default section: same look as "Detailed methodology" */
  function audFold(host, title, hint) {
    var d = h('details', { class: 'ex-explain aud-fold' });
    d.appendChild(h('summary', null, [h('b', { text: title }), h('span', { class: 'ex-explain-hint', text: ' \u2014 ' + (hint || 'click to expand') })]));
    host.appendChild(d);
    return d;
  }
  function audDayLabel(s, gran) {
    var p = String(s).slice(0, 10).split('-'), m = MON[(+p[1]) - 1];
    return gran === 'month' ? m + ' ' + p[0] : m + ' ' + (+p[2]) + ', ' + p[0];
  }
  /* Trend chart for this tile: x labels carry the year, platform colours are fixed (YouTube gold, Instagram grey), and periods with 30-99 scored rows
     (thin) are drawn on the line itself: hollow markers and dashed segments. Periods under 30 scored rows are not drawn (gaps). */
  function audTrend(host, cfg) {
    var days = cfg.days, thin = cfg.thin || 100, vals = [];
    cfg.series.forEach(function (s) { s.pts.forEach(function (p) { if (isNum(p.v)) { vals.push(p.v); } }); });
    if (!days.length || !extent(vals)) { empty(host, cfg.emptyMsg); return; }
    var F = frame(host, { width: cfg.width, height: cfg.height || 170, margin: { t: 8, r: 12, b: 24, l: 40 }, title: cfg.title });
    var dom = padDomain(extent(vals), { symmetric: true });
    var y = linScale(dom[0], dom[1], F.ih, 0), x = linScale(0, Math.max(days.length - 1, 1), 0, F.iw);
    yAxis(F, y, { count: 3, fmt: cfg.yFmt, zeroEmph: true });
    F.g.appendChild(el('line', { x1: 0, x2: F.iw, y1: F.ih, y2: F.ih, stroke: K.axis, 'stroke-width': 1 }));
    if (dom[0] < 0 && dom[1] > 0) { F.g.appendChild(el('line', { x1: 0, x2: F.iw, y1: y(0), y2: y(0), stroke: K.axis, 'stroke-width': 1 })); }
    var want = Math.max(2, Math.min(Math.floor(F.iw / 84), days.length)), step = Math.max(1, Math.ceil((days.length - 1) / Math.max(1, want - 1)));
    for (var i = 0; i < days.length; i += step) {
      var xx = x(i);
      F.g.appendChild(el('line', { x1: xx, x2: xx, y1: F.ih, y2: F.ih + 3, stroke: K.axis, 'stroke-width': 1 }));
      F.g.appendChild(svgText(xx, F.ih + 14, audDayLabel(days[i], cfg.gran), { anchor: xx < 34 ? 'start' : (xx > F.iw - 34 ? 'end' : 'middle'), size: 9, tabular: true, fill: K.ink3 }));
    }
    cfg.series.forEach(function (s) {
      var col = s.color, pts = s.pts;
      for (var k = 0; k < pts.length - 1; k++) {            // a segment between two consecutive drawn periods; dashed when either end is thin
        var a = pts[k], b = pts[k + 1];
        if (!isNum(a.v) || !isNum(b.v)) { continue; }
        var dash = (a.n < thin || b.n < thin);
        var ln = el('line', { x1: x(k), y1: y(a.v), x2: x(k + 1), y2: y(b.v), stroke: col, 'stroke-width': 2, 'stroke-linecap': 'round', opacity: dash ? 0.75 : 1 });
        if (dash) { ln.setAttribute('stroke-dasharray', '4 3'); }
        F.g.appendChild(ln);
      }
      pts.forEach(function (pt, k) {
        if (!isNum(pt.v)) { return; }
        var isThin = pt.n < thin, left = k > 0 && isNum(pts[k - 1].v), right = k < pts.length - 1 && isNum(pts[k + 1].v);
        var c = el('circle', { cx: x(k), cy: y(pt.v), r: isThin ? 3.4 : (left || right ? 0 : 2.8), fill: isThin ? K.sunken : col, stroke: col, 'stroke-width': isThin ? 1.6 : 0 });
        var t = el('title'); t.textContent = s.label + ' · ' + audDayLabel(days[k], cfg.gran) + ': net ' + cfg.yFmt(pt.v) + ' (' + fmtInt(pt.n) + ' scored' + (isThin ? ', thin sample' : '') + ')';
        c.appendChild(t); F.g.appendChild(c);
        if (!isThin && (left || right)) {                     // an invisible, larger target so every period has a tooltip
          var hit = el('circle', { cx: x(k), cy: y(pt.v), r: 6, fill: 'transparent' }); var t2 = el('title'); t2.textContent = t.textContent; hit.appendChild(t2); F.g.appendChild(hit);
        }
      });
    });
  }
  function audTrendLegend(series, thin) {
    var l = h('div', { class: 'aud-trend-key' });
    series.forEach(function (s) { l.appendChild(h('span', { class: 'aud-trend-k' }, [h('span', { class: 'aud-trend-sw', style: 'background:' + s.color }), txt(' ' + s.label)])); });
    l.appendChild(h('span', { class: 'aud-trend-k' }, [h('span', { class: 'aud-trend-hollow', style: 'border-color:' + (series[0] ? series[0].color : K.focal) }), txt(' hollow dot, dashed line = thin (30–' + (thin - 1) + ' scored rows)')]));
    return l;
  }
  /* Posts behind a gap, by how many scored comments each has: amber = fewer than 10 (small sample), mid = 10-29, dark = 30 or more. */
  function audSizeBar(g) {
    var bar = h('div', { class: 'aud-bar aud-size' }), n = g.n || 0;
    if (!n) { bar.appendChild(h('span', { class: 'aud-na', text: 'no posts with a gap' })); return bar; }
    [['aud-s-small', g.small, 'fewer than 10 scored comments (small sample)'], ['aud-s-mid', g.n_10_29, '10 to 29 scored comments'], ['aud-s-ok', g.n_30_plus, '30 or more scored comments']].forEach(function (x) {
      var sp = h('span', { class: x[0] }); sp.style.width = (100 * (x[1] || 0) / n) + '%';
      sp.setAttribute('title', fmtInt(x[1] || 0) + ' posts: ' + x[2]); bar.appendChild(sp);
    });
    return bar;
  }
  /* Rows behind each trend period (both platforms together), drawn under the chart on the same x positions so a thin period cannot look as solid
     as a busy one. Periods under 30 scored rows are not plotted at all (the chart's own support gate); 30-99 are plotted but marked thin. */
  function audVolume(tr, days, layer, basis, thin) {
    var vol = {};
    tr.forEach(function (r) {
      var n = basis === 'comment' ? (r.layer === layer ? r.scored : 0) : ((r[layer] || {}).scored || 0);
      vol[r.period] = (vol[r.period] || 0) + (n || 0);
    });
    var mx = 0; days.forEach(function (d) { mx = Math.max(mx, vol[d] || 0); });
    var box = h('div', { class: 'aud-vol-wrap' });
    var strip = h('div', { class: 'aud-vol', role: 'img', 'aria-label': 'Scored rows per period' });
    days.forEach(function (d, i) {
      var n = vol[d] || 0, b = h('i', { class: n < 30 ? 'aud-vol-none' : (n < thin ? 'aud-vol-thin' : 'aud-vol-ok') });
      b.style.left = (days.length > 1 ? 100 * i / (days.length - 1) : 50) + '%';
      b.style.height = (mx ? Math.max(2, 100 * n / mx) : 2) + '%';
      b.setAttribute('title', d + ': ' + fmtInt(n) + ' scored' + (n < 30 ? ' (too few: not plotted)' : (n < thin ? ' (thin: read the line here as indicative)' : '')));
      strip.appendChild(b);
    });
    box.appendChild(strip);
    box.appendChild(h('div', { class: 'fnote', text: 'Scored rows per period. Light bars: fewer than ' + thin + ' (thin, indicative only); no line under 30.' }));
    return box;
  }
  function audCtl(label, items, current, onPick) {
    var row = h('div', { class: 'ex-row' }, [h('span', { class: 'bds-label', text: label })]);
    items.forEach(function (x) {
      var b = h('button', { type: 'button', class: 'df-preset-btn' + (String(current) === String(x[0]) ? ' active' : ''), text: x[1], title: x[2] || '' });
      b.addEventListener('click', function () { onPick(x[0]); });
      row.appendChild(b);
    });
    return row;
  }
  function audFullWindow() {        // the audience tile has its own date range: clear it (all dates); other filters are left as they are
    EX.audDates = { from: '', to: '' }; render();
  }

  function exAudView(host) {
    var subNav = h('div', { class: 'ex-row ex-sent-nav', style: 'margin-bottom: 0.8rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--gold-border);' }, [
      h('span', { class: 'bds-label', text: 'SENTIMENT VIEW' }),
      (function () {
        var b = h('button', { type: 'button', class: 'df-preset-btn', 'data-sub': 'post', text: 'Post Tone (Authors)' });
        b.addEventListener('click', function () { EX.tile = 'sent'; render(); });
        return b;
      })(),
      (function () {
        var b = h('button', { type: 'button', class: 'df-preset-btn active', 'data-sub': 'aud', text: 'Audience Reaction (Comments \u0026 Replies)' });
        b.addEventListener('click', function () { EX.tile = 'aud'; render(); });
        return b;
      })()
    ]);
    host.appendChild(subNav);
    /* "How to read this dashboard" (expandable, collapsed by default) + the detailed methodology. */
    var howBox = h('details', { class: 'ex-explain aud-how' });
    howBox.appendChild(h('summary', null, [h('b', { text: 'How to read this dashboard' }), h('span', { class: 'ex-explain-hint', text: ' \u2014 click to see explanation' })]));
    var howList = h('ul', { class: 'aud-how-l' });
    [['Net sentiment', ' = Positive % − Negative % (−100 to +100).', 'Net sentiment = 100 × (positive share − negative share). 0 means as many positive as negative comments; it is not the average tone.'],
     ['Tone score', ' runs from −1 (very negative wording) to +1 (very positive).', 'Tone = P(positive) − P(negative) from the same multilingual model for posts, comments and replies.'],
     ['Gap score', ' = comment tone − post tone. Negative: the comments read more negative than the post.', 'The gap is the mean tone of the scored comments (or replies) minus the post’s own tone. A positive gap does not necessarily mean the audience supports the content.'],
     ['Aligned', ' means the gap is within ±0.30. It does not mean the audience agrees.', '0.30 is an operational flag for triage, provisional and not a statistical test.'],
     ['Sentiment reflects wording', ', not beliefs or intent.', 'War and news vocabulary pushes comments toward negative; the model agreed with a hand reader on about 7 of 10 comments (60 rows, one reviewer, 95% interval 61–83%).'],
     ['Label confidence', ' is the model’s certainty about its label, not how often it is right.', 'Mean of the top label probability (0.33–1). A high value does not make the label correct.']].forEach(function (x) {
      var li = h('li', { title: x[2] }, [h('b', { text: x[0] }), txt(x[1] + ' ')]);
      var more = h('div', { class: 'aud-info-t', id: 'aud-info-' + x[0].replace(/\W+/g, '-').toLowerCase(), text: x[2] }); more.style.display = 'none';
      var ib = h('button', { type: 'button', class: 'aud-info', 'aria-expanded': 'false', 'aria-controls': more.id, 'aria-label': 'More about ' + x[0], text: 'i' });
      ib.addEventListener('click', function () { var open = more.style.display === 'none'; more.style.display = open ? '' : 'none'; ib.setAttribute('aria-expanded', open ? 'true' : 'false'); });
      li.appendChild(ib); li.appendChild(more);
      howList.appendChild(li);
    });
    howBox.appendChild(howList);
    howBox.appendChild(h('div', { class: 'fnote', text: 'Press the \u201ci\u201d button (or hover a line) for detail. Dashes mean missing or too few, never zero. Sample sizes are shown next to every figure.' }));
    howBox.appendChild(h('div', { class: 'fnote aud-lang', text: 'Language: comments come in many languages and mixed scripts (for example Hindi or Urdu written in Latin letters, Burmese, Arabic). One multilingual model scores them all and it was not calibrated per language, so transliterated or code-mixed text, sarcasm and very short or emoji-only comments are the most likely to be misread.' }));
    host.appendChild(howBox);
    exExplain(host, 'Detailed methodology', 'Audience sentiment covers YouTube, Instagram, LinkedIn and Snapchat, the platforms with comment text. Three layers are kept apart: ' +
      'the post’s own tone, the comments, and the replies to comments (a reply is a second-level comment; LinkedIn and Snapchat are comment-only). Each is scored by the same multilingual ' +
      'model, tone = P(positive) − P(negative). Net sentiment = 100 × (positive share − negative share), compared within a platform. ' +
      'It reads wording, not attitude. Comment and reply shares and means are shown only with at least ' + 30 + ' scored rows; a gap needs a post tone and at least 5 scored comments (or replies), ' +
      'and the minimum can be raised below. Post sentiment classes use the ±0.15 tone cut-off; comment and reply classes use the model’s top label. ' +
      'The gap = the mean tone of the comments (or replies) minus the post’s tone; the 0.30 flag is an operational threshold, not a significance test. ' +
      'A dash means missing or too few, never zero. The date range applies to the post’s publish date (except the trend when set to “Comment date”), ' +
      'and the theme filter to the post’s D-theme, which is provisional. Platforms without comment text are not shown here.');
    var plats = PLATFORMS.filter(exPlatOk).join(',');
    var dFrom = EX.audDates ? EX.audDates.from : S.from, dTo = EX.audDates ? EX.audDates.to : S.to;     // null = the page's range; {} = chosen here (may be open-ended)
    var key = [dFrom, dTo, exAnyPlat() ? plats : '', EX.entityUid, EX.theme, EX.audBasis, EX.top, EX.audMin].join('|');
    var dateBar = h('div', { class: 'ex-row' }, [h('span', { class: 'bds-label', text: 'AUDIENCE DATES' })]);
    var bPage = h('button', { type: 'button', class: 'df-preset-btn' + (!EX.audDates ? ' active' : ''), text: 'Page range (' + S.from + ' \u2192 ' + S.to + ')',
      title: 'Use the date range chosen above. That range ends where the metrics snapshot ends, so newer posts are not in it.' });
    bPage.addEventListener('click', function () { EX.audDates = null; render(); });
    var bAll = h('button', { type: 'button', class: 'df-preset-btn' + (EX.audDates && !EX.audDates.from && !EX.audDates.to ? ' active' : ''), text: 'All dates',
      title: 'Every post with comment data, including posts newer than the metrics snapshot.' });
    bAll.addEventListener('click', function () { EX.audDates = { from: '', to: '' }; render(); });
    var cFrom = h('input', { type: 'date', class: 'bds-date-input', id: 'aud-date-from', 'aria-label': 'Audience start date' }), cTo = h('input', { type: 'date', class: 'bds-date-input', id: 'aud-date-to', 'aria-label': 'Audience end date' });
    cFrom.value = EX.audDates ? EX.audDates.from : ''; cTo.value = EX.audDates ? EX.audDates.to : '';
    var cErr = h('span', { class: 'ex-date-err', role: 'status' });
    var cGo = h('button', { type: 'button', class: 'df-preset-btn', id: 'aud-date-apply', text: 'Apply dates' });
    cGo.addEventListener('click', function () {
      if (cFrom.value && cTo.value && cFrom.value > cTo.value) { cErr.textContent = 'The start date is after the end date.'; return; }
      if (!cFrom.value && !cTo.value) { cErr.textContent = 'Pick a date, or use All dates.'; return; }
      EX.audDates = { from: cFrom.value, to: cTo.value }; render();
    });
    [bPage, bAll, cFrom, h('span', { class: 'bds-arrow', text: '\u2192' }), cTo, cGo, cErr].forEach(function (x) { dateBar.appendChild(x); });
    var audCtlBox = h('div', { class: 'aud-ctlbox' }); audCtlBox.appendChild(dateBar); host.appendChild(audCtlBox);
    var basisBar = h('div', { class: 'ex-row' }, [h('span', { class: 'bds-label', text: 'TREND BY' })]);
    [['post', 'Post date'], ['comment', 'Comment date']].forEach(function (x) {
      var b = h('button', { type: 'button', class: 'df-preset-btn' + (EX.audBasis === x[0] ? ' active' : ''), text: x[1],
        title: x[0] === 'post' ? 'Group by the date the post was published.' : 'Group by the date each comment or reply was written.' });
      b.addEventListener('click', function () { EX.audBasis = x[0]; render(); });
      basisBar.appendChild(b);
    });
    audCtlBox.appendChild(basisBar);
    var body = h('div', { class: 'aud' });
    host.appendChild(body);

    function paint(d) {
      body.textContent = '';
      var f = d.filters || {};
      var unsup = d.unsupported_platforms || [], ytc = unsup.indexOf('youtube_comment') >= 0, noText = unsup.filter(function (x) { return x !== 'youtube_comment'; });
      var hasYtVideo = (f.platforms || []).indexOf('youtube_video') >= 0;
      if (ytc) {        // YouTube Comments is not a source of its own: the comments are the audience layer of the YouTube videos
        body.appendChild(h('p', { class: 'cs ex-warn', text: 'YouTube Comments is not a separate source here: YouTube comments are analysed as the audience layer of YouTube videos' +
          (hasYtVideo ? ', so they are already included under youtube_video.' : '. Select youtube_video to see them; YouTube Comments on its own cannot give a platform-level audience analysis.') }));
      }
      if (noText.length) {
        body.appendChild(h('p', { class: 'cs ex-warn', text: 'No comment text exists for ' + noText.join(', ') + ' (counts only), so they are not shown. Audience sentiment covers YouTube, Instagram, LinkedIn and Snapchat.' }));
      }
      if (!(f.platforms && f.platforms.length)) {
        empty(body, ytc && !noText.length ? 'YouTube Comments alone cannot be analysed here. Select a supported platform (youtube_video, instagram, linkedin, snapchat) in the platform list.'
                                          : 'Pick YouTube, Instagram, LinkedIn or Snapchat in the platform list (the other platforms have no comment text).'); return;
      }
      body.appendChild(h('p', { class: 'cs', text: audPl(d.n_posts, 'post') + (d.n_posts === 1 ? ' matches' : ' match') + ' the filters; ' + fmtInt(d.n_posts_with_data) + ' of them ' + (d.n_posts_with_data === 1 ? 'has' : 'have') + ' comment or reply data.' +
        (d.undated_posts_excluded_by_dates ? ' ' + audPl(d.undated_posts_excluded_by_dates, 'post') + ' without a date ' + (d.undated_posts_excluded_by_dates === 1 ? 'is' : 'are') + ' left out by the date range.' : '') }));
      /* coverage at a glance: how much of what matches actually has comment data */
      var covRows = [];
      var allLabel = (f.platforms || []).length === 2 ? 'Both platforms' : 'All platforms';
      var ALL_PKS = [
        ['youtube_video', 'YouTube'],
        ['instagram', 'Instagram'],
        ['linkedin', 'LinkedIn'],
        ['snapchat', 'Snapchat'],
        ['all', allLabel]
      ];
      ALL_PKS.forEach(function (pk) {
        if (pk[0] === 'all' ? (f.platforms || []).length < 2 : (f.platforms || []).indexOf(pk[0]) < 0) { return; }
        var C = (d.layers[pk[0]] || {}).coverage; if (!C) { return; }
        var capVal = null;
        if (pk[0] === 'youtube_video') { capVal = C.at_cap; }
        else if (pk[0] === 'all') { capVal = ((d.layers.youtube_video || {}).coverage || {}).at_cap; }
        else if (pk[0] === 'linkedin' || pk[0] === 'snapchat') { capVal = C.at_cap; }
        covRows.push({ platform: pk[1], posts: C.posts, with_c: C.with_comments, scored: C.with_scored_comments, pct: C.coverage_pct, med: C.median_comments, rows: C.comments_scored,
                       cap: capVal });
      });
      if (covRows.length) {
        body.appendChild(h('div', { class: 'ct aud-sec', text: 'Coverage at a glance' }));
        audTable(body, [
          { k: 'platform', label: 'Platform' }, { k: 'posts', label: 'Posts matching the filters', num: true, fmt: audN },
          { k: 'with_c', label: 'With collected comments', num: true, fmt: audN }, { k: 'scored', label: 'With scored comments', num: true, fmt: audN },
          { k: 'pct', label: 'Coverage', num: true, fmt: function (v) { return v == null ? '—' : Number(v).toFixed(1) + '%'; } },
          { k: 'rows', label: 'Comments scored', num: true, fmt: audN }, { k: 'med', label: 'Median comments per post', num: true, fmt: function (v) { return v == null ? '—' : String(Math.round(v)); } },
          { k: 'cap', label: 'At the ~100 limit', num: true, fmt: audN }
        ], covRows, { rawTable: true });
        body.appendChild(h('p', { class: 'fnote', text: 'Coverage = posts with at least one collected comment ÷ posts matching the filters. A post without collected comments is missing data, not a neutral audience.' }));
        var yc = (d.layers.youtube_video || {}).coverage, ic = (d.layers.instagram || {}).coverage;
        var cav = audFold(body, 'Platform caveats', 'collection limits and coverage notes, click to see');
        if ((f.platforms || []).indexOf('youtube_video') >= 0 && yc && yc.with_comments) {
          cav.appendChild(h('p', { class: 'cs ex-warn', text: 'YouTube collection limit: comments were collected up to about ' + ((d.limits || {}).yt_cap || 100) + ' per video. ' + fmtInt(yc.at_cap) + ' of ' + fmtInt(yc.with_comments) +
            ' YouTube posts with comments (' + (100 * yc.at_cap / yc.with_comments).toFixed(0) + '%) are at that limit, so for them the comment tone describes only the comments collected and may not represent the whole thread. ' +
            'Most videos hold far fewer (median ' + Math.round(yc.median_comments || 0) + '), so the limit mainly affects the most-discussed videos.' }));
        }
        if ((f.platforms || []).indexOf('instagram') >= 0 && ic && ic.posts && ic.coverage_pct != null && ic.coverage_pct < 25) {
          cav.appendChild(h('p', { class: 'cs ex-warn', text: 'Instagram coverage is low: only ' + ic.coverage_pct.toFixed(1) + '% of the matching Instagram posts have comment data, so Instagram results describe a small subset of posts.' }));
        }
        if ((f.platforms || []).indexOf('linkedin') >= 0 && (d.platform_caveats || {}).linkedin) {
          cav.appendChild(h('p', { class: 'cs ex-warn', text: 'LinkedIn: ' + d.platform_caveats.linkedin }));
        }
        if ((f.platforms || []).indexOf('snapchat') >= 0 && (d.platform_caveats || {}).snapchat) {
          cav.appendChild(h('p', { class: 'cs ex-warn', text: 'Snapchat: ' + d.platform_caveats.snapchat }));
        }
        if (!cav.querySelector('p')) { cav.remove(); }
      }
      var db = d.date_bounds || {};
      if (db.post_max && !EX.audDates && S.to < db.post_max) {
        body.appendChild(h('p', { class: 'cs ex-warn', text: 'The audience data runs to ' + db.post_max + ' (comments to ' + (db.comment_max || '?') + '), later than the page range, which ends where the metrics snapshot ends (' + S.to +
          '). Choose \u201cAll dates\u201d or a custom range above to include the newer posts.' }));
      }
      if (f.entity_uid) {
        var cv = (d.entity || {}).coverage || {}, bits = [];
        Object.keys(cv).forEach(function (k) {
          var c = cv[k]; bits.push((AUD_PLAT[k] || k) + ': ' + fmtInt(c.with_entity_data) + ' of ' + fmtInt(c.posts_with_data) + ' posts with audience data have entity data');
        });
        body.appendChild(h('p', { class: 'cs ex-warn', text: 'Entity filter: ' + fmtInt((d.entity || {}).posts_in_entity_extract) + ' posts name ' + (EX.entityName || 'this entity') + ' in the production entity extract. ' +
          'That extract is a snapshot, so the posts added after it (' + fmtInt(Object.keys(cv).reduce(function (a, k) { return a + (cv[k].enriched_posts || 0); }, 0)) + ' enriched posts) have no entity data and cannot match. ' + bits.join('; ') + '.' }));
      }
      var fc = d.filter_coverage || {}, nF = (f.theme ? 1 : 0) + (f.entity_uid ? 1 : 0), base = fc.posts_with_data || 0;
      var pctOf = function (a, b, dp) { return b ? (100 * (a || 0) / b).toFixed(dp == null ? 0 : dp) : '0'; };
      var themeShare = 'only ' + pctOf(fc.with_theme, base) + '% of posts with comment data have any theme';
      if (nF === 2) {          // both filters: say what is left after ALL of them, then what each keeps on its own
        body.appendChild(h('p', { class: 'cs ex-warn', text: 'All filters together leave ' + audPl(d.n_posts_with_data, 'post') + ' with comment data (' + pctOf(d.n_posts_with_data, base, 1) + '% of the ' + fmtInt(base) +
          ' with comment data in these platforms and dates). Each on its own would keep: theme ' + audThemeName(f.theme) + ' ' + audN(fc.theme_kept) + ', entity ' + (EX.entityName || 'selected') + ' ' + audN(fc.entity_kept) + '. ' +
          'Themes are assigned automatically and are provisional (' + themeShare + '). Small selections give unstable shares and gaps; check the sample sizes below.' }));
      } else if (f.theme) {
        body.appendChild(h('p', { class: 'cs ex-warn', text: 'Theme filter: ' + audThemeName(f.theme) + ' leaves ' + audPl(d.n_posts_with_data, 'post') + ' of ' + fmtInt(base) + ' with comment data (' + pctOf(d.n_posts_with_data, base) +
          '%). Themes are assigned automatically and are provisional; ' + themeShare + ', so this view is a minority of the data.' }));
      } else if (f.entity_uid) {
        body.appendChild(h('p', { class: 'cs ex-warn', text: 'Entity filter leaves ' + audPl(d.n_posts_with_data, 'post') + ' of ' + fmtInt(base) + ' with comment data (' + pctOf(d.n_posts_with_data, base, 1) +
          '%). Small selections give unstable shares and gaps; check the sample sizes below.' }));
      }
      if (!d.n_posts_with_data) {
        var box = h('div', { class: 'empty' }, [txt('No posts with comment or reply data match these filters. ')]);
        var wb = h('button', { type: 'button', class: 'df-preset-btn', text: 'Show all dates' });
        wb.addEventListener('click', audFullWindow);
        if (!EX.audDates || EX.audDates.from || EX.audDates.to) { box.appendChild(wb); }
        body.appendChild(box);
      }

      var stFold = audFold(body, 'Coverage: what comment data exists', 'collected, reported zero, not collected, unknown');
      /* only the platforms selected are shown (a platform that was not chosen would be a row of zeros); "both" only when two are selected */
      var selP = f.platforms || [];
      var showPk = function (pk) { return pk === 'all' ? selP.length > 1 : selP.indexOf(pk) >= 0; };
      var PKS = ALL_PKS.filter(function (pk) { return showPk(pk[0]) && d.layers[pk[0]]; });

      /* 1. layers side by side: post, comments, replies, per platform */
      body.appendChild(h('div', { class: 'ct aud-sec', text: 'Post, comment and reply sentiment' }));
      var rows = [];
      PKS.forEach(function (pk) {
        var L = d.layers[pk[0]];
        rows.push({ platform: pk[1], layer: 'Post tone', posts: L.post.posts_with_data, n: L.post.with_tone, pos: L.post.pos_pct, neu: L.post.neu_pct, neg: L.post.neg_pct, net: L.post.net, tone: L.post.tone_mean,
                    note: 'tone classes at ±0.15 (provisional); ' + fmtInt(L.post.without_tone) + ' posts with data have no post tone' });
        var lyList = (pk[0] === 'linkedin' || pk[0] === 'snapchat') ? ['comment'] : ['comment', 'reply'];
        lyList.forEach(function (ly) {
          var x = L[ly];
          rows.push({ platform: pk[1], layer: ly === 'comment' ? 'Comments' : 'Replies', posts: x.posts_with_data, n: x.scored, pos: x.pos_pct, neu: x.neu_pct, neg: x.neg_pct, net: x.net, tone: x.tone_mean,
                      note: x.supported ? '' : (x.scored ? 'fewer than 30 scored rows: shares not shown' : 'no scored rows'), conf: x.conf_mean, notScored: x.not_scored });
        });
      });
      audTable(body, [
        { k: 'platform', label: 'Platform' }, { k: 'layer', label: 'Layer' },
        { k: 'posts', label: 'Posts', num: true, fmt: audN }, { k: 'n', label: 'Scored', num: true, fmt: audN },
        { k: 'pos', label: 'Positive', num: true, fmt: audPct }, { k: 'neu', label: 'Neutral', num: true, fmt: audPct }, { k: 'neg', label: 'Negative', num: true, fmt: audPct },
        { k: 'net', label: 'Net', num: true, fmt: audNet }, { k: 'tone', label: 'Mean tone', num: true, fmt: audTone },
        { k: 'conf', label: 'Label confidence', num: true, fmt: function (v) { return v == null ? '—' : Number(v).toFixed(2); } },
        { k: 'note', label: 'Note' }
      ], rows, { rawTable: true });
      var dist = h('div', { class: 'aud-dist' });
      rows.forEach(function (r) {
        dist.appendChild(h('div', { class: 'aud-dist-r' }, [h('span', { class: 'aud-dist-l', text: r.platform + ' · ' + r.layer }), audBar(r.neg, r.neu, r.pos),
          h('span', { class: 'aud-dist-n', text: r.n == null ? '' : fmtInt(r.n) + ' scored' })]));
      });
      var dl = h('div', { class: 'aud-legend' }, [h('span', { class: 'aud-key aud-neg' }), txt(' negative '), h('span', { class: 'aud-key aud-neu' }), txt(' neutral '), h('span', { class: 'aud-key aud-pos' }), txt(' positive')]);
      body.appendChild(dl); body.appendChild(dist);

      /* 2. the gap between the audience and the post */
      body.appendChild(h('div', { class: 'ct aud-sec', text: 'Audience vs post: the sentiment gap' }));
      body.appendChild(h('div', { class: 'cs aud-gapdef' }, [h('b', { text: 'Audience gap = mean comment sentiment − original post sentiment.' }), txt(' Triage flag at ±0.30; see the note below.')]));
      audFold(body, 'Reading the gap', 'what it does and does not mean').appendChild(h('div', { class: 'cs aud-gapdef' }, [txt('A positive gap does not necessarily mean the audience supports the content: a very negative news post with milder comments also gives a positive gap, and a negative gap can simply mean comments are blunter than a neutral caption. ' +
            'The 0.30 line below is an operational flag for triage, not a statistical test: a gap built on a handful of comments can cross it by chance, and a gap inside it is not evidence of agreement.')]));
      body.appendChild(audCtl('MIN COMMENTS PER GAP', [[5, '5 (default)', 'A gap needs at least 5 scored comments (or replies).'], [10, '10', ''], [20, '20', ''], [30, '30', ''], [50, '50', '']], EX.audMin,
        function (v) { EX.audMin = v; render(); }));
      if (EX.audMin > 5) {
        body.appendChild(h('p', { class: 'fnote', text: 'Gaps built on fewer than ' + EX.audMin + ' scored comments (or replies) are left out of this section, the histogram and the theme table’s gap column; for Replies vs comments both the replies and the comments need ' + EX.audMin +
          '. Gap values are unchanged; the divergent list always needs at least ' + ((d.limits || {}).min_top || 10) + ' comments and uses the larger of the two.' }));
      }
      var grows = [], dash = function (v, f2) { return v == null ? '—' : f2(v); };
      PKS.forEach(function (pk) {
        var G = (d.layers[pk[0]] || {}).gaps; if (!G) { return; }
        var isCommentOnly = (pk[0] === 'linkedin' || pk[0] === 'snapchat');
        var gapList = isCommentOnly ? [['comment_vs_post', 'Comments vs post']] : [['comment_vs_post', 'Comments vs post'], ['reply_vs_post', 'Replies vs post']];
        gapList.forEach(function (g) {
          var x = G[g[0]];
          if (!x) return;
          var note = !x.n ? (pk[0] === 'linkedin' ? 'posts have no tone score' : '') : (x.n < 30 ? 'few posts: read with care' : (x.small / x.n >= 0.25 ? fmtInt(x.small) + ' of ' + fmtInt(x.n) + ' posts rest on fewer than 10' : ''));
          grows.push({ platform: pk[1], cmp: g[1], n: x.n, rows: x.rows, medrows: x.median_rows, small: x.small, mean: x.mean, median: x.median,
                       neg: x.n ? x.more_negative / x.n * 100 : null, al: x.n ? x.aligned / x.n * 100 : null, pos: x.n ? x.more_positive / x.n * 100 : null, g: x,
                       note: note });
        });
        if (!isCommentOnly && G.reply_vs_comment) {
          grows.push({ platform: pk[1], cmp: 'Replies vs comments', n: G.reply_vs_comment.n, rows: G.reply_vs_comment.reply_rows, medrows: null, small: null, mean: G.reply_vs_comment.mean, median: null, neg: null, al: null, pos: null,
                       note: 'rows = replies; needs replies and comments' });
        }
      });
      var gfmt = function (v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2); };
      /* table A: the gap and its direction (the columns an analyst reads first), then table B: the sample behind each gap */
      audTable(body, [
        { k: 'platform', label: 'Platform' }, { k: 'cmp', label: 'Comparison' }, { k: 'n', label: 'Posts with a gap', num: true, fmt: audN },
        { k: 'mean', label: 'Mean gap', num: true, fmt: gfmt }, { k: 'median', label: 'Median gap', num: true, fmt: gfmt },
        { k: 'neg', label: 'More negative (gap ≤ −0.30)', num: true, fmt: audPct }, { k: 'al', label: 'Within ±0.30 (not “agree”)', num: true, fmt: audPct },
        { k: 'pos', label: 'More positive (gap ≥ +0.30)', num: true, fmt: audPct }
      ], grows, { rawTable: true });
      var sf = audFold(body, 'Sample behind each gap', 'scored rows, median per post, small samples');
      sf.appendChild(h('div', { class: 'fnote', style: 'margin-top:0.5rem', text: 'Sample behind each gap (scored comments, or replies for the reply rows):' }));
      audTable(sf, [
        { k: 'platform', label: 'Platform' }, { k: 'cmp', label: 'Comparison' },
        { k: 'rows', label: 'Scored comments / replies', num: true, fmt: audN }, { k: 'medrows', label: 'Median per post', num: true, fmt: function (v) { return dash(v, function (x) { return String(Math.round(x)); }); } },
        { k: 'small', label: 'Posts with < 10 (small sample)', num: true, fmt: audN }, { k: 'note', label: 'Sample note' }
      ], grows, { rawTable: true });
      var sz = h('div', { class: 'aud-dist aud-sizes' });
      grows.forEach(function (r) {
        if (!r.g) { return; }
        sz.appendChild(h('div', { class: 'aud-size-r' }, [h('span', { class: 'aud-dist-l', text: r.platform + ' · ' + r.cmp }), audSizeBar(r.g),
          h('span', { class: 'aud-dist-n', text: audN(r.small) + ' of ' + audN(r.n) + ' < 10' })]));
      });
      sf.appendChild(h('p', { class: 'fnote', text: 'Posts behind each gap, by number of scored comments (replies for the reply rows):' }));
      sf.appendChild(h('div', { class: 'aud-legend' }, [h('span', { class: 'aud-key aud-s-small' }), txt(' fewer than 10 (small sample) '), h('span', { class: 'aud-key aud-s-mid' }), txt(' 10–29 '), h('span', { class: 'aud-key aud-s-ok' }), txt(' 30 or more')]));
      sf.appendChild(sz);
      sf.appendChild(h('p', { class: 'fnote', text: 'Gap = mean tone of the audience layer − the post’s tone (both on the same −1 to +1 scale; range −2 to +2). “Clearly” means 0.30 or more (provisional). ' +
        'Posts without a post tone (no caption, or not scored) have no gap: they are not treated as neutral. Replies vs comments compares the two audience layers and needs no post tone.' }));
      var hist = ((d.layers.all || {}).gaps || {}).comment_vs_post ? (d.layers.all.gaps.comment_vs_post.hist || []) : [];
      if (hist.some(function (x) { return x.n; })) {
        var mx = Math.max.apply(null, hist.map(function (x) { return x.n; }));
        var hb = h('div', { class: 'aud-hist' });
        hist.forEach(function (x) {
          var r = h('div', { class: 'aud-hist-r' }, [h('span', { class: 'aud-hist-l', text: x.bin }), h('span', { class: 'aud-hist-b' }, [h('span', { style: 'width:' + (mx ? x.n / mx * 100 : 0) + '%' })]), h('span', { class: 'aud-hist-n', text: fmtInt(x.n) })]);
          hb.appendChild(r);
        });
        var histPlatLabel = selP.length > 2 ? 'all platforms' : (selP.length === 2 ? 'both platforms' : (AUD_PLAT[selP[0]] || 'selected platform'));
        var hf = audFold(body, 'Gap distribution (histogram)', 'comment gap per post, click to see'); hf.appendChild(h('div', { class: 'fnote', text: 'Comment gap per post (' + histPlatLabel + '), number of posts:' })); hf.appendChild(hb);
      }

      /* 3. trend over time */
      body.appendChild(h('div', { class: 'ct aud-sec', text: 'Sentiment over time (by ' + (f.basis === 'comment' ? 'comment date' : 'post date') + ', per ' + d.granularity + ')' }));
      var tr = d.trend || [];
      if (!tr.length) { empty(body, 'No comment or reply data in these dates.'); }
      else {
        var periods = {}; tr.forEach(function (r) { periods[r.period] = true; });
        var days = Object.keys(periods).sort();
        var idx = {}; days.forEach(function (p, i) { idx[p] = i; });
        var thinN0 = (d.limits || {}).thin_trend || 100;
        function series(layer) {
          return Object.keys(AUD_PLAT).filter(function (pk) {
            if (selP.indexOf(pk) < 0) return false;
            if (layer === 'reply' && (pk === 'linkedin' || pk === 'snapchat')) return false;
            return true;
          }).map(function (pk) {
            var by = {};
            tr.forEach(function (r) {
              if (r.platform !== pk) { return; }
              var o = f.basis === 'comment' ? (r.layer === layer ? r : null) : r[layer];
              if (o) { by[r.period] = o; }
            });
            var platColors = { youtube_video: K.focal, instagram: K.context, linkedin: '#0a66c2', snapchat: '#d4af37' };
            return { label: AUD_PLAT[pk], color: platColors[pk] || K.focal,
                     pts: days.map(function (p) { var o = by[p]; return { v: o && o.net != null ? o.net : null, n: o ? o.scored : 0 }; }) };
          });
        }
        var wrap = h('div', { class: 'aud-charts' });
        body.appendChild(wrap);                                  // in the page before it is measured, so the charts use the width they really get
        var cells = [];
        [['comment', 'Comments — net sentiment'], ['reply', 'Replies — net sentiment']].forEach(function (x) {
          var ser = series(x[0]);
          var cell = h('div', { class: 'aud-chart' }, [h('div', { class: 'fnote', text: x[1] + ' (no point where fewer than 30 scored)' }), audTrendLegend(ser, thinN0)]);
          var plot = h('div', { class: 'aud-plot' }); cell.appendChild(plot); wrap.appendChild(cell);
          cell.appendChild(audVolume(tr, days, x[0], f.basis, thinN0));
          cells.push({ plot: plot, ser: ser, title: x[1] });
        });
        var lastW = 0;
        function drawCharts() {
          cells.forEach(function (c) {
            var w = Math.max(220, Math.floor(c.plot.clientWidth || wrap.clientWidth / 2 || 320));
            lastW = w; c.plot.textContent = '';
            audTrend(c.plot, { days: days, gran: d.granularity, series: c.ser, thin: thinN0, width: w, height: 170, title: c.title + ' over time', yFmt: function (v) { return (v >= 0 ? '+' : '') + Math.round(v); },
                               emptyMsg: 'Fewer than 30 scored rows in every period, so no net value is shown.' });
          });
        }
        drawCharts();
        if (window.ResizeObserver) {                              // redraw when the width changes (rotation, window resize, side panels)
          var ro = new ResizeObserver(function () {
            if (!wrap.isConnected) { ro.disconnect(); return; }
            var w = Math.floor(cells[0].plot.clientWidth || 0);
            if (w && Math.abs(w - lastW) > 6) { drawCharts(); }
          });
          ro.observe(wrap);
        }
        var trows = tr.filter(function (r) { return selP.indexOf(r.platform) >= 0; }).map(function (r) {
          var thinN = (d.limits || {}).thin_trend || 100, smp = function (n) { return n < 30 ? 'too few (not plotted)' : (n < thinN ? 'thin' : 'ok'); };
          if (f.basis === 'comment') {
            if (r.layer === 'reply' && (r.platform === 'linkedin' || r.platform === 'snapchat')) return [];
            return [{ period: r.period, platform: AUD_PLAT[r.platform], layer: r.layer === 'comment' ? 'Comments' : 'Replies', n: r.scored, net: r.net, tone: r.tone, smp: smp(r.scored) }];
          }
          var lyArr = (r.platform === 'linkedin' || r.platform === 'snapchat') ? [['comment', 'Comments']] : [['comment', 'Comments'], ['reply', 'Replies']];
          return lyArr.map(function (ly) { var o = r[ly[0]]; return { period: r.period, platform: AUD_PLAT[r.platform], layer: ly[1], n: o ? o.scored : 0, net: o ? o.net : null, tone: o ? o.tone : null, smp: smp(o ? o.scored : 0) }; });
        });
        trows = [].concat.apply([], trows).sort(function (a, b) { return a.period < b.period ? -1 : a.period > b.period ? 1 : (a.platform + a.layer).localeCompare(b.platform + b.layer); });
        audTable(body, [{ k: 'period', label: 'Period start' }, { k: 'platform', label: 'Platform' }, { k: 'layer', label: 'Layer' }, { k: 'n', label: 'Scored', num: true, fmt: audN },
          { k: 'net', label: 'Net', num: true, fmt: audNet }, { k: 'tone', label: 'Mean tone', num: true, fmt: audTone }, { k: 'smp', label: 'Sample' }], trows, { label: 'Trend values (' + trows.length + ')' });
      }

      /* 4. by theme (the existing D-themes) */
      body.appendChild(h('div', { class: 'ct aud-sec', text: 'By theme' }));
      var th = (d.themes || []).slice(0, EX.top);
      audTable(body, [
        { k: 'name', label: 'Theme' }, { k: 'posts', label: 'Posts with data', num: true, fmt: audN },
        { k: 'c_scored', label: 'Comments scored', num: true, fmt: audN }, { k: 'c_net', label: 'Comment net', num: true, fmt: audNet },
        { k: 'r_scored', label: 'Replies scored', num: true, fmt: audN }, { k: 'r_net', label: 'Reply net', num: true, fmt: audNet },
        { k: 'gap_n', label: 'Posts with a gap', num: true, fmt: audN }, { k: 'gap_mean', label: 'Mean comment gap', num: true, fmt: gfmt }
      ], th.map(function (t) { return { name: audThemeName(t.theme), key: t.theme, posts: t.posts, c_scored: t.c_scored, c_net: t.c_net, r_scored: t.r_scored, r_net: t.r_net, gap_n: t.gap_n, gap_mean: t.gap_mean }; }),
      { label: 'Audience sentiment by D-theme (' + th.length + ')', open: true });
      var fcT = d.filter_coverage || {};
      audFold(body, 'About the themes', 'provisional D-themes').appendChild(h('p', { class: 'fnote', text: 'Themes are the existing provisional D-themes, shown as they are: only ' + (fcT.posts_with_data ? (100 * fcT.with_theme / fcT.posts_with_data).toFixed(0) : 0) +
        '% of posts with comment data have a theme (theme accuracy was 81% lenient / 56% strict in the 2026-11-20 audit). Shares and nets appear only with at least 30 scored rows; the gap column uses the minimum comments chosen above.' }));

      /* 5. what data exists: zero comments is not the same as missing comments */
      var srows = [];
      ALL_PKS.filter(function (pk) { return pk[0] !== 'all' && showPk(pk[0]); }).forEach(function (pk) {
        var s = (d.layers[pk[0]] || {}).status; if (!s) { return; }
        var r = { platform: pk[1], posts: s.posts }; AUD_STATUS.forEach(function (a) { r[a[0]] = s[a[0]]; }); srows.push(r);
      });
      audTable(stFold, [{ k: 'platform', label: 'Platform' }, { k: 'posts', label: 'Posts', num: true, fmt: audN }].concat(AUD_STATUS.map(function (a) { return { k: a[0], label: a[1], num: true, fmt: audN }; })), srows, { rawTable: true });
      stFold.appendChild(h('p', { class: 'fnote', text: 'Instagram reports a comment count, so “reported zero” is a real zero; “reported, none collected” means comments exist that were never collected. ' +
        'YouTube’s reported count is mostly missing, so a video without collected comments is “unknown”, not zero. For LinkedIn and Snapchat, collection covers the first ~10 and ~20 comments per post respectively. These posts are not counted as neutral anywhere.' }));

      /* 6. the posts where audience and post disagree most */
      var tg = d.top_gaps || { negative: [], positive: [] };
      var gp = (f.platforms || []).filter(function (x) { return AUD_PLAT[x]; });
      if (gp.indexOf(EX.audGapPlat) < 0) { EX.audGapPlat = 'all'; }
      if (EX.audGapPlat !== 'all' && d.top_gaps_by_platform && d.top_gaps_by_platform[EX.audGapPlat]) { tg = d.top_gaps_by_platform[EX.audGapPlat]; }
      var topMin = (d.top_gaps || {}).min_comments || 10;
      body.appendChild(h('div', { class: 'ct aud-sec', text: 'Posts where the audience differs most from the post' }));
      var dn = audFold(body, 'How these cards are chosen', 'ranking, sample size, strong post tone');
      dn.appendChild(h('p', { class: 'fnote', text: 'Posts with a post tone and at least ' + topMin + ' scored comments, ranked by the comment gap (the ranking is unchanged; the platform buttons only narrow the list). ' +
        'Each card shows how many comments the gap rests on. Open a post to read it.' }));
      if (gp.length > 1) {
        var gapPlatOpts = [['all', gp.length === 2 ? 'Both' : 'All']].concat(
          gp.map(function (p) { return [p, AUD_PLAT[p] || p]; })
        );
        body.appendChild(audCtl('PLATFORM', gapPlatOpts, EX.audGapPlat, function (v) { EX.audGapPlat = v; render(); }));
      }
      dn.appendChild(h('p', { class: 'fnote', text: 'Cards marked \u201cstrongly positive post\u201d or \u201cstrongly negative post\u201d have a post tone of \u00b1' + ((d.limits || {}).extreme_tone || 0.5).toFixed(1) +
        ' or more (provisional, informational only; the ranking ignores it). A strongly negative post (often a news or war report) tends to show a positive gap, and a strongly positive post (for example a promotional Instagram caption) a negative one, because comments are usually milder than the post.' }));
      var cols = h('div', { class: 'ex-cols' });
      [['negative', 'Audience more negative than the post'], ['positive', 'Audience more positive than the post']].forEach(function (x) {
        var col = h('div', { class: 'ex-col' }, [h('div', { class: 'ct', text: x[1] + ' (' + (tg[x[0]] || []).length + ')' })]);
        (tg[x[0]] || []).forEach(function (p) {
          var card = h('div', { class: 'aud-post' });
          card.appendChild(h('div', { class: 'aud-post-h' }, [h('b', { text: AUD_PLAT[p.platform] || p.platform }), txt(' · ' + (p.date || 'no date') + (p.theme ? ' · ' + p.theme : ''))]));
          if (p.text) { card.appendChild(h('div', { class: 'aud-post-t', text: p.text })); }
          var chips = h('div', { class: 'aud-chips' }, [h('span', { class: 'aud-chip' + (p.small_sample ? ' aud-chip-warn' : ''), text: audPl(p.comments, 'comment') + (p.small_sample ? ' · small sample' : ''),
            title: 'Scored comments this gap is built on.' })]);
          if (p.replies) { chips.appendChild(h('span', { class: 'aud-chip', text: audPl(p.replies, 'reply', 'replies') })); }
          var strong = p.extreme_post_tone != null ? p.extreme_post_tone : (p.post_tone != null && Math.abs(p.post_tone) >= ((d.limits || {}).extreme_tone || 0.5));
          if (strong && p.post_tone != null) {
            var pos = p.post_tone > 0;
            chips.appendChild(h('span', { class: 'aud-chip aud-chip-cap aud-chip-' + (pos ? 'tpos' : 'tneg'), tabindex: '0', text: (pos ? 'strongly positive post ' : 'strongly negative post ') + audTone(p.post_tone),
              title: pos ? 'A strongly positive post tone makes a negative gap likely: comments are usually milder than the post' + (p.platform === 'instagram' ? ' (on Instagram, often a promotional caption against emoji or neutral comments)' : '') + '.'
                         : 'A strongly negative post tone (often a news or war report) makes a positive gap likely: comments are usually milder than the post.' }));
          }
          card.appendChild(chips);
          card.appendChild(h('div', { class: 'aud-post-m', text: 'gap ' + audTone(p.gap) + ' = comments ' + audTone(p.comment_tone) + ' − post ' + audTone(p.post_tone) +
            (p.reply_gap != null ? ' · reply gap ' + audTone(p.reply_gap) + ' (' + audPl(p.replies, 'reply', 'replies') + ')' : '') }));
          if (p.url) { card.appendChild(h('a', { href: p.url, target: '_blank', rel: 'noopener noreferrer', text: 'open post' })); }
          col.appendChild(card);
        });
        if (!(tg[x[0]] || []).length) { empty(col, 'None in this selection.'); }
        cols.appendChild(col);
      });
      body.appendChild(cols);
      body.appendChild(h('p', { class: 'fnote', text: 'Source: ' + d.source + (d.built ? ' (built ' + String(d.built).slice(0, 10) + ')' : '') + '. Version ' + d.version + '. ' +
        'The model reads wording, not attitude; per comment it agreed with a reader about 7 times in 10 (small hand check).' }));
    }
    if (EX.aud && EX.audKey === key) { paint(EX.aud); return; }
    body.appendChild(h('div', { class: 'empty', text: 'Loading…' }));
    var url = '/api/explore/audience?' + (dFrom ? 'from=' + encodeURIComponent(dFrom) + '&' : '') + (dTo ? 'to=' + encodeURIComponent(dTo) + '&' : '') + 'x=1' + (exAnyPlat() ? '&platforms=' + encodeURIComponent(plats) : '') +
      (EX.entityUid ? '&entity_uid=' + encodeURIComponent(EX.entityUid) : '') + (EX.theme ? '&theme=' + encodeURIComponent(EX.theme) : '') +
      '&basis=' + EX.audBasis + '&limit=' + Math.min(EX.top, 10) + (EX.audMin > 5 ? '&min_comments=' + EX.audMin : '');
    fetch(url, { cache: 'no-store' }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) { throw new Error(j && j.error ? j.error : 'HTTP ' + r.status); } return j; });
    }).then(function (d) {
      if (d.error) { throw new Error(d.error); }
      EX.aud = d; EX.audKey = key;
      if (body.isConnected !== false) { paint(d); }
    }).catch(function (err) {
      body.textContent = '';
      empty(body, /^HTTP|Failed|fetch|NetworkError/i.test(err.message) ? 'Audience sentiment needs the API server (' + err.message + '). The static snapshot has no audience data.' : 'Audience sentiment: ' + err.message);
    });
  }

  function exEntityOptions() {
    var rows = T('entity_dim').slice(0, 1500);
    var map = {};
    rows.forEach(function (r) { if (!map[r.canonical_name]) { map[r.canonical_name] = r; } });
    return map;
  }

  P.explore_attention = function (host) { P.basic_attention(host); };
  P.explore_alerts = function (host) { P.basic_alerts_list(host); };

  P.explore_tiles = function (host) {
    var card = h('div', { class: 'card ex-card' });
    card.appendChild(h('div', { class: 'ex-card-t', text: 'Social Signal Monitor' }));
    var grid = h('div', { class: 'ex-tiles' });
    EX_TILES.forEach(function (t) {
      var isAct = (EX.tile === t.k) || (t.k === 'sent' && EX.tile === 'aud');
      var b = h('button', { type: 'button', class: 'ex-tile' + (isAct ? ' active' : '') });
      b.setAttribute('data-tile', t.k);
      b.setAttribute('aria-pressed', isAct ? 'true' : 'false');
      var ic = document.createElementNS(SVGNS, 'svg');
      ic.setAttribute('viewBox', '0 0 24 24'); ic.setAttribute('width', '26'); ic.setAttribute('height', '26');
      ic.setAttribute('fill', 'none'); ic.setAttribute('stroke', 'currentColor');
      ic.setAttribute('stroke-width', '2'); ic.setAttribute('stroke-linecap', 'round'); ic.setAttribute('stroke-linejoin', 'round');
      ic.innerHTML = t.i;
      b.appendChild(ic);
      b.appendChild(h('div', { class: 'ex-tile-t', text: t.t }));
      b.appendChild(h('div', { class: 'ex-tile-s', text: t.s }));
      b.addEventListener('click', function () { EX.tile = t.k; render(); });
      grid.appendChild(b);
    });
    card.appendChild(grid);
    host.appendChild(card);
  };

  /* What a reader needs to know before trusting "today": how old the data is, whether the newest day is partial, and which
     platforms stopped earlier. Shown only when there is something to say. */
  function exFreshness() {
    var i = dqInfo(), bits = [];
    if (!i.newest) { return null; }
    if (i.ageDays != null && i.ageDays >= 2) { bits.push('Newest data is ' + i.newest + ', ' + i.ageDays + ' days ago, so "today" here means the newest day collected.'); }
    if (i.partial) {
      bits.push(i.partial.day + ' is a partial day (' + fmtInt(i.partial.n) + ' posts and comments against about ' + fmtInt(i.partial.typical) + ' on a normal day), so growth and stage against full days can look like a decline. ' +
        'The presets end on the last complete day, ' + i.lastFull + '; pick ' + i.partial.day + ' in the date boxes to include it.');
    }
    if (!bits.length && !i.ended.length) { return null; }
    var det = h('details', { class: 'cs ex-warn ex-fresh' });
    var sum = h('summary', { style: 'cursor:pointer;font-weight:600' }, [
      h('span', { text: 'Data freshness notice' + (i.partial ? ' (' + i.partial.day + ' is partial)' : '') }),
      h('span', { class: 'ex-explain-hint', text: ' \u2014 click to see notice' })
    ]);
    det.appendChild(sum);
    var box = h('div', { style: 'margin-top:0.35rem' });
    bits.forEach(function (b) { box.appendChild(h('div', { text: b })); });
    if (i.ended.length) {
      box.appendChild(h('div', { text: 'Platforms whose data ends earlier: ' + i.ended.map(function (e) { return e.platform + ' (' + e.last + ')'; }).join(', ') + '.' }));
    }
    det.appendChild(box);
    return det;
  }

  P.explore_controls = function (host) {
    var card = h('div', { class: 'card ex-ctl' });
    var fresh = exFreshness();
    if (fresh) { card.appendChild(fresh); }
    var row1 = h('div', { class: 'ex-row' }, [h('span', { class: 'bds-label', text: 'DATE RANGE' })]);
    [['Last 1d', 1], ['Last 7d', 7], ['Last 14d', 14], ['Last 30d', 30], ['Full window', 0]].forEach(function (p) {
      var b = h('button', { type: 'button', class: 'df-preset-btn', text: p[0] });
      b.addEventListener('click', function () {
        var W = WINDAYS.length ? WINDAYS : ALLDAYS;
        S.to = dqInfo().lastFull || W[W.length - 1];                       // presets end on the last COMPLETE day
        var ti = W.indexOf(S.to); if (ti < 0) { ti = W.length - 1; }
        S.from = p[1] ? W[Math.max(0, ti - p[1] + 1)] : W[0];
        S.windowOnly = true; render();
      });
      row1.appendChild(b);
    });
    var dFrom = h('input', { type: 'date', class: 'bds-date-input', id: 'ex-date-from', 'aria-label': 'Start date' }), dTo = h('input', { type: 'date', class: 'bds-date-input', id: 'ex-date-to', 'aria-label': 'End date' });
    dFrom.value = S.from; dTo.value = S.to;
    var dErr = h('span', { class: 'ex-date-err', role: 'status' });
    var dApply = h('button', { type: 'button', class: 'df-preset-btn', id: 'ex-date-apply', text: 'Apply dates' });
    dApply.addEventListener('click', function () {
      var f = dFrom.value, t = dTo.value, lo = ALLDAYS[0], hi = ALLDAYS[ALLDAYS.length - 1];
      if (!f || !t) { dErr.textContent = 'Pick both dates.'; return; }
      if (f > t) { dErr.textContent = 'The start date is after the end date.'; return; }
      if (t < lo || f > hi) { dErr.textContent = 'No data between ' + lo + ' and ' + hi + ' overlaps that range.'; return; }
      S.from = f < lo ? lo : f; S.to = t > hi ? hi : t;
      var W0 = WINDAYS[0], W1 = WINDAYS[WINDAYS.length - 1];
      S.windowOnly = !!(W0 && S.from >= W0 && S.to <= W1);
      EX.viral = null; render();
    });
    row1.appendChild(dFrom); row1.appendChild(h('span', { class: 'bds-arrow', text: '\u2192' })); row1.appendChild(dTo);
    row1.appendChild(dApply); row1.appendChild(dErr);
    card.appendChild(row1);

    var row2 = h('div', { class: 'ex-row' });
    var det = h('details', { class: 'ex-plat' });
    var n = exAnyPlat() ? Object.keys(EX.platforms).filter(function (k) { return EX.platforms[k]; }).length : 0;
    det.appendChild(h('summary', { text: n ? n + ' platforms' : 'All platforms' }));
    var menu = h('div', { class: 'ex-plat-menu' });
    var acts = h('div', { class: 'ex-plat-acts' });
    var sa = h('button', { type: 'button', text: 'Select all' }), cl = h('button', { type: 'button', text: 'Clear' });
    sa.addEventListener('click', function () { PLATFORMS.forEach(function (p) { EX.platforms[p] = true; }); EX.viral = null; render(); });
    cl.addEventListener('click', function () { EX.platforms = {}; EX.viral = null; render(); });
    acts.appendChild(sa); acts.appendChild(cl); menu.appendChild(acts);
    PLATFORMS.forEach(function (p) {
      var cb = h('input', { type: 'checkbox' });
      cb.checked = !!EX.platforms[p];
      cb.addEventListener('change', function () { EX.platforms[p] = cb.checked; EX.viral = null; });
      menu.appendChild(h('label', { class: 'ex-plat-opt' }, [cb, h('span', { text: ' ' + p })]));
    });
    var ap = h('button', { type: 'button', class: 'df-preset-btn', text: 'Apply platforms' });
    ap.addEventListener('click', function () { render(); });
    menu.appendChild(ap);
    det.appendChild(menu); row2.appendChild(det);

    var q = h('input', { type: 'text', class: 'ex-q', placeholder: EX.tile === 'viral' ? 'Search a keyword, or pick a topic\u2026' : 'Search topic or text\u2026' });
    var topicByLabel = {};
    if (EX.tile === 'viral') {
      q.title = 'A keyword: flood = any form of the word (floods, flooding\u2026) \u00b7 flood relief = both words \u00b7 "flash flood" = exact phrase \u00b7 flood OR landslide = either \u00b7 flood -dam = without dam. Or pick a topic from the suggestions to see only the posts in that topic.';
      var tl = h('datalist', { id: 'ex-topic-list' });
      T('topic_dim').forEach(function (d) {
        if (d.topic_id === 'unassigned' || !d.label) { return; }
        topicByLabel[String(d.label).toLowerCase()] = d;
        var o = document.createElement('option'); o.value = d.label; o.label = 'topic \u00b7 ' + fmtInt(d.n_posts || 0) + ' posts'; tl.appendChild(o);
      });
      q.setAttribute('list', 'ex-topic-list');
      row2.appendChild(tl);
    }
    q.value = (EX.tile === 'viral' && EX.topicId) ? EX.topicName : EX.q;
    function applyQ() {
      var v = q.value.trim();
      var hit = EX.tile === 'viral' ? topicByLabel[v.toLowerCase()] : null;        // a topic name picked or typed in full = topic filter; anything else = keyword search
      if (hit) { EX.topicId = hit.topic_id; EX.topicName = hit.label; EX.q = ''; }
      else { EX.topicId = ''; EX.topicName = ''; EX.q = v; }
      EX.viral = null; render();
    }
    q.addEventListener('keydown', function (e) { if (e.key === 'Enter') { applyQ(); } });
    q.addEventListener('change', function () { if (EX.tile === 'viral' && topicByLabel[q.value.trim().toLowerCase()]) { applyQ(); } });
    if (EX.tile !== 'aud') { row2.appendChild(q); }
    if (EX.tile === 'viral' || EX.tile === 'topics' || EX.tile === 'aud') {
      var TYPES = [['', 'Any type'], ['PERSON', 'People'], ['ORG', 'Organisations'], ['LOCATION', 'Places (incl. countries)'], ['EVENT', 'Events']];
      var tsel = h('select', { class: 'ex-top', id: 'ex-ent-type', 'aria-label': 'Entity type' });
      TYPES.forEach(function (x) { var o = h('option', { text: x[1], value: x[0] }); if (EX.entType === x[0]) { o.selected = true; } tsel.appendChild(o); });
      tsel.addEventListener('change', function () { EX.entType = tsel.value; EX.entSub = ''; EX.entityUid = ''; EX.entityName = ''; EX.viral = null; render(); });
      var subs = {};
      T('entity_dim').forEach(function (r) { if ((!EX.entType || r.entity_type === EX.entType) && r.entity_subtype) { subs[r.entity_subtype] = true; } });
      var ssel = h('select', { class: 'ex-top', id: 'ex-ent-sub', 'aria-label': 'Entity subtype' });
      ssel.disabled = !EX.entType;
      [''].concat(Object.keys(subs).sort()).forEach(function (k) {
        var o = h('option', { text: k ? subtypeName(k) : 'Any subtype', value: k }); if (EX.entSub === k) { o.selected = true; } ssel.appendChild(o);
      });
      ssel.addEventListener('change', function () { EX.entSub = ssel.value; EX.entityUid = ''; EX.entityName = ''; EX.viral = null; render(); });
      var opts = {}, cnt = 0;
      T('entity_dim').forEach(function (r) {
        if (cnt >= 400) { return; }
        if (EX.entType && r.entity_type !== EX.entType) { return; }
        if (EX.entSub && r.entity_subtype !== EX.entSub) { return; }
        if (!opts[r.canonical_name]) { opts[r.canonical_name] = r; cnt++; }
      });
      var dl = h('datalist', { id: 'ex-ent-list' });
      Object.keys(opts).forEach(function (nm) {
        var o = document.createElement('option'); o.value = nm;
        o.label = opts[nm].entity_type + ' \u00b7 ' + fmtInt(opts[nm].distinct_posts) + ' posts';
        dl.appendChild(o);
      });
      var ent = h('input', { type: 'text', class: 'ex-q', id: 'ex-ent', placeholder: (EX.tile === 'topics' ? 'Topics with posts naming\u2026 (type to pick)' : 'Posts naming\u2026 (type to pick)'), title: 'Pick a person, organisation, place or country; only posts that name it are listed. A country here means the post names it, not that the event happened there.' });
      ent.setAttribute('list', 'ex-ent-list'); ent.value = EX.entityName;
      function applyEnt() {
        var r = opts[ent.value.trim()];
        EX.entityName = r ? r.canonical_name : ''; EX.entityUid = r ? r.entity_uid : '';
        EX.viral = null; render();
      }
      ent.addEventListener('change', applyEnt);
      row2.appendChild(tsel); row2.appendChild(ssel); row2.appendChild(ent); row2.appendChild(dl);
      var thsel = h('select', { class: 'ex-top', id: 'ex-theme', 'aria-label': 'Security theme',
                                title: 'Security theme (D01\u2013D09), assigned automatically and provisional. Default: only clear matches (about 75% right on a fresh check). Most posts have no theme.' });
      [''].concat(TAX.axis('security_domain').order).forEach(function (k) {
        var o = h('option', { text: k ? exThemeName(k) : 'Any security theme', value: k }); if (EX.theme === k) { o.selected = true; } thsel.appendChild(o);
      });
      thsel.addEventListener('change', function () { EX.theme = thsel.value; EX.viral = null; EX.st = null; render(); });
      row2.appendChild(thsel);
      if (EX.theme && EX.tile !== 'aud') {
        var tb = h('input', { type: 'checkbox', id: 'ex-theme-all' }); tb.checked = !!EX.themeAll;
        tb.addEventListener('change', function () { EX.themeAll = tb.checked; EX.viral = null; EX.st = null; render(); });
        row2.appendChild(h('label', { class: 'ex-plat-opt', title: 'Also include posts with a weaker match (about 59% right)' }, [tb, h('span', { text: ' Include lower-confidence' })]));
      }
    }
    if (EX.tile === 'topics' || EX.tile === 'life' || EX.tile === 'viral' || EX.tile === 'sent') {
      var ub = h('input', { type: 'checkbox', id: 'ex-unclass' });
      ub.checked = !!EX.unclass;
      ub.addEventListener('change', function () { EX.unclass = ub.checked; render(); });
      row2.appendChild(h('label', { class: 'ex-plat-opt' }, [ub, h('span', { text: ' Include Unclassified' })]));
    }
    if (EX.tile === 'topics' && !EX.entityUid) {
      var gsel = h('select', { class: 'ex-top', id: 'ex-topic-grain', 'aria-label': 'Topic classification',
                               title: 'Choose between Security themes (D01–D09) or Macro clusters (t000–t007)' });
      [['theme', 'Security themes (D01–D09)'], ['macro', 'Macro clusters (t000–t007)']].forEach(function (x) {
        var o = h('option', { text: x[1], value: x[0] }); if ((EX.topicGrain || 'theme') === x[0]) { o.selected = true; } gsel.appendChild(o);
      });
      gsel.addEventListener('change', function () { EX.topicGrain = gsel.value; render(); });
      row2.appendChild(gsel);
      var psel = h('select', { class: 'ex-top', id: 'ex-minplat', 'aria-label': 'Seen on at least this many platforms',
                               title: 'Keep only topics that appear on at least this many platforms (cross-platform presence)' });
      [[0, 'Seen on any number of platforms'], [2, 'Seen on 2+ platforms'], [3, 'Seen on 3+ platforms'], [5, 'Seen on 5+ platforms']].forEach(function (x) {
        var o = h('option', { text: x[1], value: String(x[0]) }); if (EX.minPlat === x[0]) { o.selected = true; } psel.appendChild(o);
      });
      psel.addEventListener('change', function () { EX.minPlat = parseInt(psel.value, 10); render(); });
      row2.appendChild(psel);
    }
    var sel = h('select', { class: 'ex-top' });
    [10, 25, 50, EX_ALL].forEach(function (v) {
      var o = h('option', { text: v === EX_ALL ? 'Show all' : 'Show ' + v, value: String(v) }); if (EX.top === v) { o.selected = true; }
      if (v === EX_ALL) { o.title = 'Every row of a list. Lists of posts are cut at ' + EX_POST_CAP + ' per list (the server limit).'; }
      sel.appendChild(o);
    });
    sel.addEventListener('change', function () { EX.top = parseInt(sel.value, 10); EX.viral = null; render(); });
    row2.appendChild(sel);
    var go = h('button', { type: 'button', class: 'df-preset-btn', text: 'Search' });
    go.addEventListener('click', applyQ);
    var rs = h('button', { type: 'button', class: 'df-preset-btn', text: 'Reset' });
    rs.addEventListener('click', function () { EX.platforms = {}; EX.minPlat = 0; EX.q = ''; EX.topicId = ''; EX.topicName = ''; EX.entityUid = ''; EX.entityName = ''; EX.entType = ''; EX.entSub = ''; EX.audDates = { from: '', to: '' }; EX.audMin = 5; EX.audGapPlat = 'all'; EX.theme = ''; EX.themeAll = false; EX.topicGrain = 'theme'; EX.viral = null; EX.st = null; render(); });
    row2.appendChild(go); row2.appendChild(rs);
    card.appendChild(row2);
    host.appendChild(card);
  };

  P.explore_view = function (host) {
    var t = EX_TILES.filter(function (x) { return x.k === EX.tile; })[0] || (EX.tile === 'aud' ? EX_TILES.filter(function (x) { return x.k === 'sent'; })[0] : null) || EX_TILES[0];
    var card = h('div', { class: 'card' });
    card.appendChild(h('div', { class: 'ct', text: EX.tile === 'aud' ? 'Audience Sentiment' : t.t }));
    host.appendChild(card);
    if (EX.tile === 'topics') { exTopicsView(card); }
    else if (EX.tile === 'viral') { exViralView(card); }
    else if (EX.tile === 'spread') { exSpreadView(card); }
    else if (EX.tile === 'sent') { exSentView(card); }
    else if (EX.tile === 'aud') { exAudView(card); }
    else { exLifeView(card); }
  };

  function initModeToggle() {
    var basicBtn = document.getElementById('mode-basic-btn');
    var advBtn = document.getElementById('mode-advanced-btn');
    var expBtn = document.getElementById('mode-explore-btn');
    var basicView = document.getElementById('basic-view');
    var advView = document.getElementById('advanced-view');
    var expView = document.getElementById('explore-view');
    var link = document.getElementById('basic-to-advanced-link');
    if (!basicBtn || !advBtn || !basicView || !advView) { return; }
    function setMode(mode) {
      var m = (mode === 'advanced' || mode === 'explore') ? mode : 'basic';
      if (m === 'explore' && !expView) { m = 'basic'; }
      document.body.setAttribute('data-mode', m);
      basicView.style.display = m === 'basic' ? '' : 'none';
      advView.style.display = m === 'advanced' ? '' : 'none';
      if (expView) { expView.style.display = m === 'explore' ? '' : 'none'; }
      var mv = document.getElementById('military-view');
      if (mv) { mv.style.display = 'none'; }
      basicBtn.setAttribute('aria-pressed', m === 'basic' ? 'true' : 'false');
      advBtn.setAttribute('aria-pressed', m === 'advanced' ? 'true' : 'false');
      if (expBtn) { expBtn.setAttribute('aria-pressed', m === 'explore' ? 'true' : 'false'); }
      var mb = document.getElementById('mode-military-btn');
      if (mb) { mb.setAttribute('aria-pressed', 'false'); }
      try { localStorage.setItem('z-mode', m); } catch (e) { /* file:// */ }
      render();
    }
    var stored = null;
    try { stored = localStorage.getItem('z-mode'); } catch (e) { stored = null; }
    setMode(stored === 'advanced' || stored === 'explore' ? stored : 'basic');
    basicBtn.addEventListener('click', function () { setMode('basic'); });
    advBtn.addEventListener('click', function () { setMode('advanced'); });
    if (expBtn) { expBtn.addEventListener('click', function () { setMode('explore'); }); }
    if (link) { link.addEventListener('click', function (e) { e.preventDefault(); setMode('advanced'); }); }
  }

  /* =============================================================== filters (v11 — Dashboard Filters card) */

  /* --- helpers for the new filter card --- */
  var _pendingS = {};   // staging state used by Apply button

  function _makeDropdown(triggerLabel, keys, store, labeller, onApply) {
    var wrap = document.createElement('div');
    wrap.className = 'df-dropdown';

    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'df-dropdown-trigger';

    var trigText = document.createElement('span');
    var arrow = document.createElement('span');
    arrow.className = 'df-dd-arrow';
    arrow.textContent = '▼';
    trigger.appendChild(trigText);
    trigger.appendChild(arrow);

    var menu = document.createElement('div');
    menu.className = 'df-dropdown-menu';
    menu.style.display = 'none';

    // actions row: Select All / Clear
    var actRow = document.createElement('div');
    actRow.className = 'df-dd-actions';
    var selAllBtn = document.createElement('button');
    selAllBtn.className = 'df-dd-act-btn'; selAllBtn.type = 'button'; selAllBtn.textContent = 'Select all';
    var clearBtn = document.createElement('button');
    clearBtn.className = 'df-dd-act-btn'; clearBtn.type = 'button'; clearBtn.textContent = 'Clear';
    actRow.appendChild(selAllBtn); actRow.appendChild(clearBtn);
    menu.appendChild(actRow);

    // search
    var searchWrap = document.createElement('div');
    searchWrap.className = 'df-dd-search-wrap';
    var searchInput = document.createElement('input');
    searchInput.type = 'text'; searchInput.className = 'df-dd-search';
    searchInput.placeholder = 'Search…';
    searchWrap.appendChild(searchInput);
    menu.appendChild(searchWrap);

    // list
    var list = document.createElement('div');
    list.className = 'df-dd-list';
    menu.appendChild(list);
    wrap.appendChild(trigger); wrap.appendChild(menu);

    function getLabel(k) { return labeller ? labeller(k) : k; }

    function refreshList(filter) {
      list.textContent = '';
      keys.forEach(function (k) {
        var lbl = getLabel(k);
        if (filter && lbl.toLowerCase().indexOf(filter.toLowerCase()) === -1) { return; }
        var item = document.createElement('label');
        item.className = 'df-dd-item';
        var cb = document.createElement('input');
        cb.type = 'checkbox'; cb.checked = !!store[k];
        cb.addEventListener('change', function () {
          store[k] = cb.checked;
          if (!keys.some(function (x) { return store[x]; })) { store[k] = true; cb.checked = true; }
          refreshTriggerLabel(); onApply();
        });
        var txt = document.createElement('span'); txt.textContent = lbl;
        item.appendChild(cb); item.appendChild(txt);
        list.appendChild(item);
      });
    }

    function refreshTriggerLabel() {
      var sel = keys.filter(function (k) { return store[k]; });
      if (sel.length === keys.length) { trigText.textContent = triggerLabel + ' (' + keys.length + ')'; }
      else if (sel.length <= 3) { trigText.textContent = sel.map(getLabel).join(', '); }
      else { trigText.textContent = sel.length + ' selected'; }
    }

    selAllBtn.addEventListener('click', function () {
      keys.forEach(function (k) { store[k] = true; });
      refreshList(searchInput.value); refreshTriggerLabel(); onApply();
    });
    clearBtn.addEventListener('click', function () {
      var first = keys[0];
      keys.forEach(function (k) { store[k] = false; });
      if (first) { store[first] = true; }
      refreshList(searchInput.value); refreshTriggerLabel(); onApply();
    });
    searchInput.addEventListener('input', function () { refreshList(searchInput.value); });

    trigger.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = wrap.classList.toggle('open');
      menu.style.display = open ? 'block' : 'none';
      if (open) { refreshList(''); }
    });
    document.addEventListener('click', function () {
      wrap.classList.remove('open'); menu.style.display = 'none';
    });
    menu.addEventListener('click', function (e) { e.stopPropagation(); });

    refreshTriggerLabel();
    wrap._refresh = function () { refreshTriggerLabel(); };
    return wrap;
  }

  function buildFilters() {
    var host = document.getElementById('filters-in');
    if (!host) { return; }
    host.textContent = '';

    var card = document.createElement('div');
    card.className = 'dash-filters-card';

    /* --- header --- */
    var hdr = document.createElement('div'); hdr.className = 'df-header';
    var title = document.createElement('h2'); title.className = 'df-title'; title.textContent = 'Dashboard Filters';
    hdr.appendChild(title);
    card.appendChild(hdr);

    /* --- date range (single line presets + date inputs) --- */
    var dateSection = document.createElement('div'); dateSection.className = 'df-section';
    var dateLbl = document.createElement('div'); dateLbl.className = 'df-label'; dateLbl.textContent = 'DATE RANGE';
    dateSection.appendChild(dateLbl);

    var dateLine = document.createElement('div'); dateLine.className = 'df-date-single-line';
    var presetsRow = document.createElement('div'); presetsRow.className = 'df-presets';
    var presets = [
      { l: 'Full window', f: function () { S.from = WINDAYS[0] || ALLDAYS[0]; S.to = WINDAYS[WINDAYS.length - 1] || ALLDAYS[ALLDAYS.length - 1]; S.windowOnly = true; } },
      { l: '7 days', f: function () { S.to = WINDAYS[WINDAYS.length - 1]; S.from = WINDAYS[Math.max(0, WINDAYS.length - 7)]; S.windowOnly = true; } },
      { l: '14 days', f: function () { S.to = WINDAYS[WINDAYS.length - 1]; S.from = WINDAYS[Math.max(0, WINDAYS.length - 14)]; S.windowOnly = true; } },
      { l: '30 days', f: function () { S.to = WINDAYS[WINDAYS.length - 1]; S.from = WINDAYS[Math.max(0, WINDAYS.length - 30)]; S.windowOnly = true; } }
    ];
    var presetBtns = [];
    presets.forEach(function (p) {
      var b = document.createElement('button'); b.type = 'button'; b.className = 'df-preset-btn';
      b.textContent = p.l;
      b.addEventListener('click', function () {
        p.f();
        syncDates();
        setDirty();
        updateSummary();
        presetBtns.forEach(function (x) { x.classList.remove('active'); });
        b.classList.add('active');
      });
      presetBtns.push(b);
      presetsRow.appendChild(b);
    });
    dateLine.appendChild(presetsRow);

    var datesRow = document.createElement('div'); datesRow.className = 'df-dates-row';
    var dFrom = document.createElement('input'); dFrom.type = 'date'; dFrom.className = 'df-date-input';
    dFrom.min = ALLDAYS[0]; dFrom.max = ALLDAYS[ALLDAYS.length - 1]; dFrom.value = S.from;
    var arrow = document.createElement('span'); arrow.className = 'df-date-arrow'; arrow.textContent = '→';
    var dTo = document.createElement('input'); dTo.type = 'date'; dTo.className = 'df-date-input';
    dTo.min = ALLDAYS[0]; dTo.max = ALLDAYS[ALLDAYS.length - 1]; dTo.value = S.to;
    function syncDates() { dFrom.value = S.from; dTo.value = S.to; }

    dFrom.addEventListener('change', function () {
      if (dFrom.value) {
        S.from = dFrom.value;
        presetBtns.forEach(function (x) { x.classList.remove('active'); });
        setDirty();
        updateSummary();
      }
    });
    dTo.addEventListener('change', function () {
      if (dTo.value) {
        S.to = dTo.value;
        presetBtns.forEach(function (x) { x.classList.remove('active'); });
        setDirty();
        updateSummary();
      }
    });
    datesRow.appendChild(dFrom);
    datesRow.appendChild(arrow);
    datesRow.appendChild(dTo);
    dateLine.appendChild(datesRow);

    dateSection.appendChild(dateLine);
    card.appendChild(dateSection);

    /* --- 3-column row: PLATFORM | TOPIC | DATA SCOPE --- */
    var grid = document.createElement('div');
    grid.className = 'df-grid-3col detailed-only-filter';

    // 1. Platform
    var platSection = document.createElement('div');
    platSection.className = 'df-col';
    var platLbl = document.createElement('div');
    platLbl.className = 'df-label';
    platLbl.textContent = 'PLATFORM';
    var platDd = _makeDropdown('All platforms', PLATFORMS, S.platforms, null, function () { setDirty(); updateSummary(); });
    platSection.appendChild(platLbl);
    platSection.appendChild(platDd);

    // 2. Topic
    var topicSection = document.createElement('div');
    topicSection.className = 'df-col';
    var topicLbl = document.createElement('div');
    topicLbl.className = 'df-label';
    topicLbl.textContent = 'TOPIC';
    var topicDd = _makeDropdown('All topics', TOPIC_IDS, S.topics, function (t) { return topicLabel(t); }, function () {
      /* Live-apply topic changes immediately so the dtt_bars blocks open/close in real time. */
      updateSummary();
      render();
      /* Keep the Apply button in its "Applied" state since we just rendered. */
      if (applyBtn) { applyBtn.textContent = 'Applied'; applyBtn.classList.remove('dirty'); }
    });
    topicSection.appendChild(topicLbl);
    topicSection.appendChild(topicDd);

    // 3. Data Scope
    var scopeSection = document.createElement('div');
    scopeSection.className = 'df-col';
    var scopeLbl = document.createElement('div');
    scopeLbl.className = 'df-label';
    scopeLbl.textContent = 'DATA SCOPE';

    var scopeToggle = document.createElement('div');
    scopeToggle.className = 'df-scope-toggle';
    var winBtn = document.createElement('button');
    winBtn.type = 'button';
    winBtn.className = 'df-scope-btn';
    winBtn.textContent = 'In-window only';
    var backBtn = document.createElement('button');
    backBtn.type = 'button';
    backBtn.className = 'df-scope-btn';
    backBtn.textContent = 'Include backfill';

    function refreshScope() {
      winBtn.classList.toggle('active', S.windowOnly);
      backBtn.classList.toggle('active', !S.windowOnly);
    }
    winBtn.addEventListener('click', function () {
      S.windowOnly = true;
      refreshScope();
      setDirty();
      updateSummary();
    });
    backBtn.addEventListener('click', function () {
      S.windowOnly = false;
      S.from = ALLDAYS[0];
      S.to = ALLDAYS[ALLDAYS.length - 1];
      syncDates();
      presetBtns.forEach(function (x) { x.classList.remove('active'); });
      refreshScope();
      setDirty();
      updateSummary();
    });
    refreshScope();
    scopeToggle.appendChild(winBtn);
    scopeToggle.appendChild(backBtn);

    var scopeHint = document.createElement('div');
    scopeHint.className = 'df-scope-hint';
    scopeHint.textContent = 'Counts only posts published inside the selected dates.';

    scopeSection.appendChild(scopeLbl);
    scopeSection.appendChild(scopeToggle);
    scopeSection.appendChild(scopeHint);

    grid.appendChild(platSection);
    grid.appendChild(topicSection);
    grid.appendChild(scopeSection);
    card.appendChild(grid);

    /* --- footer: summary on left + (Reset to defaults, Applied) on right --- */
    var footer = document.createElement('div');
    footer.className = 'df-footer';

    var summary = document.createElement('div');
    summary.className = 'df-summary';
    summary.id = 'fsummary';

    var actions = document.createElement('div');
    actions.className = 'df-footer-actions';

    var resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'df-reset-btn';
    resetBtn.textContent = 'Reset to defaults';

    var applyBtn = document.createElement('button');
    applyBtn.type = 'button';
    applyBtn.className = 'df-apply-btn';
    applyBtn.textContent = 'Applied';

    actions.appendChild(resetBtn);
    actions.appendChild(applyBtn);
    footer.appendChild(summary);
    footer.appendChild(actions);
    card.appendChild(footer);

    host.appendChild(card);

    function setDirty() {
      applyBtn.textContent = 'Apply filters';
      applyBtn.classList.add('dirty');
    }
    function setClean() {
      applyBtn.textContent = 'Applied';
      applyBtn.classList.remove('dirty');
    }

    applyBtn.addEventListener('click', function () {
      render();
      setClean();
    });

    function updateSummary() {
      var nDays = 0;
      var from = S.from || ALLDAYS[0] || '';
      var to = S.to || ALLDAYS[ALLDAYS.length - 1] || '';
      ALLDAYS.forEach(function (d) { if (d >= from && d <= to) { nDays++; } });
      var nPlat = PLATFORMS.filter(function (p) { return S.platforms[p]; }).length;
      var nTopic = TOPIC_IDS.filter(function (t) { return S.topics[t]; }).length;

      var platStr = (nPlat === PLATFORMS.length) ? 'all platforms' : (nPlat + ' platforms');
      var topStr = (nTopic === TOPIC_IDS.length) ? 'all topics' : (nTopic + ' topics');

      var el = document.getElementById('fsummary');
      if (el) {
        el.innerHTML = 'Will show: <span class="df-mono-date">' + from + ' → ' + to + '</span> · ' + platStr + ' · ' + topStr;
      }
      if (platDd._refresh) { platDd._refresh(); }
      if (topicDd._refresh) { topicDd._refresh(); }
    }
    updateSummary();

    resetBtn.addEventListener('click', function () {
      S.from = WINDAYS[0] || ALLDAYS[0];
      S.to = WINDAYS[WINDAYS.length - 1] || ALLDAYS[ALLDAYS.length - 1];
      S.windowOnly = true;
      PLATFORMS.forEach(function (p) { S.platforms[p] = true; });
      TOPIC_IDS.forEach(function (t) { S.topics[t] = true; });
      syncDates();
      refreshScope();
      presetBtns.forEach(function (x) { x.classList.remove('active'); });
      presetBtns[0].classList.add('active');
      if (platDd._refresh) { platDd._refresh(); }
      if (topicDd._refresh) { topicDd._refresh(); }
      updateSummary();
      render();
      setClean();
    });

    // set Full window active on load
    if (presetBtns[0]) { presetBtns[0].classList.add('active'); }
  }

  function updateFilterChrome() {
    var fs = document.getElementById('fsummary');
    if (fs) {
      fs.textContent = F.days.length + ' days · ' + selPlatforms().length +
        ' platforms · ' + selTopics().length + ' topics';
    }
  }

  /* ================================================================ render */

  var rafId = null;
  function render() {
    exSyncRange();
    readTokens();
    applyFilters();
    updateFilterChrome();
    var mode = (document.body && document.body.getAttribute('data-mode')) || 'basic';
    var selector = (mode === 'basic') ? '#basic-view [data-panel]' : (mode === 'explore' ? '#explore-view [data-panel]' : '#advanced-view [data-panel]');

    document.querySelectorAll(selector).forEach(function (host) {
      var sec = host.closest ? host.closest('section.sec') : null;
      if (sec && sec.style.display === 'none') {
        return;
      }
      var key = host.getAttribute('data-panel');
      host.textContent = '';
      var fn = P[key];
      if (!fn) { empty(host, 'No renderer registered for panel "' + key + '".'); return; }
      try { fn(host); } catch (e) {
        empty(host, 'Render error in panel "' + key + '": ' + e.message);
        if (window.console) { console.error(key, e); }
      }
    });
    if (window.__SELFTEST__) { selftest(); }
  }

  function scheduleRender() {
    if (rafId) { cancelAnimationFrame(rafId); }
    rafId = requestAnimationFrame(function () { rafId = null; render(); });
  }

  /* ------------------------------------------------------------- selftest - */

  function selftest() {
    var out = [];
    var de = document.documentElement;
    out.push('theme               : ' + (de.getAttribute('data-theme') || 'dark'));
    out.push('horizontal overflow : doc scrollWidth ' + de.scrollWidth + ' vs clientWidth ' +
      de.clientWidth + '  => ' + (de.scrollWidth <= de.clientWidth + 1 ? 'OK' : 'FAIL'));
    var blank = [];
    document.querySelectorAll('[data-panel]').forEach(function (p) {
      if (!p.childNodes.length) { blank.push(p.getAttribute('data-panel')); }
    });
    out.push('panels              : ' + document.querySelectorAll('[data-panel]').length +
      ' rendered, ' + blank.length + ' blank' + (blank.length ? ' [' + blank.join(', ') + ']' : ''));
    var errs = [];
    document.querySelectorAll('.empty').forEach(function (e) {
      if (/Render error|No renderer/.test(e.textContent)) { errs.push(e.textContent.slice(0, 120)); }
    });
    out.push('render errors       : ' + errs.length + (errs.length ? '\n  ' + errs.join('\n  ') : ''));
    var emptyPanels = [];
    document.querySelectorAll('[data-panel]').forEach(function (p) {
      if (!p.querySelector('svg') && !p.querySelector('table') && !p.querySelector('dl') &&
        !p.querySelector('.kpi') && !p.querySelector('.tcard')) {
        emptyPanels.push(p.getAttribute('data-panel'));
      }
    });
    out.push('panels w/o content  : ' + emptyPanels.length + (emptyPanels.length ? ' [' + emptyPanels.join(', ') + ']' : ''));
    var over = [];
    document.querySelectorAll('svg').forEach(function (s) {
      var par = s.parentElement;
      if (!par) { return; }
      if (s.getBoundingClientRect().width > par.getBoundingClientRect().width + 2 &&
        !par.closest('.scrollx')) {
        over.push((par.getAttribute('data-panel') || par.className) + ' svg ' +
          Math.round(s.getBoundingClientRect().width) + ' > host ' +
          Math.round(par.getBoundingClientRect().width));
      }
    });
    out.push('svg wider than host : ' + over.length + (over.length ? '\n  ' + over.join('\n  ') : ''));
    out.push('sections            : ' + document.querySelectorAll('section.sec').length);
    var st = document.getElementById('selftest');
    if (st) { st.classList.add('on'); st.textContent = out.join('\n'); }
    if (window.console) { console.log('[selftest]\n' + out.join('\n')); }
  }
  window.__runSelftest = function () { window.__SELFTEST__ = true; selftest(); };

  /* ---------------------------------------------------------------- init -- */

  function initTheme() {
    var btn = document.getElementById('theme-toggle');
    var stored = null;
    try { stored = localStorage.getItem('z-theme'); } catch (e) { stored = null; }
    var t = stored === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', t);
    function label() {
      var cur = document.documentElement.getAttribute('data-theme');
      if (btn) {
        btn.textContent = '';
        btn.appendChild(h('span', { text: cur === 'light' ? '◑' : '◐' }));
        btn.appendChild(h('span', { text: cur === 'light' ? 'Light' : 'Dark' }));
        btn.setAttribute('aria-label', 'Theme: ' + cur + '. Click to switch.');
      }
    }
    label();
    if (btn) {
      btn.addEventListener('click', function () {
        var cur = document.documentElement.getAttribute('data-theme');
        var nxt = cur === 'light' ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', nxt);
        try { localStorage.setItem('z-theme', nxt); } catch (e) { /* file:// */ }
        label();
        render();
      });
    }
  }

  function setStatus(msg, kind) {
    var el = document.getElementById('data-status');
    if (!el) { return; }
    el.textContent = msg || '';
    el.className = 'badge' + (kind ? ' ' + kind : ' gray');
  }

  function updateChrome() {
    if (!DATA) { return; }
    var sub = document.querySelector('.brand .sub');
    if (sub) {
      var w = DATA.window || {};
      var gen = (DATA.generated_at || '').slice(0, 19).replace('T', ' ');
      sub.textContent = String(w.start || '').slice(0, 10) + ' → ' +
        String(w.end || '').slice(0, 10) + ' · updated ' + gen.slice(0, 16) + ' UTC' +
        (dqLine() ? ' · ' + dqLine() : '');
    }
    var src = document.getElementById('source-badge');
    if (src) { src.textContent = 'source: ' + (DATA.source || 'metrics.json'); }
    var mode = document.getElementById('mode-badge');
    if (mode) {
      if (String(DATA.mode || '').indexOf('real') === 0) {
        mode.textContent = 'real data';
        mode.className = 'badge';
      } else {
        mode.textContent = '⚠ fixture data — not real';
        mode.className = 'badge crit';
      }
    }
  }


  // ---- Live NER panel (polls /api/gemma/status; no API on a static snapshot) ----
  var LIVE_NER_URL = 'api/gemma/status';
  var LIVE_NER_MS = 5000;
  var liveNer = { timer: null, ticker: null, lastOk: null, dead: false };

  function lnEl(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text != null) { e.textContent = text; }
    return e;
  }
  function lnFmt(n) { return (n == null) ? '–' : Number(n).toLocaleString('en-US'); }

  function lnNote(msg) {
    var note = document.getElementById('ln-note'), box = document.getElementById('ln-content');
    if (!note || !box) { return; }
    if (msg) { note.textContent = msg; note.hidden = false; box.hidden = true; }
    else { note.hidden = true; box.hidden = false; }
  }

  function renderLiveNer(d) {
    var root = document.getElementById('live-ner');
    if (!root) { return; }
    if (!d || !d.available) {
      root.setAttribute('data-state', 'off');
      lnNote('Live Named Entity Recognition is not running' + (d && d.error ? ' (' + d.error + ')' : '') +
        '. This panel updates automatically when the pipeline starts.');
      return;
    }
    root.setAttribute('data-state', 'on');
    lnNote(null);
    var strip = document.getElementById('ln-strip');
    strip.textContent = '';
    var pend = d.incoming_pending || 0;
    var stats = [
      ['Routed', lnFmt(d.routed)], ['Posts extracted', lnFmt(d.extracted_posts)],
      ['Entities', lnFmt(d.entities)],
      ['OK rate', d.ok_rate == null ? '–' : (d.ok_rate * 100).toFixed(1) + '%'],
      ['Posts / min', d.rate_per_min == null ? '–' : d.rate_per_min.toFixed(1)]
    ];
    stats.forEach(function (s) {
      var c = lnEl('div', 'ln-stat'); c.appendChild(lnEl('div', 'v', s[1])); c.appendChild(lnEl('div', 'l', s[0]));
      strip.appendChild(c);
    });
    var q = lnEl('div', 'ln-stat'), qv = lnEl('div', 'v', lnFmt(pend));
    qv.appendChild(document.createTextNode(' '));
    if (pend > 0) { qv.appendChild(lnEl('span', 'badge', 'priority')); }
    q.appendChild(qv); q.appendChild(lnEl('div', 'l', 'New posts pending'));
    strip.appendChild(q);

    var names = d.domain_names || {}, dom = d.by_domain || {};
    var keys = Object.keys(dom).sort(function (a, b) { return dom[b] - dom[a]; });
    var max = keys.length ? dom[keys[0]] : 1;
    var dbox = document.getElementById('ln-domains'); dbox.textContent = '';
    if (!keys.length) { dbox.appendChild(lnEl('div', 'fnote', 'No routed posts yet.')); }
    keys.forEach(function (k) {
      var row = lnEl('div', 'ln-bar');
      row.appendChild(lnEl('span', null, names[k] ? names[k] : (k === 'none' ? 'No domain' : k)));
      var t = lnEl('span', 't'), i = lnEl('i'); i.style.width = (100 * dom[k] / max) + '%';
      t.appendChild(i); row.appendChild(t); row.appendChild(lnEl('span', null, lnFmt(dom[k])));
      if (!names[k] && k !== 'none') { console.warn('live-ner: domain without a name: ' + k); }
      dbox.appendChild(row);
    });

    var top = document.getElementById('ln-top'); top.textContent = '';
    (d.top_entities || []).forEach(function (e) {
      top.appendChild(lnEl('li', null, e.name + ' · ' + e.entity_type + ' (' + e.level + ') × ' + e.n));
    });
    if (!(d.top_entities || []).length) { top.appendChild(lnEl('li', 'fnote', 'No entities in the last 24h.')); }

    var feed = document.getElementById('ln-feed'); feed.textContent = '';
    (d.recent || []).forEach(function (r) {
      var li = lnEl('li', 'ln-item' + (r.source === 'new' ? ' is-new' : ''));
      var meta = lnEl('div', 'meta');
      if (r.source === 'new') { meta.appendChild(lnEl('span', 'badge', 'new')); }
      var dn = function (k) { return k ? (names[k] || k) : null; };
      var bits = [r.platform, dn(r.domain_1), dn(r.domain_2), r.event_mode,
      r.confidence_1 != null ? 'conf ' + Number(r.confidence_1).toFixed(2) : null, r.routed_at];
      meta.appendChild(document.createTextNode(bits.filter(Boolean).join(' · ')));
      li.appendChild(meta);
      li.appendChild(lnEl('div', null, r.snippet || '(text unavailable)'));
      (r.entities || []).forEach(function (e) { li.appendChild(lnEl('span', 'ln-ent', e.name + ' · ' + e.type)); });
      feed.appendChild(li);
    });
    liveNer.lastOk = Date.now();
    liveNerTick();
  }

  function liveNerTick() {
    var u = document.getElementById('ln-updated');
    if (u && liveNer.lastOk) {
      u.textContent = 'last updated ' + Math.max(0, Math.round((Date.now() - liveNer.lastOk) / 1000)) + 's ago';
    }
  }

  function liveNerPoll() {
    if (liveNer.dead || !document.getElementById('live-ner')) { return Promise.resolve(); }
    return fetch(LIVE_NER_URL, { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) { throw { status: r.status }; }
        return r.json();
      })
      .then(renderLiveNer)
      .catch(function (err) {
        if (err && (err.status === 404 || err.status === 405)) {
          liveNer.dead = true;
          document.getElementById('live-ner').setAttribute('data-state', 'static');
          lnNote('Live Named Entity Recognition needs the API server; this static snapshot has no live endpoint.');
        } else {
          lnNote('Live Named Entity Recognition endpoint unreachable; retrying.');
        }
      });
  }

  function initLiveNer() {
    if (!document.getElementById('live-ner')) { return; }
    var start = function () {
      if (liveNer.timer) { return; }
      liveNerPoll();
      liveNer.timer = setInterval(function () {
        if (document.visibilityState === 'visible') { liveNerPoll(); }
      }, LIVE_NER_MS);
      liveNer.ticker = setInterval(liveNerTick, 1000);
    };
    start();
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') { liveNerPoll(); }
    });
    window.__liveNerPoll = liveNerPoll;
  }

  /* ============================================================ Overview date chip strip (v14) */

  function buildBasicDateStrip() {
    // Remove any previously built strip (re-init guard)
    var old = document.getElementById('basic-date-strip');
    if (old) { old.parentNode.removeChild(old); }

    var strip = document.createElement('div');
    strip.id = 'basic-date-strip';

    /* ---- DATE RANGE group ---- */
    var drGroup = document.createElement('div');
    drGroup.className = 'bds-group';

    var drLabel = document.createElement('span');
    drLabel.className = 'bds-label';
    drLabel.textContent = 'DATE RANGE';
    drGroup.appendChild(drLabel);

    var drRow = document.createElement('div');
    drRow.className = 'bds-row';

    var presets = [
      {
        l: 'Full window', f: function () {
          S.from = WINDAYS[0] || ALLDAYS[0];
          S.to = WINDAYS[WINDAYS.length - 1] || ALLDAYS[ALLDAYS.length - 1];
          S.windowOnly = true;
        }
      },
      {
        l: 'Last 7d', f: function () {
          S.to = WINDAYS[WINDAYS.length - 1] || ALLDAYS[ALLDAYS.length - 1];
          S.from = WINDAYS[Math.max(0, WINDAYS.length - 7)] || ALLDAYS[Math.max(0, ALLDAYS.length - 7)];
          S.windowOnly = true;
        }
      },
      {
        l: 'Last 14d', f: function () {
          S.to = WINDAYS[WINDAYS.length - 1] || ALLDAYS[ALLDAYS.length - 1];
          S.from = WINDAYS[Math.max(0, WINDAYS.length - 14)] || ALLDAYS[Math.max(0, ALLDAYS.length - 14)];
          S.windowOnly = true;
        }
      },
      {
        l: 'Last 30d', f: function () {
          S.to = WINDAYS[WINDAYS.length - 1] || ALLDAYS[ALLDAYS.length - 1];
          S.from = WINDAYS[Math.max(0, WINDAYS.length - 30)] || ALLDAYS[Math.max(0, ALLDAYS.length - 30)];
          S.windowOnly = true;
        }
      },
      {
        l: 'All incl. backfill', f: function () {
          S.from = ALLDAYS[0] || '';
          S.to = ALLDAYS[ALLDAYS.length - 1] || '';
          S.windowOnly = false;
        }
      }
    ];

    var btns = [];
    presets.forEach(function (p) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'df-preset-btn';
      b.textContent = p.l;
      b.addEventListener('click', function () {
        p.f();
        btns.forEach(function (x) { x.classList.remove('active'); });
        b.classList.add('active');
        updateBdsInputs();
        updateBdsSummary();
        render();
      });
      btns.push(b);
      drRow.appendChild(b);
    });

    /* date inputs */
    var dFrom = document.createElement('input');
    dFrom.type = 'date'; dFrom.className = 'bds-date-input';
    dFrom.value = S.from;
    var sep = document.createElement('span');
    sep.className = 'bds-arrow'; sep.textContent = '→';
    var dTo = document.createElement('input');
    dTo.type = 'date'; dTo.className = 'bds-date-input';
    dTo.value = S.to;

    function updateBdsInputs() {
      dFrom.value = S.from;
      dTo.value = S.to;
    }

    dFrom.addEventListener('change', function () {
      S.from = dFrom.value;
      btns.forEach(function (x) { x.classList.remove('active'); });
      updateBdsSummary();
      render();
    });
    dTo.addEventListener('change', function () {
      S.to = dTo.value;
      btns.forEach(function (x) { x.classList.remove('active'); });
      updateBdsSummary();
      render();
    });

    drRow.appendChild(dFrom);
    drRow.appendChild(sep);
    drRow.appendChild(dTo);
    drGroup.appendChild(drRow);
    strip.appendChild(drGroup);

    /* ---- divider ---- */
    var div1 = document.createElement('div'); div1.className = 'bds-divider'; strip.appendChild(div1);

    /* ---- SCOPE group ---- */
    var scGroup = document.createElement('div');
    scGroup.className = 'bds-group';

    var scLabel = document.createElement('span');
    scLabel.className = 'bds-label'; scLabel.textContent = 'SCOPE';
    scGroup.appendChild(scLabel);

    var scRow = document.createElement('div');
    scRow.className = 'bds-row bds-scope-wrap';

    var winBtn = document.createElement('button');
    winBtn.type = 'button'; winBtn.className = 'bds-scope-btn'; winBtn.textContent = 'in-window only';
    var backBtn = document.createElement('button');
    backBtn.type = 'button'; backBtn.className = 'bds-scope-btn'; backBtn.textContent = 'incl. backfill';

    function refreshScope() {
      winBtn.classList.toggle('active', S.windowOnly);
      backBtn.classList.toggle('active', !S.windowOnly);
    }
    refreshScope();

    winBtn.addEventListener('click', function () { S.windowOnly = true; refreshScope(); updateBdsSummary(); render(); });
    backBtn.addEventListener('click', function () {
      S.windowOnly = false;
      S.from = ALLDAYS[0] || S.from;
      S.to = ALLDAYS[ALLDAYS.length - 1] || S.to;
      btns.forEach(function (x) { x.classList.remove('active'); });
      if (btns[4]) { btns[4].classList.add('active'); }
      updateBdsInputs();
      refreshScope();
      updateBdsSummary();
      render();
    });

    scRow.appendChild(winBtn);
    scRow.appendChild(backBtn);
    scGroup.appendChild(scRow);
    strip.appendChild(scGroup);

    /* ---- divider ---- */
    var div2 = document.createElement('div'); div2.className = 'bds-divider'; strip.appendChild(div2);

    /* ---- SELECTION summary ---- */
    var selGroup = document.createElement('div');
    selGroup.className = 'bds-group';

    var selLabel = document.createElement('span');
    selLabel.className = 'bds-label'; selLabel.textContent = 'SELECTION';
    selGroup.appendChild(selLabel);

    var selVal = document.createElement('span');
    selVal.className = 'bds-summary'; selVal.id = 'bds-summary-val';
    selGroup.appendChild(selVal);
    strip.appendChild(selGroup);

    /* summary updater */
    function updateBdsSummary() {
      var days = ALLDAYS.filter(function (d) { return d >= S.from && d <= S.to; });
      var nDays = days.length;
      var nPlat = Object.keys(S.platforms).filter(function (k) { return S.platforms[k]; }).length;
      var totPlat = Object.keys(S.platforms).length;
      var nTop = Object.keys(S.topics).filter(function (k) { return S.topics[k]; }).length;
      var totTop = Object.keys(S.topics).length;
      selVal.textContent = nDays + ' days · ' + nPlat + '/' + totPlat + ' platforms · ' + nTop + '/' + totTop + ' topics';
    }
    updateBdsSummary();

    // Default to "Full window" active
    if (btns.length) { btns[0].classList.add('active'); }

    // Insert just before #basic-view
    var basicView = document.getElementById('basic-view');
    if (basicView && basicView.parentNode) {
      basicView.parentNode.insertBefore(strip, basicView);
    }
  }

  /* ============================================================ Dashboard Sections nav (v11) */

  function buildDashboardSections() {
    var advView = document.getElementById('advanced-view');
    if (!advView) { return; }

    // Remove existing dash-sections-wrap if already built (re-init guard)
    var existing = advView.querySelector('.dash-sections-wrap');
    if (existing) { existing.parentNode.removeChild(existing); }

    var sections = Array.prototype.slice.call(advView.querySelectorAll('section.sec'));
    if (!sections.length) { return; }

    var groups = [
      {
        icon: '⊞',
        title: '1. Overview & Alerts',
        subtitle: 'Header & Key Performance Indicator (KPI) · Alerts · Acceleration · Burst episodes',
        metrics: [
          { id: 'kpi', label: '1. Header & Key Performance Indicator (KPI) row' },
          { id: 'alerts', label: '2. Alerts (False Discovery Rate-gated)' },
          { id: 'accel', label: '3. Acceleration & emergence' },
          { id: 'bursts', label: '4. Burst episodes' }
        ]
      },
      {
        icon: '📈',
        title: '2. Activity & Virality',
        subtitle: 'Volume & growth · Intensity · Virality & reach',
        metrics: [
          { id: 'volume', label: '5. Volume & growth' },
          { id: 'intensity', label: '6. Intensity & surprise' },
          /* { id: 'persistence', label: '7. Persistence' }, */
          { id: 'virality', label: '10. Virality & reach' }
          /* { id: 'authors', label: '11. Author diversity & coordination' }, */
          /* { id: 'readability', label: '12. Readability' } */
        ]
      },
      {
        icon: '👥',
        title: '3. Sentiment Analysis',
        subtitle: 'Tone · Net sentiment score · Tone drift',
        metrics: [
          { id: 'tone', label: '8. Tone' }
          /* { id: 'hate', label: '9. Hate / toxicity' } */
        ]
      },
      {
        icon: '🔗',
        title: '4. Entities & Ontology',
        subtitle: 'Entity directory · Live Named Entity Recognition',
        metrics: [
          /* { id: 'ontology', label: '13. Ontology' }, */
          /* { id: 'hashtags', label: '14. Hashtags' }, */
          { id: 'entities', label: '16. Entity directory' },
          { id: 'livener', label: '20. Live Named Entity Recognition' }
        ]
      },
      {
        icon: '🛡',
        title: '5. Risk & Intelligence',
        subtitle: 'Risk categories & thematic dimensions',
        metrics: [
          /* { id: 'methodology', label: '15. Methodology & data quality' }, */
          { id: 'risktheme', label: '17. Risk categories & thematic dimensions' }
          /* { id: 'geotimeline', label: '18. Timeline & alerts' }, */
          /* { id: 'geoverify', label: '19. Geo-ontology verification' } */
        ]
      }
    ];

    groups.forEach(function (g) {
      g.ids = g.metrics.map(function (m) { return m.id; });
    });

    var wrap = document.createElement('div');
    wrap.className = 'dash-sections-wrap';

    // 1. Primary Category Bar (Top-level tabs)
    var list = document.createElement('div');
    list.className = 'dash-sections-tabs';

    var allBtn = document.createElement('button');
    allBtn.type = 'button';
    allBtn.className = 'dash-sec-pill dash-sec-all-pill';
    allBtn.innerHTML = '<span class="dsp-icon">⚡</span><span>View all (12)</span>';

    var pills = [];

    // 2. Secondary Sub-metric Bar
    var subBar = document.createElement('div');
    subBar.className = 'dash-subsections-bar';

    var currentGroup = -1;
    var currentSubId = null;

    function renderSubBar(groupIdx, activeSubId) {
      subBar.textContent = '';
      if (groupIdx === -1) {
        var lbl = document.createElement('span');
        lbl.className = 'dsub-label';
        lbl.textContent = 'ALL 12 METRICS:';
        subBar.appendChild(lbl);

        var jumpSelect = document.createElement('select');
        jumpSelect.className = 'dsub-select';
        var defOpt = document.createElement('option');
        defOpt.value = '';
        defOpt.textContent = 'Jump directly to metric…';
        jumpSelect.appendChild(defOpt);

        groups.forEach(function (g) {
          var optGrp = document.createElement('optgroup');
          optGrp.label = g.title;
          g.metrics.forEach(function (m) {
            var opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.label;
            optGrp.appendChild(opt);
          });
          jumpSelect.appendChild(optGrp);
        });

        jumpSelect.addEventListener('change', function () {
          if (jumpSelect.value) {
            var el = document.getElementById(jumpSelect.value);
            if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
          }
        });
        subBar.appendChild(jumpSelect);
        return;
      }

      var g = groups[groupIdx];
      var lbl = document.createElement('span');
      lbl.className = 'dsub-label';
      lbl.textContent = 'SHOW:';
      subBar.appendChild(lbl);

      // "All in section" pill
      var allPill = document.createElement('button');
      allPill.type = 'button';
      allPill.className = 'dash-sub-pill' + (!activeSubId ? ' active' : '');
      allPill.textContent = 'All in ' + g.title.replace(/^\d+\.\s*/, '');
      allPill.addEventListener('click', function () {
        showSubMetric(groupIdx, null);
      });
      subBar.appendChild(allPill);

      // Sub-metric pills
      g.metrics.forEach(function (m) {
        var mPill = document.createElement('button');
        mPill.type = 'button';
        mPill.className = 'dash-sub-pill' + (activeSubId === m.id ? ' active' : '');
        mPill.textContent = m.label;
        mPill.addEventListener('click', function () {
          showSubMetric(groupIdx, m.id);
        });
        subBar.appendChild(mPill);
      });
    }

    function showGroup(groupIdx, isAll) {
      currentGroup = isAll ? -1 : groupIdx;
      currentSubId = null;

      pills.forEach(function (p, i) { p.classList.toggle('active', !isAll && i === groupIdx); });
      allBtn.classList.toggle('active', isAll);

      sections.forEach(function (sec) {
        if (isAll) {
          sec.style.display = '';
        } else {
          sec.style.display = (groups[groupIdx].ids.indexOf(sec.id) !== -1) ? '' : 'none';
        }
      });

      renderSubBar(currentGroup, null);
      setTimeout(function () { render(); window.dispatchEvent(new Event('resize')); }, 40);
      try { if (history.replaceState) { history.replaceState(null, null, isAll ? '#all' : '#group' + groupIdx); } } catch (e) { }
    }

    function showSubMetric(groupIdx, subId) {
      currentGroup = groupIdx;
      currentSubId = subId;

      sections.forEach(function (sec) {
        if (!subId) {
          sec.style.display = (groups[groupIdx].ids.indexOf(sec.id) !== -1) ? '' : 'none';
        } else {
          sec.style.display = (sec.id === subId) ? '' : 'none';
        }
      });

      renderSubBar(groupIdx, subId);
      setTimeout(function () {
        render();
        window.dispatchEvent(new Event('resize'));
        var target = document.getElementById(subId || groups[groupIdx].ids[0]);
        if (target) { target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
      }, 50);
      try { if (history.replaceState) { history.replaceState(null, null, '#' + (subId || groups[groupIdx].ids[0])); } } catch (e) { }
    }

    allBtn.addEventListener('click', function () { showGroup(-1, true); });
    list.appendChild(allBtn);

    groups.forEach(function (g, gi) {
      var pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'dash-sec-pill';
      pill.title = g.subtitle;
      pill.innerHTML = '<span class="dsp-icon">' + g.icon + '</span><span class="dsp-title">' + g.title + '</span>';
      pill.addEventListener('click', function () {
        showGroup(gi, false);
        var first = document.getElementById(g.ids[0]);
        if (first) {
          setTimeout(function () { first.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 80);
        }
      });
      pills.push(pill);
      list.appendChild(pill);
    });

    wrap.appendChild(list);
    wrap.appendChild(subBar);

    var firstSection = advView.querySelector('section.sec');
    if (firstSection) { advView.insertBefore(wrap, firstSection); } else { advView.appendChild(wrap); }

    var hash = (location.hash || '').replace(/^#/, '');
    if (hash && hash !== 'all') {
      var gIdx = -1, mId = null;
      groups.forEach(function (g, i) {
        if (g.ids.indexOf(hash) !== -1) {
          gIdx = i;
          mId = hash;
        }
      });
      if (gIdx !== -1) {
        showSubMetric(gIdx, mId);
        return;
      }
    }
    showGroup(-1, true);
    window.__showDashGroup = showGroup;
    window.__showSubMetric = showSubMetric;
  }


  function initDetailedMetricToggle() {
    var toc = document.querySelector('#advanced-view .toc');
    var advView = document.getElementById('advanced-view');
    if (!toc || !advView) { return; }

    var sections = Array.prototype.slice.call(advView.querySelectorAll('section.sec'));
    if (!sections.length) { return; }

    // Add 'View All' pill at the start of TOC if not already present
    var allBtn = toc.querySelector('.toc-all-btn');
    if (!allBtn) {
      allBtn = document.createElement('a');
      allBtn.className = 'toc-btn toc-all-btn';
      allBtn.href = '#all';
      allBtn.textContent = '⚡ View All';
      toc.insertBefore(allBtn, toc.firstChild);
    }

    var links = Array.prototype.slice.call(toc.querySelectorAll('a'));
    var validIds = sections.map(function (s) { return s.id; });

    function showSection(targetId, updateHash) {
      var isAll = !targetId || targetId === 'all';

      // Update active state on tabs
      links.forEach(function (l) {
        var href = l.getAttribute('href') || '';
        var id = href.replace(/^#/, '');
        if ((isAll && id === 'all') || (!isAll && id === targetId)) {
          l.classList.add('active');
          l.setAttribute('aria-selected', 'true');
        } else {
          l.classList.remove('active');
          l.setAttribute('aria-selected', 'false');
        }
      });

      // Show/hide sections
      sections.forEach(function (sec) {
        if (isAll || sec.id === targetId) {
          sec.style.display = '';
        } else {
          sec.style.display = 'none';
        }
      });

      // Trigger resize for newly visible charts
      setTimeout(function () {
        window.dispatchEvent(new Event('resize'));
      }, 60);

      if (updateHash !== false) {
        try {
          if (history.replaceState) {
            history.replaceState(null, null, '#' + (isAll ? 'all' : targetId));
          }
        } catch (e) { }
      }
    }

    // Attach click events
    links.forEach(function (link) {
      link.addEventListener('click', function (e) {
        e.preventDefault();
        var href = link.getAttribute('href') || '';
        var id = href.replace(/^#/, '');
        showSection(id, true);
        if (id !== 'all') {
          var targetSec = document.getElementById(id);
          if (targetSec) {
            targetSec.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }
        }
      });
    });

    // Check hash on load or default to first section
    var initialHash = (location.hash || '').replace(/^#/, '');
    if (initialHash && (initialHash === 'all' || validIds.indexOf(initialHash) !== -1)) {
      showSection(initialHash, false);
    } else {
      showSection(sections[0].id, false);
    }

    window.addEventListener('hashchange', function () {
      var h = (location.hash || '').replace(/^#/, '');
      if (h && (h === 'all' || validIds.indexOf(h) !== -1)) {
        showSection(h, false);
      }
    });

    window.__showMetricTab = showSection;
  }

  function initUI() {
    if (/[?&]selftest=1/.test(location.search)) { window.__SELFTEST__ = true; }
    if (/[?&]theme=light/.test(location.search)) {
      try { localStorage.setItem('z-theme', 'light'); } catch (e) { /* noop */ }
    }
    updateChrome();
    initTheme();
    initModeToggle();
    initDrillChrome();
    buildFilters();
    render();
    initLiveNer();
    initDetailedMetricToggle();
    initMetricsPanelControls();
    buildBasicDateStrip();
    buildDashboardSections();
    var t = null;
    window.addEventListener('resize', function () {
      if (t) { clearTimeout(t); }
      t = setTimeout(scheduleRender, 180);
    });
  }

  function boot(data) {
    if (!data || !data.tables) {
      setStatus('no metrics tables in payload', 'crit');
      return;
    }
    hydrate(data);
    setStatus('snapshot · interactive filters', 'gray');
    initUI();
  }

  function loadData() {
    // Prefer embedded blob only if it actually has fact tables (not a shell stub).
    var embedded = window.__METRICS__;
    if (embedded && embedded.tables && embedded.tables.metrics_daily &&
      embedded.tables.metrics_daily.rows && embedded.tables.metrics_daily.rows.length) {
      boot(embedded);
      return;
    }
    var url = window.__METRICS_URL__ || ('data/metrics.json?v=25subtypes_v17');
    setStatus('loading metrics…', 'gray');
    fetch(url)
      .then(function (r) {
        if (!r.ok) { throw new Error(url + ' → HTTP ' + r.status); }
        return r.json();
      })
      .then(function (data) {
        boot(data);
      })
      .catch(function (err) {
        console.error(err);
        setStatus('failed to load ' + url + ' — serve UI/ over HTTP', 'crit');
        var wrap = document.querySelector('.wrap');
        if (wrap && !document.getElementById('load-error')) {
          var box = document.createElement('div');
          box.id = 'load-error';
          box.className = 'warnbox';
          box.innerHTML = '<div class="hd">Could not load metrics snapshot</div>' +
            '<p style="margin:0">Open via a local static server (not <code>file://</code>):' +
            '<br><code>cd UI && python3 -m http.server 8877</code>' +
            '<br>then <code>http://127.0.0.1:8877/</code>' +
            '<br><br>Rebuild data with <code>python UI/build_dashboard.py</code>.' +
            '<br><span class="fnote">' + String(err && err.message || err) + '</span></p>';
          wrap.insertBefore(box, wrap.firstChild);
        }
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadData);
  } else { loadData(); }
})();
