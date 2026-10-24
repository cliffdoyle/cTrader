// Shared helpers: New York time formatting, API access, header, notification polling (toasts + browser notifications).
const NY_FMT = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/New_York', weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const NY_SHORT = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/New_York', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
const DIGITS = { NAS100: 1, US30: 1, US500: 1, EURUSD: 5, GBPUSD: 5, AUDUSD: 5, USDCAD: 5, USDJPY: 3, XAUUSD: 2, GBPJPY: 3 };
const SYMBOLS = Object.keys(DIGITS);
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
  h.innerHTML = `<span class="brand">ICT Scanner</span>
    <nav><a href="/">Overview</a>${SYMBOLS.map(s => `<a href="/symbol/${s}">${s}</a>`).join('')}<a href="/backtest">Backtest</a></nav>
    <span class="spacer"></span>
    <span id="health" class="small muted"></span>
    <button class="btn" id="notif-perm" style="display:none">Enable pop-ups</button>
    <a href="/notifications">🔔 <span id="unread" class="badge" style="display:none">0</span></a>`;
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
      if (r.muted || n.read || n.level === 'info') continue;
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

function setupRow(s) {
  const plan = s.entry != null ? `<span class="mono">${s.entry} / ${s.stop} / ${s.tp1}</span> <span class="muted">(${s.rr ?? '?'}R)</span>` : '<span class="muted">waiting for confirmation</span>';
  return `<tr><td><a href="/setup/${encodeURIComponent(s.id)}">${esc(s.symbol)} ${esc(s.setup)}</a><div class="small muted">${esc(s.name)}</div></td>
    <td>${arrow(s.dir)}</td><td>${stage(s.stage)}<div class="small muted">${esc(s.last_ny)} NY</div></td>
    <td class="small">${esc(s.poi.kind)}<div class="mono">${s.poi.low} – ${s.poi.high}</div></td>
    <td class="small">${plan}${s.result_r != null ? `<div class="${s.result_r > 0 ? 'bull' : 'bear'}">${fmtR(s.result_r)}</div>` : ''}</td>
    <td>${gradeBadge(s)} ${s.score}/7</td><td class="small">${esc(s.last_note)}</td></tr>`;
}
const SETUP_HEAD = '<tr><th>Setup</th><th>Dir</th><th>Stage</th><th>POI</th><th>Entry / Stop / TP1</th><th>Grade / score</th><th>Latest</th></tr>';
