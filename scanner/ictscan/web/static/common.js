// Shared helpers: New York time formatting, API access, header, notification polling (toasts + browser notifications).
const NY_FMT = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/New_York', weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const NY_SHORT = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/New_York', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const DIGITS = { NAS100: 1, US30: 1, US500: 1, EURUSD: 5, GBPUSD: 5, AUDUSD: 5, USDCAD: 5, USDJPY: 3, XAUUSD: 2, GBPJPY: 3 };
const SYMBOLS = Object.keys(DIGITS);
// Indices shown in the header (keep in sync with DASHBOARD_SYMBOLS in config.py).
const NAV = ['NAS100', 'US30', 'US500'];
const NAMES = { NAS100: 'Nasdaq 100', US30: 'Dow Jones 30', US500: 'S&P 500' };
const TFS = ['w1', 'd1', 'h4', 'h1', 'm15', 'm5'];

function ny(v) { if (v == null) return ''; const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v); return NY_FMT.format(d) + ' NY'; }
function nyShort(v) { const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v); return NY_SHORT.format(d); }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function pill(state) { return `<span class="pill ${esc(state)}">${esc(state || '—')}</span>`; }
function stage(s) { return `<span class="stage ${esc(s)}">${esc(s)}</span>`; }
function arrow(dir) { return dir === 'bullish' ? '<span class="bull">▲ long</span>' : '<span class="bear">▼ short</span>'; }
function fmtR(r) { return r == null ? '' : (r > 0 ? '+' : '') + Number(r).toFixed(2) + 'R'; }
function gradeBadge(s) { return s.grade ? `<span class="grade g${esc(s.grade)}" title="${esc(s.grade_note || '')}">${esc(s.grade)}</span>` : ''; }
async function api(path, opts) { const r = await fetch(path, opts); if (!r.ok) throw new Error(`${r.status} ${path}`); return r.json(); }

function renderHeader() {
  const h = document.createElement('header');
  const here = location.pathname;
  const link = (href, label) => `<a href="${href}" class="${here === href ? 'on' : ''}">${label}</a>`;
  h.innerHTML = `<a class="brand" href="/"><span class="logo">◆</span><span>ICT Scanner<small>Market maker models · US indices</small></span></a>
    <nav>${link('/', 'Overview')}${link('/cycles', 'Time cycles')}${NAV.map(s => link(`/symbol/${s}`, s)).join('')}</nav>
    <span class="spacer"></span>
    <span id="live-badge" class="livebadge off" title="Prices stream every second from Pepperstone while the market is open">…</span>
    <span id="health" class="small muted"></span>
    <button class="btn" id="notif-perm" style="display:none">Enable pop-ups</button>
    <a class="bell" href="/notifications" title="Notifications">🔔<span id="unread" class="badge" style="display:none">0</span></a>`;
  document.body.prepend(h);
  const btn = h.querySelector('#notif-perm');
  if ('Notification' in window && Notification.permission === 'default') {
    btn.style.display = '';
    btn.onclick = () => Notification.requestPermission().then(() => { btn.style.display = 'none'; });
  }
  const t = document.createElement('div'); t.id = 'toasts'; document.body.appendChild(t);
}

function setUnread(n) { const b = document.getElementById('unread'); if (!b) return; b.textContent = n; b.style.display = n ? '' : 'none'; }
function setHealth(h) {
  const el = document.getElementById('health'); if (!el || !h) return;
  el.innerHTML = `<span class="dot ${esc(h.status)}"></span>${esc(h.status)} · last scan ${h.last_scan ? nyShort(h.last_scan) : '—'}`;
  el.title = h.message || '';
}

function toast(n) {
  const box = document.getElementById('toasts');
  const d = document.createElement('div'); d.className = 'toast ' + (n.level === 'result' ? 'result' : '');
  d.innerHTML = `<div class="t">${esc(n.title)}</div><div class="small">${esc(n.body)}</div><div class="small"><a href="/setup/${encodeURIComponent(n.setup_id)}">open setup →</a></div>`;
  box.appendChild(d); setTimeout(() => d.remove(), 20000);
}

