/* PromptMeter UI — vanilla JS, no build step. */

const S = { route: 'plan', id: null, status: null, models: [], vendors: [],
            efforts: ['none','low','medium','high','max'], model: '',
            effort: 'medium', surface: 'terminal', plans: [], oracles: [], cache: {},
            csrfToken: null };

/* ------------------------------------------------------------------ api */

async function api(path, opts = {}) {
  const method = opts.method || 'GET';
  const headers = { 'Content-Type': 'application/json' };
  // Every mutating request needs the per-process token the server hands out
  // on GET /api/status — a forged cross-origin POST cannot set a custom
  // header, so this is what makes that request rejected rather than acted on.
  if (method !== 'GET' && S.csrfToken) headers['X-PromptMeter-Token'] = S.csrfToken;
  let r;
  try {
    r = await fetch(path, {
      method,
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    // fetch() only rejects when nothing answered at all — surface that in words
    // instead of the browser's bare "Failed to fetch".
    throw new Error('Can’t reach PromptMeter — is it still running? Start it again and retry.');
  }
  const j = await r.json().catch(() => ({ error: 'Bad response' }));
  // The token is per server process. Restarting PromptMeter with a tab left
  // open made every action fail with "Missing or wrong CSRF token" until a
  // manual reload — pick up the new token and retry the request once instead.
  if (r.status === 403 && /CSRF/.test(j.error || '') && !opts._retried) {
    const s = await fetch('/api/status').then(x => x.json()).catch(() => null);
    if (s && s.csrf_token && s.csrf_token !== S.csrfToken) {
      S.csrfToken = s.csrf_token;
      return api(path, { ...opts, _retried: true });
    }
  }
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

function toast(msg, isErr) {
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const el = document.createElement('div');
  el.className = 'toast' + (isErr ? ' err' : '');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

/* ------------------------------------------------------------- helpers */

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const usd = n => (n == null ? '—' : (n < 0.01 && n > 0 ? '<$0.01' : '$' + Number(n).toFixed(2)));
const pct = n => (n == null ? '—' : Number(n).toFixed(0) + '%');
const num = n => (n == null ? '—' : Number(n).toLocaleString());

function tokens(n) {
  if (n == null) return '—';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

function dur(sec) {
  if (sec == null) return '—';
  sec = Math.max(0, sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  if (h >= 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function when(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000), now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (sameDay) return 'today ' + time;
  const tmr = new Date(now.getTime() + 86400000);
  if (d.toDateString() === tmr.toDateString()) return 'tomorrow ' + time;
  return d.toLocaleDateString([], { weekday: 'short' }) + ' ' + time;
}

function ago(ts) {
  if (!ts) return '—';
  const s = Date.now() / 1000 - ts;
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
}

const severity = p => (p == null ? '' : p >= 90 ? 'critical' : p >= 75 ? 'serious' : p >= 50 ? 'warning' : '');

const BAND = {
  green:    { label: 'Low risk',      cls: 'good',     color: 'var(--good)' },
  amber:    { label: 'Moderate risk', cls: 'warning',  color: 'var(--warning)' },
  red:      { label: 'High risk',     cls: 'serious',  color: 'var(--serious)' },
  critical: { label: 'Critical risk', cls: 'critical', color: 'var(--critical)' },
};

function riskChip(band, p) {
  const b = BAND[band] || BAND.green;
  return `<span class="chip ${b.cls}"><span class="swatch" style="background:${b.color}"></span>${b.label}${
    p != null ? ' · ' + (p * 100).toFixed(0) + '%' : ''}</span>`;
}

const STATUS_COLOR = {
  done: 'var(--good)', running: 'var(--accent)', failed: 'var(--critical)',
  skipped: 'var(--text-muted)', pending: 'var(--baseline)',
};

// Categorical slots assigned to model tiers in fixed order — a tier always keeps
// its colour, so a filter that removes one never repaints the others.
const TIER_COLOR = { opus: 'var(--series-1)', sonnet: 'var(--series-2)', haiku: 'var(--series-3)' };

// Matches providers.ENV_KEYS on the Python side — display only.
const ENV_KEY_NAMES = { anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', gemini: 'GEMINI_API_KEY' };

function modelColor(id) {
  const m = S.models.find(x => x.id === id);
  return TIER_COLOR[m && m.tier] || 'var(--series-4)';
}

function updateSidebarTagline(pv) {
  // The sidebar's "local · no API key" claim is only true in the default
  // heuristic mode — once a provider key is configured, real prompt text is
  // sent to that provider to draft a plan, and the persistent chrome
  // shouldn't keep claiming otherwise.
  const el = document.getElementById('brand-tagline');
  if (!el || !pv) return;
  const connected = pv.active && pv.active !== 'heuristic' && pv.keys && pv.keys[pv.active];
  el.textContent = connected ? `connected via ${pv.active}` : 'local · no API key';
}

function modelChip(id, label) {
  return `<span class="chip"><span class="swatch" style="background:${modelColor(id)}"></span>${esc(label || modelLabel(id))}</span>`;
}

/* ------------------------------------------------------- components */

function ring(pctVal, size = 92, label = '', arm = false) {
  const r = (size - 12) / 2, c = 2 * Math.PI * r, mid = size / 2;
  const v = pctVal == null ? 0 : Math.min(100, Math.max(0, pctVal));
  const sev = severity(v);
  const color = sev ? `var(--${sev})` : 'var(--accent)';
  // Gauge ticks — the one deliberately literal "meter" touch in the whole
  // app, used only here, on the two numbers the entire product exists to
  // show. Twelve marks like a dial face, drawn just inside the ring (not
  // outside it) so a high reading's arc never paints over them and they
  // never risk clipping against the viewBox edge. Every third one heavier.
  const ticks = Array.from({ length: 12 }, (_, i) => {
    const a = (i / 12) * 2 * Math.PI - Math.PI / 2;
    const x1 = (mid + (r - 9) * Math.cos(a)).toFixed(1), y1 = (mid + (r - 9) * Math.sin(a)).toFixed(1);
    const x2 = (mid + (r - 5) * Math.cos(a)).toFixed(1), y2 = (mid + (r - 5) * Math.sin(a)).toFixed(1);
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--baseline)"
      stroke-width="${i % 3 === 0 ? 1.4 : 0.8}"/>`;
  }).join('');
  // The arc sweeps in from empty on the very first paint of a browser session
  // (armGaugeSweep(), called once from render()) — everywhere else it just
  // renders at its true offset. `arm` only ever comes from the two Windows-
  // screen instruments; every other caller gets the plain, unanimated ring.
  const finalOffset = c * (1 - v / 100);
  const arcAttrs = arm
    ? `class="ring-arc" data-final-offset="${finalOffset}" style="stroke-dashoffset:${c}"`
    : `style="stroke-dashoffset:${finalOffset}"`;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img"
      aria-label="${esc(label)} ${pctVal == null ? 'unknown' : v.toFixed(0) + ' percent used'}">
    <circle cx="${mid}" cy="${mid}" r="${r}" fill="none" stroke="var(--track)" stroke-width="9"/>
    <g opacity="0.6">${ticks}</g>
    ${pctVal == null ? '' : `<circle cx="${mid}" cy="${mid}" r="${r}" fill="none" stroke="${color}"
      stroke-width="9" stroke-linecap="round" stroke-dasharray="${c}"
      ${arcAttrs} transform="rotate(-90 ${mid} ${mid})"/>`}
    <text x="${mid}" y="${mid + 1}" text-anchor="middle" dominant-baseline="middle"
      font-size="${size * 0.23}" font-weight="600" fill="var(--text-primary)"
      font-family="var(--mono)">${pctVal == null ? '–' : Math.round(v)}</text>
    <text x="${mid}" y="${mid + size * 0.19}" text-anchor="middle"
      font-size="${size * 0.11}" fill="var(--text-muted)" font-family="var(--font)">used %</text>
  </svg>`;
}

// Fires once per browser session (sessionStorage-gated, per Gemini's review
// of this redesign — a re-render on every route change must not replay it),
// and degrades to an instant jump under prefers-reduced-motion for free,
// since the global rule at the top of styles.css already zeroes every
// transition-duration on the page. Double rAF: one frame to let the browser
// paint the "empty" starting state committed in the HTML string above,
// a second to actually flip the value so the transition has something to
// animate from.
function armGaugeSweep() {
  if (sessionStorage.getItem('pm-booted')) return;
  sessionStorage.setItem('pm-booted', '1');
  const targets = [...document.querySelectorAll('.ring-arc[data-final-offset]'),
                    ...document.querySelectorAll('.spark-anim.pre')];
  if (!targets.length) return;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.querySelectorAll('.ring-arc[data-final-offset]').forEach(el => {
      el.style.strokeDashoffset = el.dataset.finalOffset;
    });
    document.querySelectorAll('.spark-anim.pre').forEach(el => el.classList.remove('pre'));
  }));
}

function meter(usedPct, leftLabel, rightLabel) {
  if (usedPct == null) {
    return `<div class="meter">
      <div class="meter-track unknown" role="img" aria-label="no reading yet"></div>
      <div class="meter-legend"><span>no reading yet</span><span>${rightLabel || ''}</span></div>
    </div>`;
  }
  const v = Math.min(100, Math.max(0, usedPct));
  const sev = severity(v);
  return `<div class="meter">
    <div class="meter-track"><div class="meter-fill ${sev}" style="width:${v}%"></div></div>
    <div class="meter-legend"><span>${leftLabel || ''}</span><span>${rightLabel || ''}</span></div>
  </div>`;
}

function progress(p, done) {
  const v = Math.min(100, Math.max(0, (p || 0) * 100));
  return `<div class="progress-track"><div class="progress-fill ${done ? 'done' : ''}" style="width:${v}%"></div></div>`;
}

function spark(points, w = 260, h = 44, key = 'pct7', arm = false) {
  if (!points || points.length < 2) return `<div class="small muted">Not enough history yet.</div>`;
  const xs = points.map(p => p.ts), ys = points.map(p => p[key] || 0);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y1 = Math.max(100, ...ys);
  const X = t => 2 + (t - x0) / Math.max(1, x1 - x0) * (w - 4);
  const Y = v => h - 2 - (v / y1) * (h - 6);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${X(p.ts).toFixed(1)},${Y(p[key] || 0).toFixed(1)}`).join('');
  const last = points[points.length - 1];
  // Draw-in via a clip-path reveal rather than measuring the path's real
  // length (getTotalLength needs the node live in the DOM, which a string of
  // HTML isn't yet) — a left-to-right wipe reads the same as a stroke
  // drawing itself in, on any path shape, with no extra DOM round-trip.
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-label="Weekly window used over time">
    <line x1="0" y1="${h - 2}" x2="${w}" y2="${h - 2}" stroke="var(--grid)" stroke-width="1"/>
    <g class="spark-anim${arm ? ' pre' : ''}">
      <path class="spark-path" d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"
        stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${X(last.ts).toFixed(1)}" cy="${Y(last[key] || 0).toFixed(1)}" r="4"
        fill="var(--accent)" stroke="var(--surface-1)" stroke-width="2"/>
    </g>
  </svg>`;
}

/* ---------------------------------------------------------- dashboard */

function activeProjectsCard(s) {
  return `<div class="card mt16">
    <div class="card-head"><h2>Active projects</h2>
      <button class="btn-sm" onclick="go('projects')">See all</button></div>
    ${s.active_projects.length ? s.active_projects.map(p => `
      <div style="padding:9px 0;border-bottom:1px solid var(--grid)">
        <div class="spread">
          <a href="#" onclick="goProject(${p.id});return false"><strong>${esc(p.name)}</strong></a>
          <span class="small muted tnum">${p.done}/${p.total} steps · ${usd(p.spent)}</span>
        </div>
        <div class="mt8">${progress(p.progress, p.progress >= 1)}</div>
      </div>`).join('')
    : `<div class="empty"><div class="big">No active projects</div>
        <div class="small">Plan a prompt to create one.</div>
        <div class="mt12"><button class="btn-primary" onclick="go('plan')">Plan a prompt</button>
        <button style="margin-left:8px" onclick="seedDemo()">Load sample data</button></div></div>`}
  </div>`;
}

async function setWindowsProvider(v) {
  try {
    await api('/api/settings', { method: 'PATCH', body: { windows_provider: v } });
    S.status = null;
    render();
  } catch (e) { toast(e.message, true); }
}

// Claude window tracking is the assumed default only after it's actually
// confirmed — showing 5-hour/weekly percentages by default regardless of
// what the person is using PromptMeter for was a real complaint. A fresh
// install (wp === '') asks once; wp === 'other' explains why there's
// nothing to track instead of showing Claude numbers anyway.
// What every agent has actually cost, by model — the part of this screen that
// means something whichever assistant you use. Sources: Claude Code sessions
// tailed automatically, plus anything an agent reported (POST /api/usage or
// `python -m promptmeter --log-usage`).
function usageByModelCard(u) {
  const rows = (u && u.models) || [];
  return `<div class="card mt16">
    <div class="card-head"><h2>Usage by model</h2>
      <span class="hint">every agent — tracked sessions and reported turns</span></div>
    ${rows.length ? `<table>
      <thead><tr><th>Model</th><th>From</th><th class="num">Turns</th><th class="num">Input</th>
        <th class="num">Output</th><th class="num">Cost</th></tr></thead>
      <tbody>${rows.map(m => `<tr>
        <td>${esc(m.label)}${m.known ? '' : ' <span class="chip">unpriced</span>'}
          <div class="small muted mono">${esc(m.model)}</div></td>
        <td class="small muted">${m.sources.map(x => x === 'transcript' ? 'Claude Code' : 'reported').join(' + ')}</td>
        <td class="num">${num(m.turns)}</td><td class="num">${tokens(m.in_tokens + m.cache_read)}</td>
        <td class="num">${tokens(m.out_tokens)}</td><td class="num">${usd(m.cost)}</td></tr>`).join('')}</tbody></table>
      ${rows.some(m => !m.known) ? `<div class="small muted mt8">“Unpriced” models are costed with a neutral
        fallback. Add their real price under Setup → Your own models.</div>` : ''}`
    : `<div class="empty"><div class="big">No usage yet</div>
        <div class="small">Any agent can report what it spends — Codex, Gemini CLI, Cursor, Aider, a script.
          Claude Code sessions are picked up on their own.</div>
        <div class="mt12"><button class="btn-primary" onclick="go('setup')">Connect an agent</button></div></div>`}
  </div>`;
}

// Codex (ChatGPT plan) and Gemini plans meter usage too, but those limits are
// not published as stable numbers — so nothing is assumed: you enter what your
// plan allows, and reported usage is measured against it over a rolling window.
function hoursLabel(h) { return h === 168 ? '7 days' : h + ' hours'; }

function providerLimitCard(p) {
  return `<div class="card mt16">
    <div class="card-head"><h2>${esc(p.label)}</h2>
      <span class="hint">${p.configured ? 'measured from usage reported to PromptMeter' : 'enter your plan’s limit to track it'}</span></div>
    ${p.windows.map(w => `<div class="limit-row">
      <div class="spread"><strong>${esc(w.name)} window</strong>
        <span class="small muted tnum">${w.limit
          ? `${w.unit === 'usd' ? usd(w.used) : num(w.used)} of ${w.unit === 'usd' ? usd(w.limit) : num(w.limit)}
             ${w.unit === 'usd' ? '' : 'requests'} · last ${esc(hoursLabel(w.hours))}`
          : `${num(w.turns)} request${w.turns === 1 ? '' : 's'} · ${usd(w.usd)} in the last ${esc(hoursLabel(w.hours))}`}</span></div>
      ${w.limit
        ? meter(w.pct, 'used ' + pct(w.pct), w.frees_at ? 'room frees ' + when(w.frees_at) : '')
        : `<div class="meter"><div class="meter-track unknown" role="img" aria-label="no limit set"></div>
             <div class="meter-legend"><span>no limit set</span><span></span></div></div>`}
      <div class="row mt8" style="gap:8px;flex-wrap:wrap">
        <input id="lim-${esc(p.provider)}-${esc(w.id)}" type="number" min="0" step="any" style="max-width:160px"
          placeholder="your limit" aria-label="${esc(p.label)} ${esc(w.name)} limit" value="${w.limit == null ? '' : esc(String(w.limit))}">
        <select id="lim-${esc(p.provider)}-${esc(w.id)}-u" style="max-width:190px" aria-label="unit">
          <option value="turns" ${w.unit === 'turns' ? 'selected' : ''}>requests</option>
          <option value="usd" ${w.unit === 'usd' ? 'selected' : ''}>dollars (list price)</option>
        </select>
      </div>
    </div>`).join('')}
    <div class="row mt12"><button class="btn-primary btn-sm" onclick="saveLimits('${esc(p.provider)}')">Save these limits</button></div>
    <div class="small muted mt8" style="max-width:72ch">A rolling window over usage reported to PromptMeter, not your provider's own reset
      clock. Leave a box blank to stop tracking that window.</div>
  </div>`;
}

function providerLimitCards(lim) {
  return ((lim && lim.providers) || []).map(providerLimitCard).join('');
}

async function saveLimits(provider) {
  const prefix = 'lim-' + provider + '-';
  const windows = {};
  document.querySelectorAll('input[id^="' + prefix + '"]').forEach(inp => {
    const wid = inp.id.slice(prefix.length);
    const unit = (document.getElementById(inp.id + '-u') || {}).value || 'turns';
    windows[wid] = { limit: inp.value, unit };
  });
  try {
    await api('/api/limits', { method: 'POST', body: { provider, windows } });
    toast('Limits saved.');
    render();
  } catch (e) { toast(e.message, true); }
}

function subscriptionIntro() {
  return `<div class="scallop"></div>
  <h2 class="section-title">Subscription limits</h2>
  <p class="small muted" style="margin:-8px 0 0;max-width:74ch">Track the plans you actually pay for. Claude's windows are read from
    Claude Code; for Codex and Gemini you enter what your plan allows.</p>`;
}

// Claude's 5-hour / weekly plan windows are one integration, not the app's
// identity: they exist only for a Claude subscription, so they sit behind an
// opt-in and everything else on this screen works without them.
function planWindowOptIn(wp) {
  return `<div class="card mt16">
    <div class="card-head"><h2>Claude (Pro / Max)</h2>
      <span class="hint">optional — read from Claude Code</span></div>
    <p class="small muted" style="max-width:64ch">A Claude Pro or Max subscription enforces a rolling 5-hour and a
      weekly limit, and this app can track how much of each you have used from Claude Code's sessions.
      Codex and Gemini plans are below.</p>
    <div class="row mt12">
      <button class="btn-primary btn-sm" onclick="setWindowsProvider('claude')">I have a Claude plan — track it</button>
      ${wp === 'other' ? '' : `<button class="btn-sm" onclick="setWindowsProvider('other')">Not for me</button>`}
    </div>
  </div>`;
}

function renderWindowsGate(s, wp, u, lim) {
  return `
  <div class="page-head">
    <div><h1>Usage</h1><div class="sub">What your AI agents have cost, whichever ones you use.</div></div>
    <button class="btn-sm" onclick="go('plan')">Plan a prompt</button>
  </div>
  ${usageByModelCard(u)}
  ${subscriptionIntro()}
  ${wp === 'other' ? '' : planWindowOptIn(wp)}
  ${wp === 'other' ? `<p class="small muted mt12">Using a Claude subscription?
    <a href="#" onclick="setWindowsProvider('claude');return false">Turn on Claude plan-window tracking</a>.</p>` : ''}
  ${providerLimitCards(lim)}
  ${activeProjectsCard(s)}`;
}

async function viewDashboard() {
  const s = await api('/api/status');
  S.status = s;
  const wp = (s.settings || {}).windows_provider || '';
  const u = await api('/api/usage/summary').catch(() => ({ models: [], turns: 0, cost: 0 }));
  const lim = await api('/api/limits').catch(() => ({ providers: [] }));
  if (wp !== 'claude') return renderWindowsGate(s, wp, u, lim);
  const hist = await api('/api/meter/history?hours=30').catch(() => ({ points: [] }));
  const f = s.five_hour, w = s.seven_day;
  const unmetered = f.used == null && w.used == null;
  // True first run: no reading of any kind AND not one project ever created —
  // totals.projects counts sample data too, so loading the demo also clears
  // this. Fixes the #1-ranked backlog gap (ARCHITECTURE.md §12, "P1 — there
  // is no first run"): a new user used to land on hatched empty bars with no
  // idea what the app measures or what to do next.
  const firstRun = unmetered && ((s.totals || {}).projects || 0) === 0;

  const heroLine = (() => {
    if (unmetered) return `No reading yet`;
    if (f.used != null && f.used >= 90) return `Session window nearly spent`;
    if (s.exhausts_at && w.remaining > 0) {
      const before = s.seven_day.resets_at && s.exhausts_at < s.seven_day.resets_at;
      return before ? `Runs out ${when(s.exhausts_at)}` : `Lasts to the weekly reset`;
    }
    return w.remaining <= 0 ? 'Weekly window spent' : 'Pace is fine';
  })();

  const heroSub = (() => {
    if (unmetered) return 'Press “Sync from Claude” and copy the four values from its usage view. After that it keeps itself current.';
    if (f.used != null && f.used >= 90 && f.resets_at)
      return `Back at full in ${dur(f.seconds_to_reset)}, at ${when(f.resets_at)}. The weekly window still has ${(w.remaining || 0).toFixed(0)}% left.`;
    if (!s.seven_day.resets_at) return 'Weekly reset time unknown yet.';
    const paceTxt = s.pace_ok === false
      ? `You are burning ${s.burn.pp7_per_hour.toFixed(2)}%/h; ${(s.sustainable_pp7_per_hour || 0).toFixed(2)}%/h lasts to reset.`
      : `Reset is ${when(s.seven_day.resets_at)}. Sustainable pace is ${(s.sustainable_pp7_per_hour || 0).toFixed(2)}%/h.`;
    return paceTxt;
  })();

  const regimeText = {
    burst: 'Burst user — the 5-hour window is what stops you. Splitting and deferring helps most.',
    sustained: 'Sustained user — the weekly window binds. Cheaper models and smaller context help most; deferring does not.',
    comfortable: 'Comfortable — neither window is close to binding at your current pace.',
    unknown: 'Not enough history yet to tell which window binds you.',
  }[s.regime] || '';

  return `
  <div class="page-head">
    <div><h1>Usage</h1><div class="sub">What your AI agents have cost, whichever ones you use.</div></div>
    <button class="btn-sm" onclick="go('plan')">Plan a prompt</button>
  </div>

  ${usageByModelCard(u)}

  <!-- Claude's 5-hour / weekly limits are one provider's plan limits, so they sit
       in a section of their own *below* the all-agent table — not as the page. -->
  ${subscriptionIntro()}
  <h2 class="provider-title">Claude (Pro / Max)</h2>
  <p class="small muted" style="margin:0 0 14px;max-width:70ch">${
    s.source === 'status line' ? 'Live from the Claude Code status line · ' + ago(s.observed_at)
    : s.source === 'your reading' ? 'Synced from Claude ' + ago(s.observed_at) + ' · counting down on its own'
    : s.source === 'transcripts' ? `From ${s.derived.turns} local Claude Code turns · last activity ${ago(s.observed_at)}`
    : 'No numbers yet — press “Sync from Claude”'}
    · <a href="#" onclick="setWindowsProvider('other');return false">Hide Claude limits</a></p>

  ${firstRun ? `
  <div class="card">
    <div class="label">What this measures</div>
    <div class="hero">Two windows stand between you and a wall mid-run</div>
    <p class="small muted mt8" style="max-width:62ch">A Claude Pro or Max plan enforces two limits: a
      rolling <strong>5-hour session window</strong> and a <strong>7-day weekly window</strong>, both
      shared across Claude chat, Claude Code and Cowork. Anthropic doesn't show you the dollar size of
      either — only the percentage — so that's what the two dials below will track once they have a
      reading to show.</p>
    <div class="steps-num mt16">
      <div class="stepn">
        <div class="stepn-n">1</div>
        <div style="flex:1;min-width:0">
          <strong>Give it a real reading</strong>
          <div class="small muted mt8">Ten seconds: open Claude's own usage view — the ring beside the
            model picker in the desktop app, or <span class="mono">/usage</span> in the terminal — and
            copy the four numbers across. The dials go live immediately and keep counting down on their
            own from there.</div>
          <div class="row mt12"><button class="btn-primary btn-sm" onclick="manualEntry()">Sync from Claude</button></div>
        </div>
      </div>
      <div class="stepn">
        <div class="stepn-n">2</div>
        <div style="flex:1;min-width:0">
          <strong>Or just look around first</strong>
          <div class="small muted mt8">Loads a few days of plausible sample usage — clearly tagged, and
            excluded from every real total — so you can see what a populated Windows, Projects and
            History screen look like before committing anything real.</div>
          <div class="row mt12"><button class="btn-sm" onclick="seedDemo()">See it with sample data</button></div>
        </div>
      </div>
    </div>
  </div>` : `
  <div class="card">
    <div class="spread" style="align-items:flex-start">
      <div style="min-width:0">
        <div class="label">Weekly window</div>
        <div class="hero">${heroLine}</div>
        <div class="small muted mt8" style="max-width:52ch">${heroSub}</div>
      </div>
      <div style="text-align:right">
        ${spark(hist.points, 280, 56, 'pct7', true)}
        <div class="small muted" style="margin-top:2px">weekly window used, last 30 hours</div>
      </div>
    </div>
    <div class="small muted mt12">${regimeText}</div>
  </div>`}

  <div class="grid grid-3 mt16">
    <div class="card gauge-card">
      <div class="ring-card">
        ${ring(f.used, 92, 'current session', true)}
        <div class="ring-meta">
          <div class="label">Current session</div>
          <div class="n tnum">${f.used == null ? 'no reading' : f.used.toFixed(0) + '% used'}</div>
          <div class="small muted">${f.seconds_to_reset == null ? 'reset time unknown'
            : 'resets in ' + dur(f.seconds_to_reset)}</div>
        </div>
      </div>
      ${meter(f.used, 'used ' + pct(f.used), f.resets_at ? 'resets ' + when(f.resets_at) : '')}
      <div class="small muted mt12">${f.used == null ? 'Press “Sync from Claude” to start.'
        : s.burn.pp5_per_hour > 0.05
          ? `At ${s.burn.pp5_per_hour.toFixed(1)}%/h that is about ${dur((f.remaining || 0) / s.burn.pp5_per_hour * 3600)} of work left.`
          : 'Nothing burning locally right now.'}</div>
    </div>

    <div class="card gauge-card">
      <div class="ring-card">
        ${ring(w.used, 92, 'weekly', true)}
        <div class="ring-meta">
          <div class="label">Weekly · all models</div>
          <div class="n tnum">${w.used == null ? 'no reading' : w.used.toFixed(0) + '% used'}</div>
          <div class="small muted">${w.seconds_to_reset == null ? 'reset time unknown'
            : 'resets in ' + dur(w.seconds_to_reset)}</div>
        </div>
      </div>
      ${meter(w.used, 'used ' + pct(w.used), w.resets_at ? 'resets ' + when(w.resets_at) : '')}
      <div class="small muted mt12">${w.used == null ? 'Both windows are shared across chat, Cowork and Claude Code.'
        : s.pace_ok === false
          ? `Over pace. Drop to ${(s.sustainable_pp7_per_hour || 0).toFixed(2)}%/h to reach the reset.`
          : `Under pace — sustainable is ${(s.sustainable_pp7_per_hour || 0).toFixed(2)}%/h.`}</div>
    </div>

    <div class="card">
      <div class="label">Where these numbers come from</div>
      <div class="stat-value mt8" style="font-size:17px">${
        s.source === 'status line' ? 'Live status line'
        : s.derived && s.derived.anchored_at ? 'Your reading, ticking forward'
        : s.source === 'transcripts' ? 'Local sessions only'
        : 'Nothing yet'}</div>
      <div class="small muted mt8">${
        s.source === 'status line' ? 'Exact figures straight from Claude Code, refreshed continuously.'
        : s.derived && s.derived.anchored_at
          ? `Anchored on your reading ${ago(s.derived.anchored_at)}, plus ${usd(s.derived.spent5)} of local work since. Re-sync any time.`
          : s.source === 'transcripts'
            ? 'Counted from local Claude Code sessions only. Work done in Cowork or the browser is not visible here — sync a reading to include it.'
            : 'Sync a reading from Claude, or use Claude Code locally.'}</div>
      <dl class="kv mt12">
        <dt>Window size</dt><dd>${s.capacity.usd_per_5h == null ? '—' : '$' + s.capacity.usd_per_5h.toFixed(0)} <span class="muted">(${esc(s.capacity.confidence)})</span></dd>
        <dt>Plan</dt><dd>${esc(s.capacity.plan_label)}</dd>
        <dt>Local burn</dt><dd class="tnum">${s.burn.pp5_per_hour.toFixed(2)}%/h</dd>
      </dl>
      <div class="row mt12"><button class="btn-primary btn-sm" onclick="manualEntry()">Sync from Claude</button></div>
    </div>
  </div>

  ${s.leaks.length ? `
  <div class="card mt16">
    <div class="card-head"><h2>Where your week is going</h2>
      <span class="hint">Flagged at 10% or more of recent usage</span></div>
    ${s.leaks.map(l => `
      <div class="banner warning" style="margin-bottom:8px">
        <div class="spread"><strong>${esc(l.name)}</strong>
          <span class="chip warning"><span class="swatch" style="background:var(--warning)"></span>${l.share}% of usage</span></div>
        <div class="small muted mt8">${esc(l.why)}</div>
        <div class="small mt8"><strong>Fix:</strong> ${esc(l.fix)}</div>
      </div>`).join('')}
  </div>` : ''}

  ${providerLimitCards(lim)}

  ${activeProjectsCard(s)}`;
}

async function seedDemo() {
  try {
    const r = await api('/api/demo', { method: 'POST' });
    toast(r.message);
    S.status = null;          // stats/totals just changed — don't reuse the stale cache
    render();
  } catch (e) { toast(e.message, true); }
}

function manualEntry() {
  modal(`<h2>Copy your usage across</h2>
    <p class="small muted">Open Claude\u2019s own usage view — the ring next to the model picker in the
      desktop app, or <span class="mono">/usage</span> in the terminal — and copy the four values.
      The fields below are in the same order as that dialog.</p>

    <div class="card" style="background:var(--page);margin-bottom:12px">
      <div class="label" style="font-weight:600;color:var(--text-primary)">Current session</div>
      <div class="grid grid-2" style="gap:10px;margin-top:8px">
        <div class="field" style="margin:0"><label>% used</label>
          <input id="m5" type="number" min="0" max="100" placeholder="95"></div>
        <div class="field" style="margin:0"><label>Resets in</label>
          <div class="row" style="gap:6px">
            <input id="m5h" type="number" min="0" placeholder="1" style="width:70px"><span class="small muted">hr</span>
            <input id="m5m" type="number" min="0" max="59" placeholder="12" style="width:70px"><span class="small muted">min</span>
          </div></div>
      </div>
    </div>

    <div class="card" style="background:var(--page);margin-bottom:12px">
      <div class="label" style="font-weight:600;color:var(--text-primary)">Weekly · all models</div>
      <div class="grid grid-2" style="gap:10px;margin-top:8px">
        <div class="field" style="margin:0"><label>% used</label>
          <input id="m7" type="number" min="0" max="100" placeholder="30"></div>
        <div class="field" style="margin:0"><label>Resets in</label>
          <div class="row" style="gap:6px">
            <input id="m7h" type="number" min="0" placeholder="7" style="width:70px"><span class="small muted">hr</span>
            <input id="m7m" type="number" min="0" max="59" placeholder="22" style="width:70px"><span class="small muted">min</span>
          </div></div>
      </div>
    </div>

    <div class="banner"><strong>You only do this when you want to re-sync.</strong> From here the
      countdowns tick down on their own, the bars reset by themselves when the window rolls over, and
      any new local Claude Code work is added on top automatically.</div>

    <div class="row mt12" style="justify-content:flex-end">
      <button onclick="closeModal()">Cancel</button>
      <button class="btn-primary" onclick="saveManual()">Save reading</button>
    </div>`);
}

async function saveManual() {
  const v = id => {
    const el = document.getElementById(id);
    const n = el && el.value === '' ? null : Number(el.value);
    return (n == null || Number.isNaN(n)) ? null : n;
  };
  const mins = (h, m) => {
    const hh = v(h), mm = v(m);
    return (hh == null && mm == null) ? null : (hh || 0) * 60 + (mm || 0);
  };
  const p5 = v('m5'), p7 = v('m7');
  if (p5 == null && p7 == null) return toast('Enter at least one percentage.', true);

  try {
    const r = await api('/api/meter/manual', {
      method: 'POST',
      body: { pct5: p5, pct7: p7, reset5_minutes: mins('m5h', 'm5m'), reset7_minutes: mins('m7h', 'm7m') },
    });
    closeModal();
    const cal = r && r.calibration;
    toast(cal && cal.capacity && Object.keys(cal.capacity).length
      ? `Saved. Your 5-hour window works out to about $${(cal.capacity['5-hour'] || 0).toFixed(0)} of work.`
      : 'Saved — your windows are now live and counting down.');
    S.status = null;
    render();
  } catch (e) { toast(e.message, true); }
}

/* ---------------------------------------------------------- accordion */

// The features accordion from the USAvionix reference: hover (mouse), focus
// (keyboard) or a first tap (touch) grows one card and compresses the rest;
// activating the already-open card follows its link. Art is static inline SVG
// (constants below, never user data), coloured through CSS classes so it takes
// each card's --hue.
const ACC_ART = {
  gauge: `<svg viewBox="0 0 400 480" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
    <circle class="s dim" cx="200" cy="190" r="128" stroke-width="1"/>
    <circle class="s" cx="200" cy="190" r="104" stroke-width="12" stroke-dasharray="470 654" transform="rotate(135 200 190)"/>
    <circle class="s dim" cx="200" cy="190" r="80" stroke-width="2" stroke-dasharray="3 9"/>
    <path class="s" d="M200 190 L262 128" stroke-width="4"/><circle class="f" cx="200" cy="190" r="8"/></svg>`,
  graph: `<svg viewBox="0 0 400 480" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
    <path class="s dim" d="M90 90 L200 170 M90 90 L110 250 M200 170 L300 110 M200 170 L200 290 M110 250 L200 290 M300 110 L320 250 M200 290 L320 250" stroke-width="2"/>
    <rect class="s" x="66" y="70" width="48" height="40" rx="12" stroke-width="3"/>
    <rect class="s" x="176" y="150" width="48" height="40" rx="12" stroke-width="3"/>
    <rect class="s dim" x="86" y="230" width="48" height="40" rx="12" stroke-width="3"/>
    <rect class="s" x="276" y="90" width="48" height="40" rx="12" stroke-width="3"/>
    <rect class="s dim" x="176" y="270" width="48" height="40" rx="12" stroke-width="3"/>
    <rect class="s dim" x="296" y="230" width="48" height="40" rx="12" stroke-width="3"/>
    <circle class="f" cx="90" cy="90" r="6"/><circle class="f" cx="200" cy="170" r="6"/><circle class="f" cx="300" cy="110" r="6"/></svg>`,
  trace: `<svg viewBox="0 0 400 480" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
    <path class="s dim" d="M0 120H400M0 190H400M0 260H400M0 330H400" stroke-width="1" stroke-dasharray="2 8"/>
    <path class="s" d="M-10 300 C 40 300 60 250 100 258 S 160 170 205 190 S 270 110 320 130 S 380 70 420 60" stroke-width="4"/>
    <path class="f" opacity=".14" d="M-10 300 C 40 300 60 250 100 258 S 160 170 205 190 S 270 110 320 130 S 380 70 420 60 V480 H-10Z"/>
    <circle class="f" cx="320" cy="130" r="7"/></svg>`,
};

function accordion(items) {
  return `<div class="acc" role="group" aria-label="What PromptMeter does">${items.map((it, i) => `
    <div class="acc-item${i === 0 ? ' open' : ''}" role="button" tabindex="0"
         aria-expanded="${i === 0}" data-go="${esc(it.go)}" data-hue="${esc(String(it.hue))}"
         aria-label="${esc(it.title)} — ${esc(it.cta)}">
      <div class="acc-art" aria-hidden="true">${ACC_ART[it.art] || ''}</div>
      <span class="acc-idx">0${i + 1}</span>
      <div class="acc-body">
        <h3 class="acc-title">${esc(it.title)}</h3>
        <p>${esc(it.text)}</p>
        <span class="acc-cta">${esc(it.cta)} <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 10L10 2M4 2h6v6"/></svg></span>
      </div>
    </div>`).join('')}</div>`;
}

function accOpen(el) {
  el.parentElement.querySelectorAll('.acc-item').forEach(x => {
    const on = x === el;
    x.classList.toggle('open', on);
    x.setAttribute('aria-expanded', String(on));
  });
}

function accGo(target) {
  if (target === 'focus-prompt') {
    const box = document.getElementById('p-prompt');
    if (box) { box.scrollIntoView({ behavior: 'smooth', block: 'center' }); box.focus({ preventScroll: true }); }
  } else go(target);
}

// One delegated set of listeners, wired once — .acc is rebuilt on every render().
document.addEventListener('pointerover', e => {
  if (e.pointerType !== 'mouse') return;
  const el = e.target.closest && e.target.closest('.acc-item');
  if (el && !el.classList.contains('open')) accOpen(el);
});
document.addEventListener('focusin', e => {
  // :focus-visible only — a tap also focuses the card, and that must not
  // count as the "open" half of a two-tap open-then-follow.
  const el = e.target.closest && e.target.closest('.acc-item');
  if (el && !el.classList.contains('open') && el.matches(':focus-visible')) accOpen(el);
});
document.addEventListener('click', e => {
  const el = e.target.closest && e.target.closest('.acc-item');
  if (!el) return;
  if (!el.classList.contains('open')) accOpen(el); else accGo(el.dataset.go);
});
document.addEventListener('keydown', e => {
  const el = e.target.closest && e.target.closest('.acc-item');
  if (!el) return;
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.click(); }
  else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    const sibs = [...el.parentElement.querySelectorAll('.acc-item')];
    const dir = (e.key === 'ArrowRight' || e.key === 'ArrowDown') ? 1 : -1;
    const next = sibs[(sibs.indexOf(el) + dir + sibs.length) % sibs.length];
    // Open explicitly rather than trusting :focus-visible to fire on the focus
    // change — that heuristic differs by browser and input modality.
    e.preventDefault(); next.focus({ preventScroll: true }); accOpen(next);
  }
});

/* --------------------------------------------------------------- plan */

let planPreview = null;

async function viewPlan() {
  const models = S.models.length ? S.models : (S.models = (await api('/api/models')).models);
  const pv = await api('/api/providers').catch(() => ({ active: 'heuristic', keys: {} }));
  const sel = (S.models || []).find(m => m.id === S.model);
  return `
  <section class="hero-block">
    <div class="eyebrow">PromptMeter · for any AI agent</div>
    <h1>Know what it costs<br>before you <span class="mark">send it.</span></h1>
    <p class="lede">see the price, the risk and the split — for any model you use, before it costs you.</p>
  </section>
  ${accordion([
    { title: 'Estimate any prompt', hue: 1, art: 'gauge', go: 'focus-prompt', cta: 'Try a prompt',
      text: 'Tokens, cost and the chance of being cut off — with a plain-English verdict, for any model: Claude, GPT, Gemini, Mistral, a local one, or one you add.' },
    { title: 'Split it into steps', hue: 2, art: 'graph', go: 'projects', cta: 'See your projects',
      text: 'Big asks become a dependency graph of small steps, each with its own budget, model and a check for when it is done.' },
    { title: 'Watch it against reality', hue: 3, art: 'trace', go: 'dashboard', cta: 'Open usage',
      text: 'Real usage from any agent is tracked as you work, so spend and estimate accuracy come from your history, not guesses.' },
  ])}
  <div class="scallop"></div>
  <h2 class="section-title" id="p-form">Plan a prompt</h2>
  <div class="grid grid-2">
    <div class="card">
      <div class="field">
        <label>Your prompt</label>
        <textarea id="p-prompt" style="min-height:210px" placeholder="Paste the prompt you were about to send…"></textarea>
      </div>
      <div class="field"><label>Model</label>
        <select id="p-model" onchange="onModelChange(this.value)">${(S.vendors || []).map(v => `
          <optgroup label="${esc(v.label)}">${v.models.map(m =>
            `<option value="${m.id}" ${m.id === S.model ? 'selected' : ''}>${esc(m.label)} — ${
              tokens(m.context)} ctx · $${m.in}/$${m.out} per M</option>`).join('')}</optgroup>`).join('')}
        </select>
        ${sel ? `<div class="small muted mt8">${tokens(sel.context)} context · up to ${
          tokens(sel.max_output)} output per reply · ${sel.thinking ? 'reasons before answering' : 'no reasoning step'}</div>` : ''}
      </div>
      <div class="grid grid-2" style="gap:10px">
        <div class="field"><label>Reasoning effort</label>
          <select id="p-effort" onchange="onEffortChange(this.value)" ${sel && !sel.thinking ? 'disabled' : ''}>${(S.efforts || []).map(x =>
            `<option value="${x}" ${x === (S.effort || 'medium') ? 'selected' : ''}>${x}</option>`).join('')}</select>
        </div>
        <div class="field"><label>Turn cap (blank = none)</label>
          <input id="p-cap" type="number" min="1" placeholder="e.g. 12"></div>
      </div>
      <div class="field"><label>Working folder (optional — lets the deliverable checks run)</label>
        <input id="p-workdir" placeholder="C:\\Users\\you\\project"></div>
      <div class="field"><label>Splitting</label>
        <select id="p-force">
          <option value="auto">Automatic — split only if it helps</option>
          <option value="single">Never split — run the whole thing</option>
          <option value="forced">Always split</option>
        </select></div>
      <label class="row" style="gap:8px;cursor:pointer;margin-bottom:12px">
        <input type="checkbox" id="p-planner" style="width:auto"
          ${pv.active === 'heuristic' ? 'disabled' : (pv.auto ? 'checked' : '')}>
        <span class="small">Draft the plan first${pv.active === 'heuristic'
          ? ' <span class="muted">— pick a model on the Setup page to enable</span>'
          : ` <span class="muted">using ${esc(pv.active)}</span>`}</span>
      </label>
      <div class="row">
        <button class="btn-primary" onclick="doPreview()">See what it costs</button>
        <button onclick="commitPlan()" id="p-create" disabled>Create project</button>
      </div>
    </div>
    <div id="p-out">
      <div class="card"><div class="empty">
        <div class="big">Nothing estimated yet</div>
        <div class="small">Paste a prompt and press “See what it costs”. Nothing is sent anywhere —
          the estimate is computed on this machine.</div>
      </div></div>
    </div>
  </div>`;
}

function onModelChange(id) {
  S.model = id;
  localStorage.setItem('pm-model', id);
  api('/api/settings', { method: 'PATCH', body: { model: id } }).catch(() => {});
  const eff = document.getElementById('p-effort');
  if (eff) { S.effort = eff.value; localStorage.setItem('pm-effort', eff.value); }
  // Picking a model re-renders the whole form (the effort select can flip
  // enabled/disabled), so carry every field across — not just the prompt.
  // Losing the turn cap / working folder / splitting mode / planner tick on a
  // model change was a silent data-loss bug.
  const ids = ['p-prompt', 'p-cap', 'p-workdir', 'p-force'];
  const saved = Object.fromEntries(ids.map(i => [i, (document.getElementById(i) || {}).value]));
  const planner = (document.getElementById('p-planner') || {}).checked;
  render().then(() => {
    ids.forEach(i => {
      const el = document.getElementById(i);
      if (el && saved[i] != null) el.value = saved[i];
    });
    const pl = document.getElementById('p-planner');
    if (pl && planner && !pl.disabled) pl.checked = true;
  });
}

// Model persists on change (it reshapes the form — the effort select can
// become disabled/enabled). Effort doesn't change the form's shape, so it
// persists quietly without a full re-render. Both replace what the removed
// persistent selection bar used to do (ARCHITECTURE.md §6) — the choice
// still follows you to another browser pointed at this same install.
function onEffortChange(v) {
  S.effort = v;
  localStorage.setItem('pm-effort', v);
  api('/api/settings', { method: 'PATCH', body: { effort: v } }).catch(() => {});
}

async function doPreview(forcePlanner) {
  const prompt = document.getElementById('p-prompt').value.trim();
  if (!prompt) return toast('Paste a prompt first.', true);
  const out = document.getElementById('p-out');
  out.innerHTML = `<div class="card"><div class="empty">${forcePlanner ? 'Drafting steps…' : 'Estimating…'}</div></div>`;
  try {
    S.model = document.getElementById('p-model').value;
    S.effort = (document.getElementById('p-effort') || {}).value || 'medium';
    localStorage.setItem('pm-model', S.model);
    localStorage.setItem('pm-effort', S.effort);
    planPreview = await api('/api/preview', {
      method: 'POST',
      body: {
        prompt,
        model: document.getElementById('p-model').value,
        effort: (document.getElementById('p-effort') || {}).value || 'medium',
        workdir: document.getElementById('p-workdir').value,
        turn_cap: document.getElementById('p-cap').value || null,
        force: document.getElementById('p-force').value,
        use_planner: forcePlanner || (document.getElementById('p-planner') || {}).checked || false,
      },
    });
    out.innerHTML = renderPreview(planPreview);
    document.getElementById('p-create').disabled = false;
    // Below 900px the two columns stack and the result lands off-screen under
    // the form — bring it into view instead of leaving the user staring at "Estimate".
    if (window.innerWidth <= 900) out.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    out.innerHTML = `<div class="card"><div class="banner critical">${esc(e.message)}</div></div>`;
  }
}

// One-click recovery from "nothing to split on": re-runs the same estimate
// with the connected model drafting the step list, instead of making the
// user notice, tick "Draft the plan first" themselves, and press Estimate
// again. Ticks the box too, so the state shown matches what just ran.
function draftSplit() {
  const box = document.getElementById('p-planner');
  if (box) box.checked = true;
  doPreview(true);
}

function renderPreview(p) {
  const e = p.estimate;
  const sv = p.savings;
  const drivers = e.risk_drivers || [];
  const maxw = Math.max(...drivers.map(d => d.weight), 0.01);

  const pl = p.plain || {};
  const vb = (pl.verdict || {}).band || 'good';
  return `
  <div class="card">
    ${e.ambiguous ? `<div class="banner warning" style="margin:-4px 0 14px">
      <strong>Too little here to size with confidence.</strong> Nothing in this prompt
      matched a recognisable kind of work, so the number below is a rough floor from a
      default guess, not a real reading — add what it should do, in what format, or how
      you'll know it's done, and it will sharpen up.</div>` : ''}
    <div class="banner ${vb === 'good' ? 'good' : vb === 'warning' ? 'warning' : 'critical'}"
         style="margin:-4px 0 14px">
      <div style="font-size:15px;font-weight:600">${esc((pl.verdict || {}).do || '')}</div>
      <div class="small mt8">${esc((pl.verdict || {}).why || '')}</div>
    </div>

    <div class="grid grid-2" style="gap:14px">
      <div>
        <div class="label">How big this is</div>
        <div class="stat-value">${esc((pl.size || {}).headline || '—')}</div>
        <div class="small muted">${esc((pl.size || {}).detail || '')}</div>
      </div>
      <div>
        <div class="label">If it goes badly</div>
        <div class="stat-value">${esc((pl.worst || {}).headline || '—')}</div>
        <div class="small muted">${esc(pl.risk || '')}</div>
      </div>
    </div>
    ${pl.risk_context ? `<div class="small mt12" style="color:var(--serious)">${esc(pl.risk_context)}</div>` : ''}

    <div class="grid grid-2 mt16" style="gap:14px">
      <div>
        <div class="label">Tokens</div>
        <div class="stat-value tnum">${tokens((e.budget_p50 || {}).total_tokens ?? e.prompt_tokens)}</div>
        <div class="small muted">worst case ${tokens((e.budget_p95 || {}).total_tokens)}</div>
      </div>
      <div>
        <div class="label">Cost <span class="muted">(work value, not a bill on a plan)</span></div>
        <div class="stat-value tnum">${usd(e.cost_p50)}</div>
        <div class="small muted">worst case ${usd(e.cost_p95)}</div>
      </div>
    </div>
    <div class="small muted mt8">${esc(pl.left || '')} ${esc(pl.context || '')}</div>

    <details class="mt16"><summary>${e.spec && e.spec.vendor === 'anthropic'
      ? 'Percent of your plan window, task type, and everything else'
      : 'Task type and everything else'}</summary>
    <div class="card-head" style="margin-top:8px"><h3>Estimate</h3>${riskChip(e.risk_band, e.risk)}</div>
    ${e.spec && e.spec.vendor === 'anthropic' ? `
    ${meter(Math.min(100, e.remaining_pp5 > 0 ? (e.pp5_p95 / e.remaining_pp5) * 100 : 100),
      'worst case against what is left', e.pp5_p95 > e.remaining_pp5 ? 'does not fit' : 'fits')}
    <div class="grid grid-2 mt12" style="gap:14px">
      <div>
        <div class="label">Share of your 5-hour window</div>
        <div class="stat-value tnum">${e.pp5_p50.toFixed(0)}%</div>
        <div class="small muted">worst case ${e.pp5_p95.toFixed(0)}% · ${e.remaining_pp5.toFixed(0)}% left</div>
      </div>
      <div>
        <div class="label">Share of your weekly window</div>
        <div class="stat-value tnum">${e.pp7_p50.toFixed(1)}%</div>
        <div class="small muted">worst case ${e.pp7_p95.toFixed(1)}% · ${e.remaining_pp7.toFixed(0)}% left</div>
      </div>
    </div>` : `
    <div class="small muted">${esc((e.spec || {}).label || 'This model')} isn't billed against a Claude
      plan window, so there's no percent-of-window figure to show here — just the token/cost numbers
      above, which apply the same way regardless of provider.</div>`}
    <dl class="kv mt12">
      <dt>Task type</dt><dd>${esc(e.task_label)} <span class="muted">(${esc(e.confidence)}, ${e.samples} past runs)</span></dd>
      <dt>Prompt size</dt><dd>${tokens(e.prompt_tokens)} tokens</dd>
      <dt>Expected turns</dt><dd>${e.turns_p50} <span class="muted">· worst case ${e.turns_p95}</span></dd>
      ${e.calibration && e.calibration.calibrated ? `<dt>Worst-case band</dt>
        <dd>calibrated from ${e.calibration.n} of your own past “${esc(e.task_label.toLowerCase())}” runs
          <span class="muted">(not a guarantee — a small-sample estimate, not 95% coverage in the formal sense)</span></dd>` : ''}
    </dl>
    ${e.budget_p50 ? (() => {
      const b = e.budget_p50, w = e.budget_p95;
      const over = b.peak_context >= b.context_window;
      return `<div class="mt16">
      <div class="label">Token budget — typical run <span class="muted">(worst case in grey)</span></div>
      ${segbar(b)}
      <table style="margin-top:6px">
        <tr><td>Input sent across ${b.turns} turns</td>
            <td class="num">${tokens(b.input_tokens)}</td>
            <td class="num muted">${tokens(w.input_tokens)}</td></tr>
        <tr><td style="padding-left:22px" class="muted">of which conversation re-sent</td>
            <td class="num muted">${tokens(b.growth_tokens)}</td>
            <td class="num muted">${tokens(w.growth_tokens)}</td></tr>
        <tr><td>Output generated</td>
            <td class="num">${tokens(b.output_tokens)}</td>
            <td class="num muted">${tokens(w.output_tokens)}</td></tr>
        ${b.thinking_tokens ? `<tr><td style="padding-left:22px" class="muted">of which thinking (${esc(b.effort)})</td>
            <td class="num muted">${tokens(b.thinking_tokens)}</td>
            <td class="num muted">${tokens(w.thinking_tokens)}</td></tr>` : ''}
        <tr><td><strong>Total tokens</strong></td>
            <td class="num"><strong>${tokens(b.total_tokens)}</strong></td>
            <td class="num muted">${tokens(w.total_tokens)}</td></tr>
      </table>

      <div class="mt12">
        <div class="spread small"><span>Peak conversation size</span>
          <span class="tnum">${tokens(b.peak_context)} of ${tokens(b.context_window)}</span></div>
        ${meter(b.context_pct, 'context window', over ? 'full — will compact' : 'fits')}
      </div>
      ${w.compactions ? `<div class="banner warning mt8">
        <strong>Runs out of context in the worst case.</strong> About ${w.compactions}
        compaction${w.compactions === 1 ? '' : 's'} would be needed, costing roughly
        ${tokens(w.compaction_tokens)} extra tokens — already included above.</div>` : ''}
      ${b.output_capped ? `<div class="banner warning mt8">
        Output per reply is capped at ${tokens(b.max_output)} for this model, so long
        answers will be truncated or continued across turns.</div>` : ''}
    </div>`; })() : ''}
    ${e.scope ? `<div class="mt12">
      <div class="label">Scope — how big this is versus a typical ${esc(e.task_label.toLowerCase())}</div>
      <div class="row mt8"><span class="chip"><span class="swatch" style="background:${
        e.scope.multiplier > 1.3 ? 'var(--serious)' : e.scope.multiplier < 0.7 ? 'var(--good)' : 'var(--accent)'
      }"></span>${e.scope.multiplier}&times;</span>
      <span class="small muted">${p.planner && p.planner.ok
        ? `from a ${p.planner.items.length}-step plan drafted by ${esc(p.planner.provider)}`
        : 'from the wording'}</span></div>
      ${(e.scope.drivers || []).map(d => `<div class="small muted" style="margin-top:4px">· ${esc(d.label)}</div>`).join('')}
    </div>` : ''}
    ${p.planner && !p.planner.ok ? `<div class="banner warning mt12">
      <strong>Planner unavailable.</strong> ${esc(p.planner.error)} Fell back to the built-in scope analysis.
    </div>` : ''}
    ${p.planner && p.planner.ok && p.planner.notes ? `<div class="banner mt12">
      <strong>${esc(p.planner.provider)} flagged:</strong> ${esc(p.planner.notes)}</div>` : ''}
    ${drivers.length ? `<div class="mt12"><div class="label">What is driving the risk</div>
      ${drivers.map(d => `<div class="driver">
        <span class="bar" style="width:${Math.max(8, d.weight / maxw * 74)}px"></span>
        <span>${esc(d.label)}</span></div>`).join('')}</div>` : ''}
    </details>
  </div>

  <div class="card">
    <div class="card-head"><h2>${p.split ? `Split into ${p.steps.length} steps` : 'Run as one step'}</h2>
      <span class="hint">${p.stages} stage${p.stages === 1 ? '' : 's'}</span></div>
    <div class="banner ${p.split ? 'warning'
      : /too big|needs splitting|unavailable/i.test(p.split_reason) ? 'critical' : 'good'}">
      <div>${esc(p.split_reason)}</div>
      ${!p.split && p.can_draft ? `<div class="mt8">
        <button class="btn-sm btn-primary" onclick="draftSplit()">Draft the steps now</button></div>`
      : !p.split && !p.can_draft && /no model is connected/.test(p.split_reason) ? `<div class="mt8">
        <button class="btn-sm" onclick="go('setup')">Connect a provider</button></div>` : ''}
    </div>
    <div class="mt12">
      ${p.steps.map((s, i) => `
        <div class="spread" style="padding:8px 0;border-bottom:1px solid var(--grid)">
          <div style="min-width:0">
            <div class="row"><span class="step-num">${i + 1}</span>
              <strong style="font-size:13px">${esc(s.title)}</strong></div>
            <div class="small muted" style="margin-left:31px">${esc(s.prompt.slice(0, 110))}${s.prompt.length > 110 ? '…' : ''}</div>
          </div>
          <div style="text-align:right;white-space:nowrap">
            <div class="small tnum">${usd(s.estimate.cost_p50)}</div>
            <div class="small muted">${esc(modelLabel(s.model))}</div>
          </div>
        </div>`).join('')}
    </div>
    ${sv ? (() => {
      const cheaper = sv.optimised_split < sv.single_run;
      return `
    <div class="mt16">
      <div class="label">Where the difference comes from</div>
      <p class="small muted" style="margin-top:6px">Each line is a separate estimate, not a rule of
        thumb — they reconcile.</p>
      <table style="margin-top:6px">
        <tr><td>One long run</td><td class="num">${usd(sv.single_run)}</td></tr>
        <tr><td>Less context re-sent each turn${sv.avoided_growth < 0 ? ' <span class="muted">(split costs more here)</span>' : ''}</td>
          <td class="num">${sv.avoided_growth >= 0 ? '−' : '+'}${usd(Math.abs(sv.avoided_growth))}</td></tr>
        <tr><td>Cheaper models on the simple steps</td><td class="num">−${usd(sv.routing)}</td></tr>
        <tr><td>Each step carries only what it touches</td><td class="num">−${usd(sv.pruning)}</td></tr>
        <tr><td><strong>Split total</strong></td><td class="num"><strong>${usd(sv.optimised_split)}</strong></td></tr>
      </table>
      <div class="banner ${cheaper ? 'good' : 'warning'} mt12">
        ${cheaper
          ? `<strong>Cheaper and safer.</strong> About ${usd(sv.net_saving)} less, mostly because a
             long agent loop re-sends its whole conversation every turn and short steps do not.
             And a step that goes wrong now costs ${usd(sv.blast_radius_after)} instead of
             ${usd(sv.blast_radius_before)}.`
          : `<strong>This split costs about ${usd(sv.optimised_split - sv.single_run)} more.</strong>
             Do it anyway only for the reasons that are not about price: ${isClaudeModel(e.model)
               ? 'it fits inside your window, and a' : 'a'} step that goes wrong costs ${usd(sv.blast_radius_after)}
             instead of ${usd(sv.blast_radius_before)}. If that does not matter, set Splitting to “Never split”.`}
      </div>
    </div>`; })() : ''}
  </div>

  ${p.schedule.spans_windows ? `
  <div class="card">
    <div class="card-head"><h2>Schedule</h2>
      <span class="hint">packed into 5-hour windows</span></div>
    <p class="small muted">This does not fit one window. Unused plan percent evaporates at every
      reset, so each window is packed as full as the safety margin allows and the rest deferred.</p>
    ${p.schedule.windows.map((w, i) => `
      <div style="padding:9px 0;border-bottom:1px solid var(--grid)">
        <div class="spread"><strong class="small">${i === 0 ? 'Now' : `After reset ${i}`} <span class="muted">(+${i * 5}h)</span></strong>
          <span class="small tnum muted">${w.used.toFixed(0)}% of window</span></div>
        <div class="small muted mt8">${w.steps.map(s => esc(s.title)).join(' · ') || '—'}</div>
      </div>`).join('')}
    <div class="small muted mt12">Finishes about ${p.schedule.hours_to_finish}h from now.
      Weekly window left afterwards: ${p.schedule.weekly_remaining_after.toFixed(0)}%.</div>
  </div>` : ''}`;
}

// A single hue stepped by opacity in pipeline order, not four categorical
// colours — see the CSS comment on .segbar. input_tokens/output_tokens
// already include growth_tokens/thinking_tokens as sub-totals (the table
// this sits beside shows that nesting with an indented "of which" row), so
// the bar carves those out into their own segment rather than double-
// counting: the four segments sum to exactly total_tokens.
function segbar(b) {
  if (!b || !b.total_tokens) return '';
  const growth = b.growth_tokens || 0;
  const thinking = b.thinking_tokens || 0;
  const inputBase = Math.max(0, (b.input_tokens || 0) - growth);
  const outputBase = Math.max(0, (b.output_tokens || 0) - thinking);
  const total = inputBase + growth + outputBase + thinking;
  if (total <= 0) return '';
  const parts = [
    { label: 'input', v: inputBase, cls: 's0' },
    { label: 're-sent conversation', v: growth, cls: 's1' },
    { label: 'output', v: outputBase, cls: 's2' },
    { label: 'thinking', v: thinking, cls: 's3' },
  ].filter(p => p.v > 0);
  const segs = parts.map(p => {
    const w = p.v / total * 100;
    return `<div class="seg ${p.cls}" style="flex:${Math.max(2, w)} 0 0">${w > 9 ? `<span>${tokens(p.v)}</span>` : ''}</div>`;
  }).join('');
  const legend = parts.map(p =>
    `<span class="k"><span class="sw ${p.cls}"></span>${p.label} · ${tokens(p.v)}</span>`).join('');
  return `<div class="segbar" role="img" aria-label="Token budget by kind">${segs}</div>
    <div class="segbar-legend">${legend}</div>`;
}

function modelLabel(id) {
  const m = S.models.find(x => x.id === id);
  return m ? m.label : id;
}

// A Claude plan window only exists for Anthropic models — showing "% of a
// 5-hour window" beside a GPT/Gemini/local run states a number that means
// nothing for that model, the same leak plain.py's vendor branch already
// guards against on the Plan screen (see explain()). An id the catalogue
// doesn't know is NOT assumed to be Claude — that assumption is what made every
// other agent look like a Claude run. Only before the catalogue has loaded at
// all do we say yes, so the figure isn't flashed off during startup.
function isClaudeModel(id) {
  if (!S.models.length) return true;
  const m = S.models.find(x => x.id === id);
  return !!m && m.vendor === 'anthropic';
}

async function commitPlan() {
  if (!planPreview) return;
  const prompt = document.getElementById('p-prompt').value.trim();
  const name = prompt.split('\n')[0].slice(0, 60);
  try {
    const p = await api('/api/projects', {
      method: 'POST',
      body: {
        name, prompt,
        model: document.getElementById('p-model').value,
        effort: (document.getElementById('p-effort') || {}).value || 'medium',
        workdir: document.getElementById('p-workdir').value,
        force: document.getElementById('p-force').value,
        turn_cap: document.getElementById('p-cap').value || null,
      },
    });
    toast('Project created.');
    goProject(p.id);
  } catch (e) { toast(e.message, true); }
}

/* ----------------------------------------------------------- projects */

let projFilter = 'active';

async function viewProjects() {
  const { projects } = await api('/api/projects?status=' + projFilter);
  const s = S.status || await api('/api/status').catch(() => null);
  const totals = (s && s.totals) || {};
  return `
  <div class="page-head">
    <div><h1>Projects</h1><div class="sub">Every prompt you have planned, with what it has cost so far.</div></div>
    <button class="btn-primary" onclick="go('plan')">New project</button>
  </div>

  <div class="grid grid-2">
    <div class="card"><div class="label">Projects</div>
      <div class="stat-value tnum">${totals.projects ?? '—'}</div>
      <div class="small muted">across every status</div></div>
    <div class="card"><div class="label">Spent</div>
      <div class="stat-value tnum">${usd(totals.spent)}</div>
      <div class="small muted">list-price equivalent · sample data excluded</div></div>
  </div>
  ${costByProject(projects)}

  <div class="tabs mt16">
    ${['active', 'done', 'archived', 'all'].map(f =>
      `<button class="tab ${projFilter === f ? 'active' : ''}" onclick="projFilter='${f}';render()">${f[0].toUpperCase() + f.slice(1)}</button>`).join('')}
  </div>
  ${projects.length ? `<div class="grid grid-auto">${projects.map(projCard).join('')}</div>`
    : `<div class="card"><div class="empty">
        <div class="big">Nothing here</div>
        <div class="small">Plan a prompt to create your first project.</div>
        <div class="mt12"><button class="btn-primary" onclick="go('plan')">Plan a prompt</button>
          <button style="margin-left:8px" onclick="seedDemo()">Load sample data</button></div>
      </div></div>`}`;
}

// What each project has actually cost, side by side — replaces a "Steps"
// count that mixed two different units (step count, iteration count) into
// one card nobody could read at a glance. Bars scale to the priciest project
// shown so relative spend is visible even when every number is small.
function costByProject(projects) {
  const withSpend = projects.filter(p => p.spent > 0);
  if (!withSpend.length) return '';
  const sorted = [...withSpend].sort((a, b) => b.spent - a.spent).slice(0, 8);
  const max = Math.max(...sorted.map(p => p.spent), 0.01);
  return `<div class="card mt16">
    <div class="label">Cost by project</div>
    <div class="mt8">${sorted.map(p => `
      <div class="driver" style="cursor:pointer" onclick="goProject(${p.id})">
        <span class="bar" style="width:${Math.max(8, p.spent / max * 160)}px;background:var(--accent)"></span>
        <span style="min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}${p.is_demo ? ' <span class="muted">(sample)</span>' : ''}</span>
        <span class="tnum muted">${usd(p.spent)}</span>
      </div>`).join('')}</div>
  </div>`;
}

function projCard(p) {
  const done = p.progress >= 1 || p.status === 'done';
  return `<div class="proj" onclick="goProject(${p.id})">
    <div class="spread">
      <div class="title">${esc(p.name)}</div>
      <div class="row" style="gap:6px">
        ${p.is_demo ? '<span class="chip">sample</span>' : ''}
        ${riskChip(p.risk_band)}
      </div>
    </div>
    <div class="goal">${esc(p.goal || p.prompt.slice(0, 150))}</div>
    ${progress(p.progress, done)}
    <div class="spread small muted">
      <span class="tnum">${p.done}/${p.total} steps · ${p.satisfied} verified</span>
      <span class="tnum">${usd(p.spent)} of ~${usd(p.est)}</span>
    </div>
    <div class="proj-actions" onclick="event.stopPropagation()">
      <button class="btn-sm" onclick="goProject(${p.id})">Open</button>
      ${p.status === 'active'
        ? `<button class="btn-sm" onclick="setProjStatus(${p.id},'done')">Mark done</button>`
        : `<button class="btn-sm" onclick="setProjStatus(${p.id},'active')">Reopen</button>`}
      <button class="btn-sm btn-danger" onclick="deleteProject(${p.id},'${esc(p.name).replace(/'/g, "\\'")}')">Delete</button>
    </div>
  </div>`;
}

async function setProjStatus(id, status) {
  try { await api(`/api/projects/${id}`, { method: 'PATCH', body: { status } }); toast('Updated.'); render(); }
  catch (e) { toast(e.message, true); }
}

function deleteProject(id, name) {
  modal(`<h2>Delete “${esc(name)}”?</h2>
    <p class="small muted">Archiving keeps the history so it still counts toward what PromptMeter has
      learned about your usage. Deleting removes it and its iterations permanently.</p>
    <div class="row" style="justify-content:flex-end">
      <button onclick="closeModal()">Cancel</button>
      <button onclick="reallyDelete(${id},false)">Archive</button>
      <button class="btn-danger" onclick="reallyDelete(${id},true)">Delete permanently</button>
    </div>`);
}

async function reallyDelete(id, hard) {
  try {
    await api(`/api/projects/${id}?hard=${hard ? 1 : 0}`, { method: 'DELETE' });
    closeModal(); toast(hard ? 'Deleted.' : 'Archived.');
    if (S.route === 'project') go('projects'); else render();
  } catch (e) { toast(e.message, true); }
}

/* ----------------------------------------------------- project detail */

// Keyed by project id — a bare global here meant opening Project A's Step
// graph tab, then navigating to Project B, silently landed on B's graph tab
// too. Each project now remembers its own last-viewed tab independently.
const projTabs = {};
function currentProjTab() { return projTabs[S.id] || 'steps'; }
function setProjTab(id, tab) { projTabs[id] = tab; render(); }

async function viewProject() {
  const p = await api(`/api/projects/${S.id}`);
  S.cache.project = p;
  const done = p.progress >= 1;
  const running = p.steps.find(s => s.status === 'running');
  const nextPending = p.steps.find(s => s.status === 'pending');
  return `
  <div class="page-head">
    <div style="min-width:0">
      <div class="small muted"><a href="#" onclick="go('projects');return false">Projects</a> ›</div>
      <h1>${esc(p.name)}</h1>
      <div class="sub row wrap">${p.is_demo ? '<span class="chip">sample data</span>' : ''}
        ${riskChip(p.risk_band, p.risk)}
        ${modelChip(p.model)}
        <span class="chip">${esc(p.task_class.replace(/_/g, ' '))}</span>
        ${p.workdir ? `<span class="chip mono">${esc(p.workdir)}</span>` : ''}</div>
    </div>
    <div class="row">
      ${!running && nextPending
        ? `<button class="btn-sm btn-primary" onclick="setStepStatus(${nextPending.id},'running')">Start next step</button>` : ''}
      ${p.status === 'active'
        ? `<button class="btn-sm" onclick="setProjStatus(${p.id},'done')">Mark done</button>`
        : `<button class="btn-sm" onclick="setProjStatus(${p.id},'active')">Reopen</button>`}
      <button class="btn-sm btn-danger" onclick="deleteProject(${p.id},'${esc(p.name).replace(/'/g, "\\'")}')">Delete</button>
    </div>
  </div>

  <div class="card">
    <div class="spread">
      <div><div class="label">Progress</div>
        <div class="stat-value tnum">${(p.progress * 100).toFixed(0)}%</div></div>
      <dl class="kv">
        <dt>Steps done</dt><dd>${p.done} of ${p.total}</dd>
        <dt>Deliverables verified</dt><dd>${p.satisfied} of ${p.total}</dd>
        <dt>Spent</dt><dd>${p.spent > p.est_p95 && p.est_p95 > 0
          ? `<span style="color:var(--critical)">${usd(p.spent)}</span>`
          : p.spent > p.est ? `<span style="color:var(--serious)">${usd(p.spent)}</span>` : usd(p.spent)}
          <span class="muted">of ~${usd(p.est)} (worst ${usd(p.est_p95)})</span>
          ${p.spent > p.est_p95 && p.est_p95 > 0 ? '<span class="chip critical" style="margin-left:6px">past worst case</span>' : ''}</dd>
        ${isClaudeModel(p.model) ? `<dt>Window used</dt><dd>${p.pp5_spent.toFixed(1)}% of a 5-hour window</dd>` : ''}
      </dl>
    </div>
    <div class="mt12">${progress(p.progress, done)}</div>
  </div>

  <div class="tabs mt16">
    ${[['steps', 'Steps'], ['graph', 'Step graph'], ['prompt', 'Original prompt']].map(([k, l]) =>
      `<button class="tab ${currentProjTab() === k ? 'active' : ''}" onclick="setProjTab(${p.id},'${k}')">${l}</button>`).join('')}
  </div>
  <div id="proj-tab">${currentProjTab() === 'steps' ? renderSteps(p)
    : currentProjTab() === 'graph' ? `<div id="graph-host" class="card">Loading graph…</div>`
    : `<div class="card"><div class="prompt-box">${esc(p.prompt)}</div></div>`}</div>`;
}

function renderSteps(p) {
  if (!p.steps.length) return `<div class="card"><div class="empty">No steps.</div></div>`;
  return p.steps.map((s, i) => renderStep(s, i, p)).join('');
}

const SAT = {
  1:  { txt: 'Deliverable satisfied', cls: 'good',     color: 'var(--good)' },
  0:  { txt: 'Not checked yet',       cls: '',         color: 'var(--baseline)' },
  '-1': { txt: 'Check failed',        cls: 'critical', color: 'var(--critical)' },
};

function renderStep(s, i, p) {
  const sat = SAT[String(s.satisfied)] || SAT[0];
  const open = S.cache.openStep === s.id;
  const iters = s.iterations || [];
  const last = iters[iters.length - 1];
  const critical = s.risk_band === 'red' || s.risk_band === 'critical';

  return `
  <div class="step ${s.status === 'running' ? 'active' : ''} ${open ? 'open' : ''}">
    <div class="step-head" onclick="toggleStep(${s.id})" style="cursor:pointer">
      <div class="row" style="min-width:0;align-items:flex-start">
        <input type="checkbox" title="Mark done" style="width:auto;margin-top:2px"
          ${s.status === 'done' ? 'checked' : ''}
          onclick="event.stopPropagation()"
          onchange="setStepStatus(${s.id}, this.checked ? 'done' : 'pending')">
        <span class="step-num">${i + 1}</span>
        <div style="min-width:0">
          <div class="row wrap">
            <strong>${esc(s.title)}</strong>
            <span class="chip"><span class="swatch" style="background:${STATUS_COLOR[s.status]}"></span>${s.status}</span>
            <span class="chip ${sat.cls}"><span class="swatch" style="background:${sat.color}"></span>${sat.txt}</span>
            ${riskChip(s.risk_band, s.risk)}
          </div>
          ${s.summary ? `<div class="small muted mt8" style="max-width:70ch"><strong>So far:</strong> ${esc(s.summary)}</div>` : ''}
        </div>
      </div>
      <div style="text-align:right;white-space:nowrap">
        <div class="small tnum">${s.spent > s.est_cost_p95 && s.est_cost_p95 > 0
          ? `<span style="color:var(--critical)">${usd(s.spent)}</span>` : usd(s.spent)}
          <span class="muted">/ ~${usd(s.est_cost_p50)}</span></div>
        <div class="small muted">${iters.length} iteration${iters.length === 1 ? '' : 's'} · ${esc(modelLabel(s.model))}</div>
        <div class="small muted">${open ? 'click to collapse' : 'click to open'}</div>
      </div>
    </div>
    <div class="mt8">${progress(s.progress, s.status === 'done')}</div>

    <div class="step-body">
      ${critical && iters.length ? `<div class="banner critical" style="margin-bottom:12px">
        <strong>High-risk step.</strong> The summaries below are folded into the next prompt so an
        interrupted run resumes instead of starting over.</div>` : ''}

      <div class="grid grid-2" style="gap:14px">
        <div>
          <div class="label">Prompt to send next</div>
          <div class="prompt-box mt8">${esc(s.next_prompt)}</div>
          <div class="row mt8">
            <button class="btn-sm" onclick="copyNext(${s.id})">Copy prompt</button>
            <button class="btn-sm" onclick="logIteration(${s.id})">Log an iteration</button>
          </div>
        </div>
        <div>
          <div class="label">Deliverable check</div>
          <div class="row mt8" style="gap:6px">
            <select id="ok-${s.id}" style="flex:1">
              ${(p.oracles || []).map(o => `<option value="${o.id}" ${o.id === s.oracle_kind ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}
            </select>
          </div>
          <div class="mt8"><input id="os-${s.id}" value="${esc(s.oracle_spec)}"
            placeholder="path, command, or file.txt::pattern"></div>
          ${(s.oracle_kind === 'command' || s.oracle_kind === 'tests') && !s.approved ? `
          <div class="banner warning mt8">
            <strong>This check runs a shell command.</strong> Confirm once before it can run —
            saving a different command or path here will ask again.
            <div class="mt8"><button class="btn-sm btn-primary" onclick="approveOracle(${s.id})">Approve and allow it to run</button></div>
          </div>` : ''}
          <div class="row mt8">
            <button class="btn-sm" onclick="saveOracle(${s.id})">Save</button>
            <button class="btn-sm" onclick="runCheck(${s.id})">Run check</button>
            <button class="btn-sm" onclick="markSatisfied(${s.id},1)">Mark satisfied</button>
          </div>
          ${s.satisfied_note ? `<div class="small muted mt8">${esc(s.satisfied_note)}</div>` : ''}
          <div class="mt12">
            <div class="label">Guards</div>
            <dl class="kv mt8">
              <dt>Iteration cap</dt><dd>${iters.length} / ${s.max_iters}</dd>
              <dt>Budget (P95)</dt><dd>${usd(s.est_cost_p95)}</dd>
              <dt>Expected turns</dt><dd>${s.est_turns_p50} – ${s.est_turns_p95}</dd>
            </dl>
          </div>
          ${s.artifacts.length ? `<div class="mt12"><div class="label">Artifacts</div>
            <div class="row wrap mt8">${s.artifacts.map(a => `<span class="chip mono">${esc(a)}</span>`).join('')}</div></div>` : ''}
        </div>
      </div>

      ${iters.length ? `<div class="mt16">
        <div class="label">Iterations — what each pass produced</div>
        <div class="mt8">${iters.map(it => `
          <div class="iter">
            <div class="iter-n ${it.verdict === 'pass' ? 'pass' : it.verdict === 'fail' ? 'fail' : ''}">${it.n}</div>
            <div style="min-width:0;flex:1">
              <div>${esc(it.summary || '(no summary recorded)')}</div>
              <div class="small muted tnum mt8">${tokens(it.out_tokens)} out · ${usd(it.cost_usd)}
                ${isClaudeModel(it.model) ? `· ${it.pp5_delta.toFixed(1)}% of a 5h window ` : ''}· ${ago(it.created_at)}</div>
            </div>
          </div>`).join('')}</div>
      </div>` : `<div class="small muted mt16">No iterations logged yet.</div>`}

      <div class="row mt16" style="justify-content:space-between">
        <div class="row">
          <button class="btn-sm" onclick="setStepStatus(${s.id},'running')">Start</button>
          <button class="btn-sm" onclick="setStepStatus(${s.id},'done')">Done</button>
          <button class="btn-sm" onclick="setStepStatus(${s.id},'skipped')">Skip</button>
        </div>
        <button class="btn-sm" onclick="resplit(${s.id})">Split this step further</button>
      </div>
    </div>
  </div>`;
}

function toggleStep(id) {
  S.cache.openStep = S.cache.openStep === id ? null : id;
  render();
}

async function copyNext(id) {
  const s = (S.cache.project.steps || []).find(x => x.id === id);
  if (!s) return;
  try { await navigator.clipboard.writeText(s.next_prompt); toast('Prompt copied.'); }
  catch { toast('Could not copy. Select the text and copy manually.', true); }
}

function logIteration(id) {
  const s = (S.cache.project.steps || []).find(x => x.id === id);
  const critical = s && (s.risk_band === 'red' || s.risk_band === 'critical');
  modal(`<h2>Log an iteration</h2>
    <p class="small muted">Record what that pass actually did and cost. This is what teaches
      PromptMeter your real usage${critical ? ', and on a high-risk step the summary is folded into the next prompt so nothing is redone' : ''}.</p>
    <div class="field"><label>Summary of what was done${critical ? ' (required on high-risk steps)' : ''}</label>
      <textarea id="it-sum" style="min-height:70px" placeholder="e.g. Added the search endpoint; results still unsorted."></textarea></div>
    <div class="grid grid-2" style="gap:10px">
      <div class="field"><label>Input tokens</label><input id="it-in" type="number" min="0" placeholder="0"></div>
      <div class="field"><label>Output tokens</label><input id="it-out" type="number" min="0" placeholder="0"></div>
      <div class="field"><label>Cache read</label><input id="it-cr" type="number" min="0" placeholder="0"></div>
      <div class="field"><label>Cache write</label><input id="it-cw" type="number" min="0" placeholder="0"></div>
    </div>
    <div class="field"><label>How did it go?</label>
      <select id="it-v"><option value="unknown">Not sure yet</option>
        <option value="pass">Worked</option><option value="fail">Did not work</option></select></div>
    <p class="small muted">Token counts come from <span class="mono">/usage</span> in Claude Code.
      Leave them blank if you do not have them — the summary alone is still useful.</p>
    <div class="row" style="justify-content:flex-end">
      <button onclick="closeModal()">Cancel</button>
      <button class="btn-primary" onclick="saveIteration(${id})">Log it</button>
    </div>`);
}

async function saveIteration(id) {
  const v = q => Number(document.getElementById(q).value || 0);
  try {
    const r = await api(`/api/steps/${id}/iterate`, {
      method: 'POST',
      body: {
        summary: document.getElementById('it-sum').value,
        in_tokens: v('it-in'), out_tokens: v('it-out'),
        cache_read: v('it-cr'), cache_write: v('it-cw'),
        verdict: document.getElementById('it-v').value,
      },
    });
    closeModal();
    toast(r.stop ? r.stop : 'Iteration logged.', !!r.stop);
    render();
  } catch (e) { toast(e.message, true); }
}

async function saveOracle(id) {
  try {
    await api(`/api/steps/${id}`, {
      method: 'PATCH',
      body: {
        oracle_kind: document.getElementById('ok-' + id).value,
        oracle_spec: document.getElementById('os-' + id).value,
      },
    });
    toast('Check saved.'); render();
  } catch (e) { toast(e.message, true); }
}

async function approveOracle(id) {
  try { await api(`/api/steps/${id}`, { method: 'PATCH', body: { approved: 1 } }); toast('Approved.'); render(); }
  catch (e) { toast(e.message, true); }
}

async function runCheck(id) {
  try {
    const r = await api(`/api/steps/${id}/check`, { method: 'POST' });
    toast(r.result.note, r.result.satisfied === -1);
    render();
  } catch (e) { toast(e.message, true); }
}

async function markSatisfied(id, val) {
  try { await api(`/api/steps/${id}/satisfy`, { method: 'POST', body: { satisfied: val } }); toast('Marked.'); render(); }
  catch (e) { toast(e.message, true); }
}

async function setStepStatus(id, status) {
  try { await api(`/api/steps/${id}`, { method: 'PATCH', body: { status } }); render(); }
  catch (e) { toast(e.message, true); }
}

async function resplit(id) {
  try { await api(`/api/steps/${id}/resplit`, { method: 'POST' }); toast('Split.'); render(); }
  catch (e) { toast(e.message, true); }
}

/* -------------------------------------------------------- step graph */

async function drawGraph() {
  const host = document.getElementById('graph-host');
  if (!host) return;
  const g = await api(`/api/graph/${S.id}`);
  if (!g.nodes.length) { host.innerHTML = `<div class="empty">No steps to graph.</div>`; return; }

  const byStage = {};
  g.nodes.forEach(n => (byStage[n.stage] = byStage[n.stage] || []).push(n));
  const stages = Object.keys(byStage).map(Number).sort((a, b) => a - b);

  const NW = 208, NH = 92, GX = 74, GY = 22;
  const pos = {};
  stages.forEach((st, si) => byStage[st].forEach((n, i) => {
    pos[n.id] = { x: 20 + si * (NW + GX), y: 20 + i * (NH + GY) };
  }));
  const W = 40 + stages.length * (NW + GX) - GX;
  const H = 40 + Math.max(...stages.map(s => byStage[s].length)) * (NH + GY) - GY;

  const edge = e => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return '';
    const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2;
    const mx = (x1 + x2) / 2;
    return `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" fill="none"
      stroke="var(--baseline)" stroke-width="${e.implicit ? 1 : 2}"
      ${e.implicit ? 'stroke-dasharray="0"' : ''} opacity="${e.implicit ? .45 : .9}"
      marker-end="url(#arrow)"/>`;
  };

  const node = n => {
    const p = pos[n.id];
    const col = STATUS_COLOR[n.status] || 'var(--baseline)';
    const satCol = n.satisfied === 1 ? 'var(--good)' : n.satisfied === -1 ? 'var(--critical)' : 'var(--baseline)';
    const satTxt = n.satisfied === 1 ? 'satisfied' : n.satisfied === -1 ? 'failed check' : 'unverified';
    const pw = Math.max(0, Math.min(1, n.progress)) * (NW - 24);
    return `<g class="gnode" transform="translate(${p.x},${p.y})" onclick="openFromGraph(${n.id})">
      <rect width="${NW}" height="${NH}" rx="9" fill="var(--surface-1)" stroke="var(--border)" stroke-width="1"/>
      <rect width="4" height="${NH}" rx="2" fill="${col}"/>
      <text x="14" y="21" font-size="12.5" font-weight="600" fill="var(--text-primary)"
        font-family="var(--font)">${esc(n.title.slice(0, 24))}${n.title.length > 24 ? '…' : ''}</text>
      <text x="14" y="38" font-size="10.5" fill="var(--text-muted)" font-family="var(--font)">
        ${n.iterations} iter · ${n.spent ? '$' + n.spent.toFixed(2) : '$0.00'} · ${esc(shortModel(n.model))}</text>
      <text x="14" y="54" font-size="10.5" fill="var(--text-secondary)" font-family="var(--font)">
        ${esc((n.summary || 'no output recorded').slice(0, 30))}${(n.summary || '').length > 30 ? '…' : ''}</text>
      <rect x="12" y="${NH - 24}" width="${NW - 24}" height="5" rx="2.5" fill="var(--track-warm)"/>
      <rect x="12" y="${NH - 24}" width="${pw}" height="5" rx="2.5" fill="${col}"/>
      <circle cx="${NW - 16}" cy="20" r="5" fill="${satCol}"/>
      <title>${esc(n.title)} — ${satTxt}
${esc((n.prompt || '').slice(0, 300))}</title>
    </g>`;
  };

  host.innerHTML = `
    <div class="card-head"><h2>Step graph</h2>
      <span class="hint">Which prompt produced what, and whether the deliverable held</span></div>
    <div class="legend" style="margin-bottom:12px">
      <span class="k"><span class="sw" style="background:var(--good)"></span>done / satisfied</span>
      <span class="k"><span class="sw" style="background:var(--accent)"></span>running</span>
      <span class="k"><span class="sw" style="background:var(--critical)"></span>failed check</span>
      <span class="k"><span class="sw" style="background:var(--baseline)"></span>pending / unverified</span>
    </div>
    <div class="graph-wrap">
      <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="min-width:${W}px">
        <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5"
          markerHeight="5" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="var(--baseline)"/></marker></defs>
        ${g.edges.map(edge).join('')}
        ${g.nodes.map(node).join('')}
      </svg>
    </div>
    <div class="small muted mt12">The dot on each card is the deliverable verdict; the bar is progress.
      Hover a card to see the prompt it sends, click it to open the step.</div>`;
}

function shortModel(id) {
  return (modelLabel(id) || id).replace('Claude ', '');
}

function openFromGraph(id) {
  projTabs[S.id] = 'steps'; S.cache.openStep = id; render();
  setTimeout(() => document.querySelector('.step.open')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 120);
}

// The calibration scatter — predicted cost on x, what it actually cost on y.
// A point on the dashed diagonal was predicted exactly right; above it, the
// estimate ran hot; below, cold. Colour carries only status (good/critical,
// already reserved for exactly this meaning elsewhere in the app), never
// identity, and the table beside this chart gives every point a label too —
// colour is never the only way to read a result, per the dataviz guidance
// this redesign followed for every new chart.
function scatterCal(acc, w = 520, h = 260) {
  const pts = acc.filter(a => a.predicted > 0 || a.actual > 0);
  if (pts.length < 2) return `<div class="small muted">Not enough completed steps yet — this fills in as steps finish.</div>`;
  const pad = 40;
  const maxV = Math.max(...pts.map(p => Math.max(p.predicted, p.actual)), 0.01) * 1.08;
  const X = v => pad + (v / maxV) * (w - pad - 14);
  const Y = v => h - pad - (v / maxV) * (h - pad - 14);
  const grid = [0.25, 0.5, 0.75, 1].map(f => {
    const v = maxV * f, y = Y(v).toFixed(1);
    return `<line x1="${X(0).toFixed(1)}" y1="${y}" x2="${w - 14}" y2="${y}" stroke="var(--grid)" stroke-width="1"/>
      <text x="${(X(0) - 6).toFixed(1)}" y="${Y(v) + 3}" text-anchor="end" font-size="9.5"
        fill="var(--text-muted)" font-family="var(--mono)">${usd(v)}</text>`;
  }).join('');
  const dots = pts.map(p => `<circle class="scatter-dot" cx="${X(p.predicted).toFixed(1)}" cy="${Y(p.actual).toFixed(1)}"
    r="4" fill="${p.within_band ? 'var(--good)' : 'var(--critical)'}" fill-opacity="0.85">
    <title>${esc(p.title)} — ${esc(p.project)} — predicted ${usd(p.predicted)}, actual ${usd(p.actual)}</title>
  </circle>`).join('');
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="Predicted versus actual cost per completed step">
    ${grid}
    <line x1="${X(0).toFixed(1)}" y1="${Y(0).toFixed(1)}" x2="${X(maxV).toFixed(1)}" y2="${Y(maxV).toFixed(1)}"
      stroke="var(--baseline)" stroke-width="1.2" stroke-dasharray="3 3"/>
    <line x1="${X(0).toFixed(1)}" y1="${(h - pad).toFixed(1)}" x2="${X(0).toFixed(1)}" y2="10" stroke="var(--grid)" stroke-width="1"/>
    ${dots}
    <text x="${w / 2}" y="${h - 8}" text-anchor="middle" font-size="10.5" fill="var(--text-secondary)" font-family="var(--font)">Predicted cost</text>
    <text x="12" y="16" font-size="10.5" fill="var(--text-secondary)" font-family="var(--font)">Actual</text>
  </svg>
  <div class="legend mt8">
    <span class="k"><span class="sw" style="background:var(--good)"></span>within band</span>
    <span class="k"><span class="sw" style="background:var(--critical)"></span>over worst case</span>
    <span class="k"><span class="sw" style="background:var(--baseline)"></span>predicted exactly right</span>
  </div>`;
}

/* ------------------------------------------------------------ history */

async function viewHistory() {
  const h = await api('/api/history');
  const acc = h.accuracy;
  const within = acc.filter(a => a.within_band).length;
  return `
  <div class="page-head">
    <div><h1>History</h1>
      <div class="sub">Everything you have run, and what PromptMeter has learned from it.</div></div>
  </div>

  <div class="grid grid-3">
    <div class="card"><div class="label">Projects</div>
      <div class="stat-value tnum">${h.projects.length}</div>
      <div class="small muted">${h.projects.filter(p => p.status === 'active').length} active</div></div>
    <div class="card"><div class="label">Estimates inside the band</div>
      <div class="stat-value tnum">${acc.length ? Math.round(within / acc.length * 100) + '%' : '—'}</div>
      <div class="small muted">${within} of ${acc.length} completed steps came in under the worst case</div></div>
    <div class="card"><div class="label">Total spent</div>
      <div class="stat-value tnum">${usd(h.projects.filter(p => !p.is_demo).reduce((s, p) => s + (p.spent || 0), 0))}</div>
      <div class="small muted">list-price equivalent · sample data excluded</div></div>
  </div>

  <div class="card mt16">
    <div class="card-head"><h2>What each model actually costs you</h2>
      <span class="hint">measured, not estimated</span></div>
    ${h.models.length ? `<table>
      <thead><tr><th>Model</th><th class="num">Iterations</th><th class="num">Output</th>
        <th class="num">Cache read</th><th class="num">Spent</th><th class="num">% of 5h window</th></tr></thead>
      <tbody>${h.models.map(m => `<tr>
        <td>${modelChip(m.model, m.label)}</td>
        <td class="num">${m.iters}</td><td class="num">${tokens(m.out_tokens)}</td>
        <td class="num">${tokens(m.cache_read)}</td><td class="num">${usd(m.cost)}</td>
        <td class="num">${isClaudeModel(m.model) ? m.pp5.toFixed(1) + '%' : '—'}</td></tr>`).join('')}</tbody></table>`
      : `<div class="empty">
          <div class="big">Nothing logged yet</div>
          <div class="small">Run a project and log an iteration. Sample data is deliberately kept out of this
            table, so it only ever shows your real runs — loading samples won't fill it.</div>
          <div class="mt12"><button class="btn-primary" onclick="go('plan')">Plan a prompt</button></div>
        </div>`}
  </div>

  <div class="card">
    <div class="card-head"><h2>What it has learned about each kind of task</h2>
      <span class="hint">priors give way to your own numbers as runs accumulate</span></div>
    <table>
      <thead><tr><th>Task type</th><th class="num">Turns (typical / worst)</th>
        <th class="num">Output per turn</th><th class="num">Context growth</th>
        <th class="num">Runs seen</th><th>Basis</th></tr></thead>
      <tbody>${h.classes.map(c => `<tr>
        <td>${esc(c.label)}</td>
        <td class="num">${c.turns_p50} / ${c.turns_p95}</td>
        <td class="num">${tokens(c.out_p50)} / ${tokens(c.out_p95)}</td>
        <td class="num">${tokens(c.growth)}</td>
        <td class="num">${c.n}</td>
        <td><span class="chip">${esc(c.confidence)}</span></td></tr>`).join('')}</tbody></table>
  </div>

  <div class="card">
    <div class="card-head"><h2>Predicted vs actual</h2>
      <span class="hint">the credibility check</span></div>
    ${acc.length ? `${scatterCal(acc)}<table style="margin-top:16px">
      <thead><tr><th>Step</th><th>Project</th><th class="num">Predicted</th>
        <th class="num">Worst case</th><th class="num">Actual</th><th>Verdict</th></tr></thead>
      <tbody>${acc.slice(0, 30).map(a => `<tr>
        <td>${esc(a.title)}</td><td class="muted">${esc(a.project)}</td>
        <td class="num">${usd(a.predicted)}</td><td class="num">${usd(a.p95)}</td>
        <td class="num">${usd(a.actual)}</td>
        <td>${a.within_band
          ? `<span class="chip good"><span class="swatch" style="background:var(--good)"></span>in band</span>`
          : `<span class="chip critical"><span class="swatch" style="background:var(--critical)"></span>over</span>`}</td>
      </tr>`).join('')}</tbody></table>`
      : `<div class="empty">
          <div class="big">No completed steps yet</div>
          <div class="small">Finish a step with a logged iteration and its predicted-vs-actual cost shows up here.</div>
        </div>`}
  </div>

  <div class="card">
    <div class="card-head"><h2>All projects</h2></div>
    <table>
      <thead><tr><th>Project</th><th class="num">Progress</th><th class="num">Steps</th>
        <th class="num">Spent</th><th>Status</th><th></th></tr></thead>
      <tbody>${h.projects.map(p => `<tr>
        <td><a href="#" onclick="goProject(${p.id});return false">${esc(p.name)}</a>
          ${p.is_demo ? ' <span class="chip">sample</span>' : ''}
          <div class="small muted">${ago(p.updated_at)}</div></td>
        <td class="num">${(p.progress * 100).toFixed(0)}%</td>
        <td class="num">${p.done}/${p.total}</td>
        <td class="num">${usd(p.spent)}</td>
        <td><span class="chip">${esc(p.status)}</span></td>
        <td><button class="btn-sm btn-danger" onclick="deleteProject(${p.id},'${esc(p.name).replace(/'/g, "\\'")}')">Delete</button></td>
      </tr>`).join('') || `<tr><td colspan="6" class="muted">Nothing yet.</td></tr>`}</tbody></table>
  </div>`;
}

/* -------------------------------------------------------------- setup */

async function viewSetup() {
  const s = S.status || await api('/api/status');
  const c = await api('/api/setup').catch(e => ({ error: e.message }));
  S.cache.setup = c;

  if (c.error) {
    return `<div class="page-head"><div><h1>Setup</h1></div></div>
      <div class="card"><div class="banner critical">${esc(c.error)}</div></div>`;
  }

  const w = c.watcher || { files: 0, turns: 0, sessions: 0, root: '', root_exists: false, projects: [] };
  const pv = await api('/api/providers').catch(() => ({ active: 'heuristic', keys: {}, registry: [] }));
  S.cache.providers = pv;
  updateSidebarTagline(pv);
  c.capacity_basis = (s.capacity || {}).confidence;
  c.capacity_usd5 = (s.capacity || {}).usd_per_5h;

  const banner = w.turns === 0
    ? ['warning', `<strong>No usage recorded yet.</strong> Claude Code sessions are picked up on their own; connect any other agent below.`]
    : c.capacity_basis === 'measured'
      ? ['good', `<strong>All set.</strong> ${num(w.turns)} turns tracked from Claude Code, and your plan window is measured.`]
      : ['good', `<strong>Tracking automatically.</strong> ${num(w.turns)} turns recorded from Claude Code with no setup. Using another agent? Connect it below.`];

  return `
  <div class="page-head">
    <div><h1>Setup</h1>
      <div class="sub">Connect the agents you use, and add any model that is not in the list.</div></div>
  </div>

  <div class="banner ${banner[0]}">${banner[1]}</div>

  <details class="card mt16">
    <summary><strong>Claude Code session tracking</strong>
      <span class="small muted">— ${num(w.turns)} turns, ${w.sessions} session${w.sessions === 1 ? '' : 's'}, last checked ${w.last_scan ? ago(w.last_scan) : 'never'}</span></summary>
    <p class="small mt12">Claude Code writes every turn to a session file on this machine; PromptMeter
      reads those files with nothing to configure. This is one automatic source — other agents report
      their usage in the card below.</p>
    <div class="row mt8">
      <button class="btn-sm" onclick="doScan(false)">Check now</button>
      <button class="btn-sm" onclick="doScan(true)">Re-read everything</button>
    </div>
    ${w.turns === 0 ? `<div class="banner warning mt12">
      ${w.root_exists
        ? 'The folder exists but has no session files with usage in them yet. Send one message in Claude Code, then press “Check now”.'
        : `Nothing at <span class="mono">${esc(w.root)}</span> yet — that folder appears the first time you use Claude Code on this machine.`}
    </div>` : ''}
    ${w.projects && w.projects.length ? `<details class="mt12">
      <summary>Which folders it is seeing</summary>
      <table><thead><tr><th>Project folder</th><th class="num">Turns</th>
        <th class="num">Cost</th><th class="num">Last activity</th></tr></thead>
      <tbody>${w.projects.map(pr => `<tr><td class="mono">${esc(pr.project)}</td>
        <td class="num">${num(pr.turns)}</td><td class="num">${usd(pr.cost)}</td>
        <td class="num">${ago(pr.last)}</td></tr>`).join('')}</tbody></table>
    </details>` : ''}
  </details>

  <div class="card">
    <div class="card-head"><h2>Connect any agent</h2>
      <span class="hint">Codex, Gemini CLI, Cursor, Aider, your own scripts — anything</span></div>
    <p class="small" style="max-width:68ch">PromptMeter does not care which assistant did the work. An agent
      (or a hook or wrapper around it) reports each turn, and it is priced from the same catalogue and counted
      in projects, History and Usage exactly like a Claude Code turn. Nothing leaves this machine.</p>
    <div class="label mt12">From a command line or hook</div>
    <div class="prompt-box mt8" id="usage-cli">python -m promptmeter --log-usage --model gpt-5.3-codex --in-tokens 12000 --out-tokens 900 --agent codex</div>
    <div class="row mt8"><button class="btn-sm" onclick="copyText('usage-cli','Command copied.')">Copy</button></div>
    <div class="label mt12">Or over HTTP while the app is running</div>
    <div class="prompt-box mt8" id="usage-http">POST /api/usage   {"model": "gpt-5.3-codex", "in_tokens": 12000, "out_tokens": 900, "agent": "codex"}
header  X-PromptMeter-Token: (the csrf_token from GET /api/status)
optional  cost_usd, cache_read, cache_write, cwd, ts, id (makes a retry safe), or {"records": [ ... ]}</div>
    <div class="small muted mt8">Not sure of a token count? Send <span class="mono">cost_usd</span> and the
      turn is recorded at that price. A model PromptMeter has not heard of is flagged “unpriced” rather than
      silently treated as Claude — add its price below.</div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Your own models</h2>
      <span class="hint">any model, any provider — Mistral, DeepSeek, Grok, a self-hosted one</span></div>
    <p class="small" style="max-width:68ch">Add a model and it appears in the Plan screen's picker and is priced
      correctly everywhere. You only need its name and its price per million tokens.</p>
    ${customModelsList()}
    <div class="grid grid-2 mt12" style="gap:10px;max-width:760px">
      <div class="field"><label>Model id (as the agent reports it)</label><input id="cm-id" placeholder="mistral-large-latest"></div>
      <div class="field"><label>Display name</label><input id="cm-label" placeholder="Mistral Large"></div>
      <div class="field"><label>Vendor</label><input id="cm-vendor" placeholder="Mistral"></div>
      <div class="field"><label>Context window (tokens)</label><input id="cm-context" type="number" min="1024" placeholder="128000"></div>
      <div class="field"><label>Input price, $ per million tokens</label><input id="cm-in" type="number" min="0" step="any" placeholder="2"></div>
      <div class="field"><label>Output price, $ per million tokens</label><input id="cm-out" type="number" min="0" step="any" placeholder="6"></div>
    </div>
    <div class="row"><button class="btn-primary btn-sm" onclick="saveCustomModel()">Add model</button></div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Claude Code live status line</h2>
      <span class="hint">Claude Code terminal only · optional</span></div>

    <p class="small">Optional extra precision for the terminal: Claude Code hands PromptMeter the exact
      percentages, second by second, instead of the estimate automatic tracking already gives you.</p>

    <div class="steps-num mt12">
      <div class="stepn">
        <div class="stepn-n">1</div>
        <div style="flex:1;min-width:0">
          <strong>Write the setting</strong>
          <div class="small muted mt8">One line in <span class="mono">${esc(c.settings_path)}</span>${
            c.settings_exists ? ', with a backup of your current file.' : ' — it will be created.'}</div>
          ${c.conflict ? `<div class="banner warning mt12">
            <strong>You already have a status line:</strong>
            <div class="prompt-box mt8">${esc(c.current_command)}</div></div>` : ''}
          <div class="row mt12">
            <button class="btn-primary" onclick="doInstall(${c.conflict ? 'true' : 'false'})">
              ${c.installed ? 'Write it again' : c.conflict ? 'Replace it' : 'Write the setting'}</button>
            ${c.installed ? `<button onclick="doUninstall()">Undo</button>` : ''}
            <button onclick="toggleManual()">Show me the file instead</button>
          </div>
        </div>
      </div>
      <div class="stepn">
        <div class="stepn-n">2</div>
        <div style="flex:1;min-width:0">
          <strong>Check that it runs</strong>
          <div class="small muted mt8">Runs the exact command Claude Code will run, so a missing Python
            shows up now rather than later.</div>
          <div class="row mt12"><button onclick="doTest()">Test it</button></div>
          <div id="test-out" class="mt12"></div>
        </div>
      </div>
      <div class="stepn">
        <div class="stepn-n">3</div>
        <div style="flex:1;min-width:0">
          <strong>Restart the terminal Claude Code</strong>
          <div class="small muted mt8">It only reads settings at startup. Send one message and a usage
            bar appears at the bottom of the session.</div>
          <div class="row mt12"><button onclick="render()">Check again</button></div>
        </div>
      </div>
    </div>

    <div id="manual" style="display:none" class="mt16">
      <div class="card" style="background:var(--page)">
        <h3>The exact file</h3>
        <p class="small muted mt8">Open <span class="mono">${esc(c.settings_path)}</span>, select
          everything in it, and replace it with this. The paths are already yours — copy, do not
          retype, and never paste anything containing a placeholder like
          <span class="mono">&lt;this folder&gt;</span>.</p>
        <div class="prompt-box mt8" id="full-json">${esc(c.full_file)}</div>
        <div class="row mt8">
          <button class="btn-sm" onclick="copyText('full-json','Whole file copied.')">Copy the whole file</button>
        </div>
      </div>
    </div>

    <details class="mt16">
      <summary>What exactly gets written, and where</summary>
      <dl class="kv">
        <dt>Settings file</dt><dd class="mono">${esc(c.settings_path)}</dd>
        <dt>Python it will use</dt><dd class="mono">${esc(c.python)}</dd>
        <dt>Script it will run</dt><dd class="mono">${esc(c.shim_path)}</dd>
        ${c.backup ? `<dt>Latest backup</dt><dd class="mono">${esc(c.backup)}</dd>` : ''}
      </dl>
      <p class="small muted mt12">Only the <span class="mono">statusLine</span> key is touched.
        Every other setting you have is preserved exactly.</p>
    </details>
  </div>

  <div class="card">
    <div class="card-head"><h2>Connect a provider</h2>
      <span class="hint">optional — every model works without this</span></div>

    <p class="small">Claude, GPT and Gemini models are all already fully usable everywhere in
      PromptMeter — the model picker, the cost and token estimate, the plan — with no key and no
      connection. What connecting a provider here adds is one specific thing: a real model drafts
      your prompt's actual step-by-step plan first, which catches work the wording never spells
      out, instead of PromptMeter guessing scope from keywords. Either way <strong>the model never
      sets a price</strong>: it lists the steps, PromptMeter costs them with its own numbers.</p>

    <div class="field mt12" style="max-width:380px">
      <label>Provider</label>
      <select id="prov-active" onchange="onProviderChange()">
        ${(pv.registry || []).map(r => `<option value="${r.id}" ${r.id === pv.active ? 'selected' : ''}>${esc(r.label)}</option>`).join('')}
      </select>
      <div class="small muted mt8">${esc((pv.registry.find(r => r.id === pv.active) || {}).note || '')}</div>
    </div>

    <div class="row mt8" style="gap:10px;align-items:center">
      <button class="btn-sm" onclick="checkOllama()" id="ollama-check-btn">Check for a local model</button>
      <span class="small muted">Looks for Ollama on this machine and switches to it automatically —
        nothing to type, nothing to pick above first.</span>
    </div>
    <div id="ollama-check-out" class="mt8"></div>

    ${pv.active === 'heuristic' ? '' : `
    <div class="grid grid-2" style="gap:12px;max-width:760px">
      <div class="field"><label>Model</label>
        <input id="prov-model" value="${esc(pv.model || (pv.registry.find(r => r.id === pv.active) || {}).default_model || '')}"></div>
      ${pv.active === 'ollama'
        ? `<div class="field"><label>Ollama address</label>
             <input id="prov-url" value="${esc(pv.base_url || 'http://localhost:11434')}"></div>`
        : `<div class="field"><label>API key${
             (pv.key_storage || {})[pv.active] === 'environment' ? ' — from .env'
             : pv.keys[pv.active] ? ' — saved' : ''}</label>
             <input id="prov-key" type="password" placeholder="${pv.keys[pv.active] ? esc(pv.keys[pv.active]) : 'paste your key'}"></div>`}
    </div>
    <div class="row">
      <button class="btn-primary" onclick="saveProvider()">Save</button>
      <button onclick="testProvider()">Test it</button>
      ${pv.keys[pv.active] && (pv.key_storage || {})[pv.active] !== 'environment'
        ? `<button class="btn-danger" onclick="clearKey()">Remove key</button>` : ''}
    </div>
    ${(pv.key_storage || {})[pv.active] === 'environment' ? `<div class="small muted mt8">
      Using ${esc(ENV_KEY_NAMES[pv.active] || 'a key')} from your <span class="mono">.env</span> file.
      Paste a key above and Save to override it just here.</div>` : ''}
    <div id="prov-test" class="mt12"></div>
    ${pv.active === 'ollama' ? `<div class="banner mt12">
      <strong>Getting Ollama.</strong> Install it from <span class="mono">ollama.com</span>, then in
      PowerShell run <span class="mono">ollama pull ${esc(pv.model || 'llama3.2:3b')}</span>. It runs
      on your own machine — no key, no cost, and nothing leaves the PC. A 3B model needs roughly 4 GB
      of free RAM.</div>` : `<div class="banner mt12">
      <strong>Where the key goes.</strong> A key you paste here is stored in your local database
      at <span class="mono">~/.promptmeter</span>${pv.dpapi_available ? ', encrypted at rest' : ''}
      and sent only to ${esc(pv.active)}. It is never shown back to this page in full and never
      leaves your machine otherwise. You can also set
      <span class="mono">${esc(ENV_KEY_NAMES[pv.active] || '')}</span> in a
      <span class="mono">.env</span> file instead — a key pasted here always takes priority over
      that. Drafting one plan costs a fraction of a cent.</div>`}
    `}

    <label class="row mt16" style="gap:8px;cursor:pointer">
      <input type="checkbox" id="prov-auto" style="width:auto" ${pv.auto ? 'checked' : ''}
        onchange="saveProvider(true)" ${pv.active === 'heuristic' ? 'disabled' : ''}>
      <span class="small">Draft a plan for every prompt automatically${pv.active === 'heuristic'
        ? ' <span class="muted">(needs a model)</span>' : ''}</span>
    </label>
    <div class="small muted mt8">Leave this off to decide per prompt on the Plan page — ambiguous
      asks get a plan, obvious ones do not.</div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Worth knowing</h2></div>
    <table>
      <tr><td><strong>Two windows, not three</strong></td>
        <td>A rolling 5-hour window and a 7-day weekly window, both shared across Claude chat,
          Claude Code and Cowork. Opus has its own separate limit on top.</td></tr>
      <tr><td><strong>Unused percent evaporates</strong></td>
        <td>Plan percent refills and is lost if unspent; money spent on usage credits does not refill.
          That is why the scheduler packs each window full rather than spreading work evenly.</td></tr>
      <tr><td><strong>The credits trap</strong></td>
        <td>Prompt cache lifetime is 1 hour on a subscription and drops to 5 minutes once you draw on
          usage credits — so crossing over quietly makes every turn more expensive.
          Set <span class="mono">ENABLE_PROMPT_CACHING_1H=1</span> to keep the hour.</td></tr>
      <tr><td><strong>Agent teams</strong></td>
        <td>Roughly 7&times; the tokens in plan mode — each teammate carries its own context window.</td></tr>
    </table>
  </div>

  <div class="card">
    <div class="card-head"><h2>Data</h2></div>
    <p class="small muted">Everything lives in one SQLite file in your home folder under
      <span class="mono">.promptmeter</span>. No account, no sync, no network calls.</p>
    <div class="row">
      <button onclick="seedDemo()">Load sample data</button>
      <button onclick="clearDemoData()">Clear sample data</button>
      <button class="btn-danger" onclick="resetAll()">Erase everything</button>
    </div>
    <div class="small muted mt8">“Clear sample data” only removes projects tagged as samples — your
      real projects and history are untouched. “Erase everything” deletes both.</div>
  </div>`;
}

function onProviderChange() {
  const v = document.getElementById('prov-active').value;
  api('/api/providers', { method: 'POST', body: { active: v, model: '' } })
    .then(() => render()).catch(e => toast(e.message, true));
}

async function saveProvider(silent) {
  const g = id => { const el = document.getElementById(id); return el ? el.value : undefined; };
  const auto = document.getElementById('prov-auto');
  try {
    await api('/api/providers', {
      method: 'POST',
      body: {
        active: g('prov-active'), model: g('prov-model'), base_url: g('prov-url'),
        key: g('prov-key') || undefined, auto: auto ? auto.checked : undefined,
      },
    });
    if (!silent) toast('Saved.');
    S.cache.providers = null;
    render();
  } catch (e) { toast(e.message, true); }
}

async function clearKey() {
  try {
    await api('/api/providers', { method: 'POST', body: { key: '' } });
    toast('Key removed.'); render();
  } catch (e) { toast(e.message, true); }
}

async function testProvider() {
  const out = document.getElementById('prov-test');
  out.innerHTML = `<div class="small muted">Asking it to draft a small plan…</div>`;
  try {
    await saveProvider(true);
    const r = await api('/api/providers/test', { method: 'POST' });
    out.innerHTML = r.ok
      ? `<div class="banner good"><strong>Working.</strong> ${esc(r.message)}
          ${r.sample ? `<div class="small muted mt8">Sample steps: ${r.sample.map(esc).join(' · ')}</div>` : ''}</div>`
      : `<div class="banner critical"><strong>Not working.</strong>
          <div class="small mt8">${esc(r.message)}</div></div>`;
  } catch (e) {
    out.innerHTML = `<div class="banner critical">${esc(e.message)}</div>`;
  }
}

// One click: detect a local Ollama and switch to it if it's actually
// answering — never installs it, never pulls a model (a multi-gigabyte
// download is not something a button starts on its own). "Installed but not
// running" and "not found at all" get different, honest messages rather
// than one generic failure, since the fix for each is different.
async function checkOllama() {
  const btn = document.getElementById('ollama-check-btn');
  const out = document.getElementById('ollama-check-out');
  if (btn) btn.disabled = true;
  if (out) out.innerHTML = `<div class="small muted">Checking…</div>`;
  try {
    const r = await api('/api/providers/ollama/check', { method: 'POST' });
    if (r.running) {
      toast(`Found Ollama — connected${r.models.length ? ' (' + r.models[0] + ')' : ''}.`);
      S.cache.providers = null;
      render();
      return;
    }
    if (out) out.innerHTML = r.installed
      ? `<div class="banner warning"><strong>Installed, but not running.</strong> Start Ollama, then
          check again.</div>`
      : `<div class="banner"><strong>Not found on this machine.</strong> Install it from
          <span class="mono">ollama.com</span>, then check again — no address or model name to type in.</div>`;
  } catch (e) {
    if (out) out.innerHTML = `<div class="banner critical">${esc(e.message)}</div>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function customModelsList() {
  const mine = (S.models || []).filter(m => m.custom);
  if (!mine.length) return '<div class="small muted mt8">None yet.</div>';
  return `<table class="mt8"><thead><tr><th>Model</th><th>Vendor</th><th class="num">In $/M</th>
    <th class="num">Out $/M</th><th></th></tr></thead><tbody>${mine.map(m => `<tr>
      <td>${esc(m.label)}<div class="small muted mono">${esc(m.id)}</div></td>
      <td>${esc(m.vendor_label || m.vendor)}</td><td class="num">${esc(String(m.in))}</td>
      <td class="num">${esc(String(m.out))}</td>
      <td class="num"><button class="btn-sm btn-danger" onclick="removeCustomModel('${esc(m.id)}')">Remove</button></td>
    </tr>`).join('')}</tbody></table>`;
}

async function refreshModels() {
  const m = await api('/api/models');
  S.models = m.models; S.vendors = m.vendors || [];
}

async function saveCustomModel() {
  const v = id => (document.getElementById(id) || {}).value || '';
  try {
    const saved = await api('/api/models/custom', { method: 'POST', body: {
      id: v('cm-id').trim(), label: v('cm-label').trim(), vendor: v('cm-vendor').trim(),
      context: v('cm-context'), in: v('cm-in'), out: v('cm-out') } });
    await refreshModels();
    toast(saved.repriced ? `Model added. Repriced ${saved.repriced} earlier turn${saved.repriced === 1 ? '' : 's'}.` : 'Model added.');
    render();
  } catch (e) { toast(e.message, true); }
}

async function removeCustomModel(id) {
  try {
    await api('/api/models/custom?id=' + encodeURIComponent(id), { method: 'DELETE' });
    await refreshModels();
    toast('Removed.');
    render();
  } catch (e) { toast(e.message, true); }
}

async function doScan(full) {
  try {
    const r = await api('/api/setup/scan', { method: 'POST', body: { full: !!full } });
    toast(r.added ? `Found ${r.added} new turn${r.added === 1 ? '' : 's'}.`
                  : `Up to date — ${num(r.turns)} turns recorded.`);
    S.status = null;
    render();
  } catch (e) { toast(e.message, true); }
}

function toggleManual() {
  const el = document.getElementById('manual');
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function copyText(id, msg) {
  const el = document.getElementById(id);
  if (!el) return;
  try { await navigator.clipboard.writeText(el.textContent); toast(msg || 'Copied.'); }
  catch { toast('Could not copy — select the text and copy it manually.', true); }
}

async function doInstall(force) {
  try {
    const r = await api('/api/setup/install', { method: 'POST', body: { force: !!force } });
    if (!r.ok) { toast(r.error || 'Could not write the settings file.', true); render(); return; }
    toast('Written. Now press “Test it”, then restart Claude Code.');
    render();
  } catch (e) { toast(e.message, true); }
}

async function doUninstall() {
  try {
    const r = await api('/api/setup/uninstall', { method: 'POST' });
    toast(r.ok ? (r.message || 'Removed.') : (r.error || 'Could not remove it.'), !r.ok);
    render();
  } catch (e) { toast(e.message, true); }
}

async function doTest() {
  const out = document.getElementById('test-out');
  out.innerHTML = `<div class="small muted">Running…</div>`;
  try {
    const r = await api('/api/setup/test', { method: 'POST' });
    out.innerHTML = r.ok
      ? `<div class="banner good"><strong>It works.</strong> ${esc(r.message)}
           <div class="prompt-box mt8">${esc(r.output)}</div></div>`
      : `<div class="banner critical"><strong>It did not run.</strong>
           <div class="small mt8">${esc(r.error)}</div>
           <div class="small mt8">The usual cause is Python not being on your PATH. Reinstall Python
             from python.org and tick <strong>“Add python.exe to PATH”</strong> on the first screen,
             then restart this app and try again.</div></div>`;
  } catch (e) {
    out.innerHTML = `<div class="banner critical">${esc(e.message)}</div>`;
  }
}

async function resetAll() {
  modal(`<h2>Erase everything?</h2>
    <p class="small muted">Deletes all projects, steps, iterations and meter history from the local
      database. This cannot be undone.</p>
    <div class="row" style="justify-content:flex-end">
      <button onclick="closeModal()">Cancel</button>
      <button class="btn-danger" onclick="doReset()">Erase</button></div>`);
}

async function doReset() {
  try {
    await api('/api/reset', { method: 'POST', body: { confirm: 'RESET_ALL_DATA' } });
    closeModal(); toast('Erased.'); go('plan');
  } catch (e) { toast(e.message, true); }
}

async function clearDemoData() {
  try {
    const r = await api('/api/clear-demo', { method: 'POST' });
    toast(r.cleared_projects ? `Removed ${r.cleared_projects} sample project${r.cleared_projects === 1 ? '' : 's'}.` : 'No sample data to clear.');
    S.status = null;
    render();
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------- modal */

function modal(html) {
  closeModal();
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.onclick = e => { if (e.target === bg) closeModal(); };
  bg.innerHTML = `<div class="modal">${html}</div>`;
  document.body.appendChild(bg);
}
function closeModal() { document.querySelectorAll('.modal-bg').forEach(m => m.remove()); }

/* ------------------------------------------------------------ router */

function go(route) { S.route = route; S.id = null; location.hash = route; render(); }
function goProject(id) { S.route = 'project'; S.id = id; S.cache.openStep = null; location.hash = 'project/' + id; render(); }

const VIEWS = {
  dashboard: viewDashboard, plan: viewPlan, projects: viewProjects,
  project: viewProject, history: viewHistory, setup: viewSetup,
};

let renderSeq = 0;
let lastViewKey = null;     // route+id of the previous render, to tell navigation from a refresh

async function render() {
  const host = document.getElementById('view');
  document.querySelectorAll('.nav-item[data-route]').forEach(b =>
    b.classList.toggle('active', b.dataset.route === S.route ||
      (S.route === 'project' && b.dataset.route === 'projects')));

  // A fast second click before the first navigation's fetch(es) resolve must
  // not let the stale response overwrite the newer one — a monotonic counter
  // discards anything that isn't still the most recent request in flight.
  const seq = ++renderSeq;
  host.classList.add('loading');
  let html, failed = null;
  try {
    html = await (VIEWS[S.route] || viewPlan)();
  } catch (e) {
    failed = e;
  }
  if (seq !== renderSeq) return;            // superseded by a newer render()
  host.classList.remove('loading');
  if (failed) {
    host.innerHTML = `<div class="card"><div class="banner critical">
      <strong>Something went wrong.</strong><div class="small mt8">${esc(failed.message)}</div></div></div>`;
    return;
  }
  host.innerHTML = html;
  // A new screen starts at the top. Without this, "Create project" from the
  // bottom of the Plan form dropped you onto the project page still scrolled
  // past its own header. Same-view refreshes (ticking a step, a toast-driven
  // re-render) keep your place because the key is unchanged.
  const viewKey = S.route + '/' + (S.id || '');
  if (viewKey !== lastViewKey) { window.scrollTo(0, 0); lastViewKey = viewKey; }
  if (S.route === 'project' && currentProjTab() === 'graph') drawGraph();
  if (S.route === 'dashboard') armGaugeSweep();
}

function parseHash() {
  // Default route is the prompt composer, not the usage-limits screen — the
  // composer is what the app opens to (September 2026, IA pass 2). Usage
  // percentages live only on the Windows screen now: no sidebar mini-meter,
  // no persistent top bar keeping them in view on every other screen. See
  // ARCHITECTURE.md §6.
  const h = (location.hash || '#plan').slice(1);
  const [r, id] = h.split('/');
  S.route = VIEWS[r] ? r : 'plan';
  S.id = id ? Number(id) : null;
}

document.querySelectorAll('.nav-item[data-route]').forEach(b =>
  b.onclick = () => go(b.dataset.route));

document.getElementById('theme-toggle').onclick = () => {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('pm-theme', next);
};

window.addEventListener('hashchange', () => { parseHash(); render(); });

// Cursor-follow glow (September 2026, part 3 — "mouse designs"). One
// delegated listener, set once, rather than wiring a per-element handler on
// every render() — .card/.proj are rebuilt from scratch on every screen
// change, so anything attached directly to them would need re-attaching
// constantly. --mx/--my are read by the ::after glow in styles.css; setting
// them on the element itself is cheap enough to do on every pointermove.
document.addEventListener('pointermove', e => {
  const el = e.target.closest('.card, .proj');
  if (!el) return;
  const r = el.getBoundingClientRect();
  el.style.setProperty('--mx', (e.clientX - r.left) + 'px');
  el.style.setProperty('--my', (e.clientY - r.top) + 'px');
});

(async function boot() {
  const t = localStorage.getItem('pm-theme');
  if (t) document.documentElement.setAttribute('data-theme', t);
  // Optimistic, per-browser fallback shown the instant the models list below
  // resolves — overwritten by the server's own settings a moment later, once
  // /api/status returns, so the same choice follows you to another browser
  // pointed at the same PromptMeter install.
  S.model = localStorage.getItem('pm-model') || S.model;
  S.effort = localStorage.getItem('pm-effort') || S.effort;

  try {
    const [m, s, pv] = await Promise.all([
      api('/api/models'),
      api('/api/status').catch(() => null),   // no token yet on first-ever run; harmless
      api('/api/providers').catch(() => null),
    ]);
    updateSidebarTagline(pv);
    S.models = m.models; S.vendors = m.vendors || []; S.oracles = m.oracles;
    S.efforts = m.efforts || S.efforts;

    if (s) {
      S.status = s;
      S.csrfToken = s.csrf_token;
      S.plans = s.plans || [];
      if (s.settings) {
        S.model = s.settings.model || S.model;
        S.effort = s.settings.effort || S.effort;
        S.surface = s.settings.surface || S.surface;
      }
    }
    if (!m.models.some(x => x.id === S.model)) S.model = m.default_model || m.models[0]?.id || S.model;
    localStorage.setItem('pm-model', S.model);
    localStorage.setItem('pm-effort', S.effort);
  } catch {}

  parseHash();
  render();
})();
