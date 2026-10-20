'use strict';
/* J1 Mesh dashboard. Every dynamic value is inserted with textContent / createTextNode:
   peer hostnames come from other devices on your meshes and must never be treated as HTML. */

const $ = (sel) => document.querySelector(sel);
const SVGNS = 'http://www.w3.org/2000/svg';

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else el.setAttribute(k, String(v));
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const fmt = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const latClass = (ms) => (ms == null ? 'lat-na' : ms <= 40 ? 'lat-fast' : ms <= 120 ? 'lat-mid' : 'lat-slow');

let state = null;
let filter = null; // provider id or null for all
let lastOk = 0;

function sparkline(values) {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('class', 'spark');
  svg.setAttribute('viewBox', '0 0 100 30');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'median latency, recent history');
  const pts = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (pts.length < 2) return svg;
  const ys = pts.map(([, v]) => v);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const span = Math.max(hi - lo, 1);
  const n = Math.max(values.length - 1, 1);
  const line = document.createElementNS(SVGNS, 'polyline');
  line.setAttribute(
    'points',
    pts.map(([i, v]) => `${((i / n) * 100).toFixed(2)},${(28 - ((v - lo) / span) * 26).toFixed(2)}`).join(' ')
  );
  svg.append(line);
  return svg;
}

function renderRace(s) {
  const root = $('#race');
  const provs = s.providers;
  const ranked = s.ranking.map((id) => provs.find((p) => p.id === id)).filter(Boolean);
  const others = provs.filter((p) => !s.ranking.includes(p.id));
  const best = ranked.length ? ranked[0].stats.median : null;
  const kids = [];

  ranked.forEach((p, i) => {
    const width = best && p.stats.median ? Math.max(4, (best / p.stats.median) * 100) : 4;
    const bar = h('div', { class: 'bar' }, h('i'));
    bar.firstChild.style.width = width.toFixed(1) + '%';
    kids.push(
      h('div', { class: 'row' + (i === 0 ? ' first' : '') },
        h('span', { class: 'rank' }, '#' + (i + 1)),
        h('span', { class: 'nm' }, p.name),
        bar,
        h('span', { class: 'ms ' + latClass(p.stats.median) }, fmt(p.stats.median) + ' ms'),
        h('span', { class: 'meta' }, `p95 ${fmt(p.stats.p95)} · ${p.stats.direct}d/${p.stats.relay}r`)
      )
    );
  });

  others.forEach((p) => {
    kids.push(
      h('div', { class: 'row off' },
        h('span', { class: 'rank' }, '–'),
        h('span', { class: 'nm' }, p.name),
        h('div', { class: 'bar' }, h('i')),
        h('span', { class: 'ms lat-na' }, '—'),
        h('span', { class: 'meta' }, p.state)
      )
    );
  });

  if (s.baseline && s.baseline.stats.median != null) {
    const b = s.baseline;
    kids.push(
      h('div', { class: 'row ref' },
        h('span', { class: 'rank' }, '·'),
        h('span', { class: 'nm' }, 'Direct internet'),
        h('div', { class: 'bar' }, h('i')),
        h('span', { class: 'ms lat-na' }, fmt(b.stats.median) + ' ms'),
        h('span', { class: 'meta' }, 'reference · ' + (b.peers[0] ? b.peers[0].name : ''))
      )
    );
    kids[kids.length - 1].querySelector('.bar > i').style.width = '100%';
  }

  if (!kids.length) kids.push(h('div', { class: 'empty' }, 'No providers enabled. Check J1_MESH_PROVIDERS.'));
  root.replaceChildren(...kids);
}