async function pollNotifications() {
  const key = 'ictscan:lastNote';
  let last = Number(localStorage.getItem(key) || 0);
  try {
    if (!last) { const r = await api('/api/notifications?limit=1'); last = r.items.length ? r.items[0].id : 0; localStorage.setItem(key, last); setUnread(r.unread); return; }
    const r = await api(`/api/notifications?after_id=${last}&limit=50`);
    const fresh = r.items.sort((a, b) => a.id - b.id);
    for (const n of fresh) {
      last = Math.max(last, n.id);
      if (!(n.popup ?? !r.muted) || n.read || n.level === 'info') continue;
      toast(n);
      if ('Notification' in window && Notification.permission === 'granted') {
        const bn = new Notification(n.title, { body: n.body, tag: `${n.setup_id}:${n.stage}` });
        bn.onclick = () => { window.focus(); location.href = '/setup/' + encodeURIComponent(n.setup_id); };
      }
    }
    localStorage.setItem(key, last); setUnread(r.unread);
    const h = await api('/api/health'); setHealth(h.health);
  } catch (e) { console.warn(e); }
}

function startShell() { renderHeader(); pollNotifications(); setInterval(pollNotifications, 20000); }

// Live prices: polls /api/live every second while the tab is visible and hands the symbols map to `cb`.
let LIVE = {};
function startLive(cb) {
  let busy = false;
  const tick = async () => {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const d = await api('/api/live');
      LIVE = d.symbols || {};
      const anyLive = Object.values(LIVE).some(x => x.live);
      const b = document.getElementById('live-badge');
      if (b) { b.className = 'livebadge ' + (anyLive ? 'on' : 'off'); b.textContent = anyLive ? 'LIVE' : 'market closed'; }
      cb && cb(LIVE);
    } catch (e) { console.warn(e); } finally { busy = false; }
  };
  tick(); setInterval(tick, 1000);
}
function flash(el, now, prev) {
  if (!el || now == null) return;
  el.textContent = now;
  if (prev != null && now !== prev) { el.classList.remove('up', 'down'); void el.offsetWidth; el.classList.add(now > prev ? 'up' : 'down'); }
}

function dirTag(dir) { return dir === 'bullish' ? '<span class="dirtag buy">▲ BUY</span>' : '<span class="dirtag sell">▼ SELL</span>'; }
function modeChip(m) { return m ? `<span class="chip ${esc(m)}">${esc(m)}</span>` : ''; }
function scoreBar(score, n) { n = n || 7; return `<span class="scorebar" title="${score}/${n} checks">${Array.from({ length: n }, (_, i) => `<i class="${i < score ? 'on' : ''}"></i>`).join('')}</span>`; }
function rCell(r) { return r == null ? '<span class="muted">—</span>' : `<span class="res-r ${r > 0 ? 'bull' : 'bear'}">${fmtR(r)}</span>`; }

function signalCard(s) {
  const href = '/setup/' + encodeURIComponent(s.id);
  return `<div class="sig-card ${esc(s.dir)}">
    <div class="sig-top"><span class="sym">${esc(s.symbol)}</span>${dirTag(s.dir)}${modeChip(s.mode)}
      <span class="right">${stage(s.stage)}</span></div>
    <div class="sig-poi">${esc(s.poi.kind)} <span class="mono">${s.poi.low} – ${s.poi.high}</span></div>
    <div class="levels">
      <div class="lv entry"><div class="k">${s.dir === 'bullish' ? 'Buy limit' : 'Sell limit'}</div><div class="v mono">${s.entry ?? '—'}</div></div>
      <div class="lv stop"><div class="k">Stop</div><div class="v mono">${s.stop ?? '—'}</div></div>
      <div class="lv target"><div class="k">Target</div><div class="v mono">${s.tp1 ?? '—'}</div></div>
    </div>
    <div class="sig-meta"><span>R:R <b>${s.rr ?? '—'}</b></span><span>Checks ${scoreBar(s.score, s.checks)} <b>${s.score}/${s.checks || 7}</b></span>
      ${s.smt ? `<span title="${esc(s.smt)}">SMT <b>✓</b></span>` : ''}</div>
    <div class="sig-live" data-live="${esc(s.symbol)}" data-entry="${s.entry ?? ''}"></div>
    <div class="sig-note"><span>${esc(s.last_note)}</span><span class="dim" style="white-space:nowrap">${esc(s.last_ny)} NY · <a href="${href}">details →</a></span></div>
  </div>`;
}

function updateSignalLive(live) {
  document.querySelectorAll('.sig-live[data-live]').forEach(el => {
    const q = live[el.dataset.live], e = parseFloat(el.dataset.entry);
    if (!q || q.price == null || isNaN(e)) { el.innerHTML = ''; return; }
    const d = DIGITS[el.dataset.live] ?? 2, gap = q.price - e;
    el.innerHTML = `<span>now <b class="mono">${q.price.toFixed(d)}</b>${q.live ? '' : ' <span class="dim">(last close)</span>'}</span>
      <span class="mono">${Math.abs(gap).toFixed(d)} pts ${gap >= 0 ? 'above' : 'below'} entry</span>`;
  });
}

function resultRow(s) {
  return `<tr><td class="small muted" style="white-space:nowrap">${esc(s.first_ny || s.last_ny)}</td>
    <td><a href="/setup/${encodeURIComponent(s.id)}"><b>${esc(s.symbol)}</b></a></td>
    <td>${dirTag(s.dir)} ${modeChip(s.mode)}</td>
    <td class="mono small">${s.entry ?? '—'} <span class="dim">/</span> ${s.stop ?? '—'} <span class="dim">/</span> ${s.tp1 ?? '—'}</td>
    <td>${stage(s.stage)}</td><td>${rCell(s.result_r)}</td><td class="small muted">${esc(s.last_note)}</td></tr>`;
}
const RESULT_HEAD = '<tr><th>Signal (NY)</th><th>Index</th><th>Model</th><th>Entry / Stop / Target</th><th>Outcome</th><th>Result</th><th>Note</th></tr>';

function setupRow(s) {
  const plan = s.entry != null ? `<span class="mono">${s.entry} / ${s.stop} / ${s.tp1}</span> <span class="muted">(${s.rr ?? '?'}R)</span>` : '<span class="muted">waiting for confirmation</span>';
  return `<tr><td><a href="/setup/${encodeURIComponent(s.id)}"><b>${esc(s.symbol)}</b></a> ${modeChip(s.mode)}<div class="small muted">${esc(s.name)}</div></td>
    <td>${dirTag(s.dir)}</td><td>${stage(s.stage)}<div class="small muted">${esc(s.last_ny)} NY</div></td>
    <td class="small">${esc(s.poi.kind)}<div class="mono">${s.poi.low} – ${s.poi.high}</div></td>
    <td class="small">${plan}${s.result_r != null ? `<div>${rCell(s.result_r)}</div>` : ''}</td>
    <td>${scoreBar(s.score, s.checks)} <span class="small">${s.score}/${s.checks || 7}</span></td><td class="small muted">${esc(s.last_note)}</td></tr>`;
}
const SETUP_HEAD = '<tr><th>Signal</th><th>Direction</th><th>Stage</th><th>Reversal level</th><th>Entry / Stop / Target</th><th>Checks</th><th>Latest</th></tr>';