function renderCards(s) {
  const cards = s.providers.map((p) => {
    const st = p.stats;
    const best = s.fastest === p.id;
    const head = h('header', null,
      h('span', { class: 'title' }, p.name),
      h('span', { class: 'state ' + p.state }, p.state)
    );
    const body = [head];
    if (p.state === 'connected') {
      body.push(h('div', { class: 'big ' + latClass(st.median) }, fmt(st.median), h('small', null, 'ms median')));
      body.push(
        h('div', { class: 'kv' },
          h('span', null, 'min ', h('b', null, fmt(st.min))),
          h('span', null, 'avg ', h('b', null, fmt(st.avg))),
          h('span', null, 'p95 ', h('b', null, fmt(st.p95))),
          h('span', null, 'loss ', h('b', null, st.loss == null ? '—' : st.loss + '%')),
          h('span', null, 'peers ', h('b', null, `${st.ok}/${st.n}`)),
          h('span', null, 'direct ', h('b', null, `${st.direct}/${st.direct + st.relay}`)),
          h('span', null, 'iface ', h('b', null, p.iface || '—')),
          h('span', null, 'ip ', h('b', null, p.self_ip || '—'))
        )
      );
      body.push(sparkline((s.series && s.series[p.id]) || []));
    } else if (p.state === 'missing') {
      body.push(h('div', { class: 'err' }, 'Not installed. Run: sudo j1-mesh install ' + p.id));
    }
    if (p.error && p.state !== 'connected') body.push(h('div', { class: 'err' }, p.error));
    return h('article', { class: 'card' + (best ? ' best' : '') }, ...body);
  });
  $('#cards').replaceChildren(...cards);
}

function renderPeers(s) {
  const chips = $('#chips');
  const ids = s.providers.filter((p) => p.state === 'connected').map((p) => p.id);
  if (filter && !ids.includes(filter)) filter = null;
  const mk = (id, label) => {
    const b = h('button', { class: 'chip', type: 'button', 'aria-pressed': String(filter === id) }, label);
    b.addEventListener('click', () => { filter = id; render(); });
    return b;
  };
  chips.replaceChildren(mk(null, 'All'), ...s.providers.filter((p) => ids.includes(p.id)).map((p) => mk(p.id, p.name)));

  const rows = [];
  s.providers.forEach((p) => {
    if (p.state !== 'connected' || (filter && filter !== p.id)) return;
    p.peers.forEach((peer) => rows.push({ mesh: p.name, ...peer }));
  });
  rows.sort((a, b) => (a.latency_ms ?? 1e9) - (b.latency_ms ?? 1e9));

  const body = rows.length
    ? rows.map((r) =>
        h('tr', null,
          h('td', null, r.mesh),
          h('td', null, r.name),
          h('td', null, r.ip || '—'),
          h('td', { class: 'num ' + latClass(r.latency_ms) }, r.latency_ms == null ? '—' : fmt(r.latency_ms) + ' ms'),
          h('td', null, h('span', { class: 'path ' + (r.path || '') }, r.path || 'unknown'))
        )
      )
    : [h('tr', null, h('td', { colspan: 5, class: 'empty' }, 'No peers online on connected meshes.'))];
  $('#peers tbody').replaceChildren(...body);
}

function renderHeader(s) {
  const up = s.providers.filter((p) => p.state === 'connected').length;
  const fastest = s.providers.find((p) => p.id === s.fastest);
  $('#sub').textContent =
    `${s.host} · ${up}/${s.providers.length} meshes up` + (fastest ? ` · fastest: ${fastest.name}` : '');
  $('#foot').textContent = `j1-mesh-stack v${s.version} · refresh ${s.interval}s`;
  document.title = fastest ? `J1 Mesh · ${fastest.name} ${fmt(fastest.stats.median)}ms` : 'J1 Mesh';
}

function setLive(cls, text) {
  const el = $('#live');
  el.className = 'live ' + cls;
  $('#live-text').textContent = text;
}

function render() {
  if (!state) return;
  renderHeader(state);
  renderRace(state);
  renderCards(state);
  renderPeers(state);
}

async function tick() {
  try {
    const res = await fetch('/api/state', { cache: 'no-store' });
    if (res.status === 503) {
      setLive('stale', 'warming up…');
    } else if (!res.ok) {
      throw new Error('HTTP ' + res.status);
    } else {
      state = await res.json();
      lastOk = Date.now();
      render();
    }
  } catch (e) {
    setLive('down', 'dashboard unreachable');
  } finally {
    if (state) {
      const age = (Date.now() - state.ts * 1000) / 1000;
      if (age > state.interval * 3 + 5) setLive('stale', `stale · ${Math.round(age)}s old`);
      else if (Date.now() - lastOk < 6000) setLive('ok', `live · ${new Date(state.ts * 1000).toLocaleTimeString()}`);
    }
    setTimeout(tick, 2000);
  }
}

tick();