/* ---------- Draw on cTrader (through the scanner running on this PC) ---------- */
const BRIDGE = ['127.0.0.1', 'localhost'].includes(location.hostname) ? '' : 'http://127.0.0.1:8765';
const DRAW_LABEL = { cycles: 'PXH / PXL levels', boxes: '90-min boxes', levels: 'Session levels', smt: 'SMT', clear: 'Clear' };
let drawLink = null;
async function drawStatus(force) {
  if (drawLink && !force) return drawLink;
  try {
    const r = await fetch(BRIDGE + '/api/draw/status', { cache: 'no-store' });
    drawLink = r.ok ? await r.json() : { ok: false, error: `PC scanner answered ${r.status}` };
  } catch (e) {
    drawLink = { ok: false, offline: true, error: 'PC scanner not reachable' };
  }
  return drawLink;
}
function linkText(s, sym) {
  if (s.ok) {
    const mine = (s.charts || []).filter(c => c.symbol === sym);
    return `<span class="dot ok"></span>cTrader linked · ${mine.length ? `${sym} chart open (${mine.map(c => c.timeframe).join(', ')})` : `no ${sym} chart — one will open`}`;
  }
  if (s.offline) return '<span class="dot off"></span>PC link offline — run the ICT Scanner on your PC (and allow "local network access" if the browser asks)';
  return `<span class="dot warn"></span>${esc(s.error || 'cTrader not ready')} — open cTrader with the MCP server enabled`;
}
function drawTools(sym, compact) {
  if (compact) return `<div class="draw-tools compact" data-sym="${sym}">
      <span class="dt-k">Draw on cTrader</span>
      <button data-what="cycles" title="Week · Day · 90-min PXH / EQ / PXL">PXH/PXL</button>
      <button data-what="boxes" title="Today's six 90-minute windows">90-min</button>
      <button data-what="levels" title="PWH/PWL, PDH/PDL, Asia, London, NY AM, midnight open">Levels</button>
      <button data-what="smt" title="SMT divergence lines and labels">SMT</button>
      <button data-what="clear" class="ghost" title="Remove the scanner's drawings from ${sym} charts">✕</button>
      <div class="dt-out tiny"></div></div>`;
  return `<div class="draw-tools panel" data-sym="${sym}">
    <div class="dt-head"><b>Draw ${sym} on cTrader</b><span class="dt-status small muted">checking the PC link…</span></div>
    <div class="dt-row">
      <div class="dt-group"><span class="dt-k">PXH / PXL</span>
        ${[['week', 'Week', 1], ['day', 'Day', 1], ['90m', '90-min', 1], ['month', 'Month', 0]].map(([k, l, on]) =>
          `<label class="chk"><input type="checkbox" value="${k}" ${on ? 'checked' : ''}>${l}</label>`).join('')}
        <button data-what="cycles" class="primary">Draw levels</button></div>
      <button data-what="boxes">90-min boxes</button>
      <button data-what="levels">Session levels</button>
      <button data-what="smt">SMT divergence</button>
      <button data-what="clear" class="ghost">Clear scanner drawings</button>
    </div>
    <pre class="dt-out tiny"></pre>
    <div class="tiny dim">Draws on the ${sym} chart in cTrader (the active one, else your open ${sym} chart, else a new chart). Redrawing replaces
      only the scanner's own objects; your drawings are never touched.</div></div>`;
}
function initDrawTools(root) {
  root.querySelectorAll('.draw-tools').forEach(box => {
    const sym = box.dataset.sym, out = box.querySelector('.dt-out'), st = box.querySelector('.dt-status');
    if (st) drawStatus().then(s => st.innerHTML = linkText(s, sym));
    box.querySelectorAll('button[data-what]').forEach(btn => btn.onclick = async () => {
      const what = btn.dataset.what;
      if (what === 'clear' && !confirm(`Remove the scanner's drawings from your ${sym} charts?`)) return;
      const kinds = [...box.querySelectorAll('input[type=checkbox]:checked')].map(i => i.value);
      box.querySelectorAll('button').forEach(b => b.disabled = true);
      btn.classList.add('busy');
      out.className = out.className.replace(/ (ok|err)/g, '');
      out.textContent = `Drawing ${DRAW_LABEL[what]} on ${sym}…`;
      try {
        const r = await fetch(BRIDGE + '/api/draw', { method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ symbol: sym, what, kinds: kinds.length ? kinds : null }) });
        const d = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
        out.classList.add(d.ok ? 'ok' : 'err');
        out.textContent = d.ok ? (box.classList.contains('compact') ? d.lines[d.lines.length - 1] : d.lines.join('\n')) : `✕ ${d.error}`;
      } catch (e) {
        out.classList.add('err');
        out.textContent = '✕ Could not reach the scanner on this PC (http://127.0.0.1:8765). Start the "ICT Scanner" task, open cTrader, '
          + 'and allow local network access if the browser asks.';
      }
      btn.classList.remove('busy');
      box.querySelectorAll('button').forEach(b => b.disabled = false);
      if (st) drawStatus(true).then(s => st.innerHTML = linkText(s, sym));
    });
  });
}

/* ---------- SMT divergence ---------- */
const SMT_STATUS = { watch: ['watch for the 5m shift', 'warn'], confirmed: ['shift confirmed', 'ok'], cancelled: ['absorbed', 'dim'] };
function smtLabel(l) { const bad = l === 'HH' || l === 'LL'; return `<span class="smt-lab ${l.startsWith('EQ') ? 'eq' : bad ? 'take' : 'hold'}">${l}</span>`; }
function smtSection(smt, opts) {
  opts = opts || {};
  if (!smt || !smt.cycles) return '<div class="panel muted small">SMT appears after the next scan.</div>';
  const v = smt.verdict || {};
  const b = v.bias, t = v.trigger, sell = b && b.side === 'bearish';
  const step = (n, title, body, state) => `<div class="smt-step ${state}"><span class="n">${n}</span><div><b>${title}</b><div class="small">${body}</div></div></div>`;
  const verdict = `<div class="smt-verdict ${b ? b.side : 'none'} ${esc(v.state || 'none')}">
    <div class="smt-v-head">${b ? `<b class="${sell ? 'bear' : 'bull'}">${sell ? '▼ Bearish' : '▲ Bullish'} SMT</b>` : '<b class="muted">No SMT signal</b>'}
      <span class="small">${esc(v.summary || '')}</span></div>
    <div class="smt-steps">
      ${step(1, 'Direction — bigger SMT', b ? `${esc(b.name)} SMT ${b.side === 'bearish' ? '▼' : '▲'} · ${esc(b.text)} <span class="dim">(${esc(b.when)} NY)</span>`
        : 'none yet — needs a Week, Day or 90-min cycle SMT (or 1H swing)', b ? 'done' : 'todo')}
      ${step(2, 'Timing — 5m / 15m SMT the same way', t ? `${esc(t.name)} SMT ${t.side === 'bearish' ? '▼' : '▲'} at ${esc(t.when)} NY · ${esc(t.text)}`
        : (b ? 'waiting — watch the 5m chart near the swept level' : '—'), t ? 'done' : 'todo')}
      ${step(3, 'Entry — MMXM signal', b ? `${sell ? 'sell' : 'buy'} the ${sell ? 'weaker' : 'stronger'} index: <b>${esc((v.trade_on || []).join(' / '))}</b> — only when the scanner pops up the MMXM signal (5m shift + FVG)`
        : '—', 'todo')}
    </div>
    ${(v.against || []).length ? `<div class="tiny warn">⚠ ${esc(v.against[0].name)} SMT points the other way (${esc(v.against[0].side)}) — smaller size or wait.</div>` : ''}</div>`;
  const tiles = smt.cycles.map(c => {
    const s = c.smt[0], syms = Object.keys(c.per);
    const state = s ? `<span class="pill ${s.side}">${s.side === 'bearish' ? '▼ SMT at highs' : '▲ SMT at lows'}</span>`
      : `<span class="pill mixed">${esc(c.state === 'inside' ? 'no divergence' : c.state)}</span>`;
    const rows = syms.map(sym => { const p = c.per[sym];
      return `<tr><td>${sym}</td><td class="${p.took_high ? 'bull' : 'dim'}">${p.took_high ? '✓' : '✗'} <span class="mono">${p.pxh}</span></td>
        <td class="${p.took_low ? 'bear' : 'dim'}">${p.took_low ? '✓' : '✗'} <span class="mono">${p.pxl}</span></td></tr>`; }).join('');
    return `<div class="smt-tile ${s ? s.side : ''}"><div class="smt-tile-h"><b>${esc(c.label)}</b><span class="tiny dim">${esc(c.window)}${c.live ? '' : ' · ended'}</span>${state}</div>
      <table class="smt-tab"><tr><th></th><th>took PXH</th><th>took PXL</th></tr>${rows}</table>
      ${c.smt.map(x => `<div class="small ${x.side === 'bearish' ? 'bear' : 'bull'}">${esc(x.text)}</div>`).join('')}</div>`;
  }).join('');
  const swings = (smt.swings || []).slice(0, opts.max || 8);
  const list = swings.length ? swings.map(e => { const [st, cls] = SMT_STATUS[e.status] || [e.status, ''];
    return `<div class="smt-ev ${e.side} ${e.status}"><span class="chip">${e.tf.toUpperCase()}</span>
      <span class="${e.side === 'bearish' ? 'bear' : 'bull'}"><b>${e.side === 'bearish' ? '▼' : '▲'}</b></span>
      <span class="small mono">${esc(e.when)}</span>
      <span class="smt-labs">${Object.entries(e.per).map(([s, p]) => `<span>${s} ${smtLabel(p.label)}</span>`).join('')}</span>
      <span class="st ${cls}">${esc(st)}</span>
      <div class="tiny dim smt-ev-t">${esc(e.text)} — ${esc(e.trade)}</div></div>`; }).join('')
    : '<div class="muted small">No swing SMT in the last hours.</div>';
  const guide = `<div class="tiny dim smt-guide"><b>How SMT works:</b> the three indices usually move together. When one takes a high (or low) and the others
    refuse, the move is not supported — a reversal warning. Bigger SMT (Week › Day › 90-min/1H) gives the <b>direction</b>; a 5m/15m SMT the same way gives
    the <b>timing</b>; the MMXM signal is the <b>entry</b>. Trade the index that refused (it is the weak one at highs, the strong one at lows).</div>`;
  return `${verdict}<details class="smt-more" ${opts.open ? 'open' : ''}><summary>Details — cycle and swing SMT</summary>${guide}<div class="smt-tiles">${tiles}</div>
    <div class="panel smt-list"><div class="small muted" style="margin-bottom:6px">Swing SMT (M5 · M15 · H1) — newest first.
      <span class="smt-lab take">HH/LL</span> took the previous swing, <span class="smt-lab hold">LH/HL</span> failed to: that index is the weak/strong one.</div>${list}</div></details>`;
}
