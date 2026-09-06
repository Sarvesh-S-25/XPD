/* PromptMeter UI — vanilla JS, no build step. */

const S = { route: 'dashboard', id: null, status: null, models: [], vendors: [],
            efforts: ['none','low','medium','high','max'], model: 'claude-sonnet-5',
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
  const r = await fetch(path, {
    method,
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const j = await r.json().catch(() => ({ error: 'Bad response' }));
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

function ring(pctVal, size = 92, label = '') {
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
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img"
      aria-label="${esc(label)} ${pctVal == null ? 'unknown' : v.toFixed(0) + ' percent used'}">
    <circle cx="${mid}" cy="${mid}" r="${r}" fill="none" stroke="var(--track)" stroke-width="9"/>
    <g opacity="0.6">${ticks}</g>
    ${pctVal == null ? '' : `<circle cx="${mid}" cy="${mid}" r="${r}" fill="none" stroke="${color}"
      stroke-width="9" stroke-linecap="round" stroke-dasharray="${c}"
      stroke-dashoffset="${c * (1 - v / 100)}" transform="rotate(-90 ${mid} ${mid})"/>`}
    <text x="${mid}" y="${mid + 1}" text-anchor="middle" dominant-baseline="middle"
      font-size="${size * 0.23}" font-weight="600" fill="var(--text-primary)"
      font-family="var(--mono)">${pctVal == null ? '–' : Math.round(v)}</text>
    <text x="${mid}" y="${mid + size * 0.19}" text-anchor="middle"
      font-size="${size * 0.11}" fill="var(--text-muted)" font-family="var(--font)">used %</text>
  </svg>`;
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

function spark(points, w = 260, h = 44, key = 'pct7') {
  if (!points || points.length < 2) return `<div class="small muted">Not enough history yet.</div>`;
  const xs = points.map(p => p.ts), ys = points.map(p => p[key] || 0);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y1 = Math.max(100, ...ys);
  const X = t => 2 + (t - x0) / Math.max(1, x1 - x0) * (w - 4);
  const Y = v => h - 2 - (v / y1) * (h - 6);
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${X(p.ts).toFixed(1)},${Y(p[key] || 0).toFixed(1)}`).join('');
  const last = points[points.length - 1];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-label="Weekly window used over time">
    <line x1="0" y1="${h - 2}" x2="${w}" y2="${h - 2}" stroke="var(--grid)" stroke-width="1"/>
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${X(last.ts).toFixed(1)}" cy="${Y(last[key] || 0).toFixed(1)}" r="4"
      fill="var(--accent)" stroke="var(--surface-1)" stroke-width="2"/>
  </svg>`;
}

/* ---------------------------------------------------------- dashboard */

async function viewDashboard() {
  const s = await api('/api/status');
  S.status = s;
  const hist = await api('/api/meter/history?hours=30').catch(() => ({ points: [] }));
  const f = s.five_hour, w = s.seven_day;
  const unmetered = f.used == null && w.used == null;

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
    <div>
      <h1>Windows</h1>
      <div class="sub">${
        s.source === 'status line' ? 'Live from the Claude Code status line · ' + ago(s.observed_at)
        : s.source === 'your reading' ? 'Synced from Claude ' + ago(s.observed_at) + ' · counting down on its own'
        : s.source === 'transcripts' ? `From ${s.derived.turns} local session turns · last activity ${ago(s.observed_at)}`
        : 'No numbers yet — press “Sync from Claude”'}</div>
    </div>
    <div class="row">
      <button class="btn-sm btn-primary" onclick="manualEntry()">Sync from Claude</button>
      <button class="btn-sm" onclick="go('plan')">Plan a prompt</button>
    </div>
  </div>

  <div class="card">
    <div class="spread" style="align-items:flex-start">
      <div style="min-width:0">
        <div class="label">Weekly window</div>
        <div class="hero">${heroLine}</div>
        <div class="small muted mt8" style="max-width:52ch">${heroSub}</div>
      </div>
      <div style="text-align:right">
        ${spark(hist.points, 280, 56, 'pct7')}
        <div class="small muted" style="margin-top:2px">weekly window used, last 30 hours</div>
      </div>
    </div>
    <div class="small muted mt12">${regimeText}</div>
  </div>

  <div class="grid grid-3 mt16">
    <div class="card">
      <div class="ring-card">
        ${ring(f.used, 92, 'current session')}
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

    <div class="card">
      <div class="ring-card">
        ${ring(w.used, 92, 'weekly')}
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

  <div class="card mt16">
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

/* --------------------------------------------------------------- plan */

let planPreview = null;

async function viewPlan() {
  const models = S.models.length ? S.models : (S.models = (await api('/api/models')).models);
  const pv = await api('/api/providers').catch(() => ({ active: 'heuristic', keys: {} }));
  const sel = (S.models || []).find(m => m.id === S.model);
  return `
  <div class="page-head">
    <div><h1>Plan a prompt</h1>
      <div class="sub">See the cost, the risk, and whether it needs splitting — before you send it.</div></div>
  </div>
  <div class="grid grid-2">
    <div class="card">
      <div class="field">
        <label>Your prompt</label>
        <textarea id="p-prompt" style="min-height:210px" placeholder="Paste the prompt you were about to send to Claude…"></textarea>
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
          <select id="p-effort" ${sel && !sel.thinking ? 'disabled' : ''}>${(S.efforts || []).map(x =>
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
        <button class="btn-primary" onclick="doPreview()">Estimate</button>
        <button onclick="commitPlan()" id="p-create" disabled>Create project</button>
      </div>
    </div>
    <div id="p-out">
      <div class="card"><div class="empty">
        <div class="big">Nothing estimated yet</div>
        <div class="small">Paste a prompt and press Estimate. Nothing is sent anywhere —
          the estimate is computed on this machine.</div>
      </div></div>
    </div>
  </div>`;
}

function onModelChange(id) {
  S.model = id;
  localStorage.setItem('pm-model', id);
  const eff = document.getElementById('p-effort');
  if (eff) { S.effort = eff.value; localStorage.setItem('pm-effort', eff.value); }
  const prompt = document.getElementById('p-prompt').value;
  render().then(() => {
    const box = document.getElementById('p-prompt');
    if (box) box.value = prompt;              // keep what was typed
  });
}

async function doPreview() {
  const prompt = document.getElementById('p-prompt').value.trim();
  if (!prompt) return toast('Paste a prompt first.', true);
  const out = document.getElementById('p-out');
  out.innerHTML = `<div class="card"><div class="empty">Estimating…</div></div>`;
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
        use_planner: (document.getElementById('p-planner') || {}).checked || false,
      },
    });
    out.innerHTML = renderPreview(planPreview);
    document.getElementById('p-create').disabled = false;
  } catch (e) {
    out.innerHTML = `<div class="card"><div class="banner critical">${esc(e.message)}</div></div>`;
  }
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
      : /too big|needs splitting|unavailable/i.test(p.split_reason) ? 'critical' : 'good'}">${esc(p.split_reason)}</div>
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
             Do it anyway only for the reasons that are not about price: it fits inside your window,
             and a step that goes wrong costs ${usd(sv.blast_radius_after)} instead of
             ${usd(sv.blast_radius_before)}. If neither matters, set Splitting to “Never split”.`}
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

function modelLabel(id) {
  const m = S.models.find(x => x.id === id);
  return m ? m.label : id;
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

  <div class="grid grid-3">
    <div class="card"><div class="label">Projects</div>
      <div class="stat-value tnum">${totals.projects ?? '—'}</div>
      <div class="small muted">across every status</div></div>
    <div class="card"><div class="label">Steps</div>
      <div class="stat-value tnum">${totals.steps ?? '—'}</div>
      <div class="small muted">${num(totals.iterations)} iterations logged</div></div>
    <div class="card"><div class="label">Spent</div>
      <div class="stat-value tnum">${usd(totals.spent)}</div>
      <div class="small muted">list-price equivalent · sample data excluded</div></div>
  </div>

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
        <dt>Window used</dt><dd>${p.pp5_spent.toFixed(1)}% of a 5-hour window</dd>
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
                · ${it.pp5_delta.toFixed(1)}% of a 5h window · ${ago(it.created_at)}</div>
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
  try { await navigator.clipboard.writeText(s.next_prompt); toast('Prompt copied — paste it into Claude.'); }
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
        <td class="num">${m.pp5.toFixed(1)}%</td></tr>`).join('')}</tbody></table>`
      : `<div class="empty">
          <div class="big">Nothing logged yet</div>
          <div class="small">Run a project and log an iteration, or load sample data to see the shape of this table.</div>
          <div class="mt12"><button class="btn-primary" onclick="go('plan')">Plan a prompt</button>
            <button style="margin-left:8px" onclick="seedDemo()">Load sample data</button></div>
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
    ${acc.length ? `<table>
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
    ? ['warning', `<strong>Nothing recorded yet.</strong> Send one message in Claude Code — terminal or desktop app — then press “Check now” below.`]
    : c.capacity_basis === 'measured'
      ? ['good', `<strong>All set.</strong> ${num(w.turns)} turns tracked automatically, and your window size is measured. Nothing left to do.`]
      : ['good', `<strong>Tracking automatically.</strong> ${num(w.turns)} turns recorded with no setup. Percentages are based on a plan estimate — one reading makes them exact.`];

  return `
  <div class="page-head">
    <div><h1>Setup</h1>
      <div class="sub">Usage tracks itself. This page shows what it can see and how to make it exact.</div></div>
  </div>

  <div class="banner ${banner[0]}">${banner[1]}</div>

  <div class="card mt16">
    <div class="card-head"><h2>Automatic tracking</h2>
      <span class="hint">works for the terminal and the desktop app · nothing to configure</span></div>

    <p class="small">Claude Code writes every turn it runs to a session file on this machine, with the
      exact token counts. PromptMeter reads those files. That covers <strong>both</strong> the terminal
      and the desktop app, needs no settings change, and updates by itself every 20 seconds.</p>

    <div class="grid grid-3 mt16" style="gap:12px">
      <div><div class="label">Session files found</div>
        <div class="stat-value tnum">${w.files}</div>
        <div class="small muted">${w.root_exists ? 'in ' + esc(w.root) : 'folder not found yet'}</div></div>
      <div><div class="label">Turns recorded</div>
        <div class="stat-value tnum">${num(w.turns)}</div>
        <div class="small muted">${w.sessions} session${w.sessions === 1 ? '' : 's'}</div></div>
      <div><div class="label">Last checked</div>
        <div class="stat-value">${w.last_scan ? ago(w.last_scan) : '—'}</div>
        <div class="small muted">${w.running ? 'watching continuously' : 'watcher stopped'}</div></div>
    </div>

    <div class="row mt16">
      <button onclick="doScan(false)">Check now</button>
      <button onclick="doScan(true)">Re-read everything</button>
    </div>

    ${w.turns === 0 ? `<div class="banner warning mt16">
      <strong>No turns found yet.</strong> ${w.root_exists
        ? 'The folder exists but has no session files with usage in them. Send one message in Claude Code — terminal or desktop app — then press “Check now”.'
        : `Nothing at <span class="mono">${esc(w.root)}</span>. That folder appears the first time you use Claude Code on this machine.`}
    </div>` : `<div class="banner good mt16">
      <strong>Tracking is on.</strong> ${num(w.turns)} turns across ${w.sessions} session${w.sessions === 1 ? '' : 's'}.
      Nothing else is needed — the windows on the Windows page update on their own.
    </div>`}

    ${w.projects && w.projects.length ? `<details class="mt12">
      <summary>Which folders it is seeing</summary>
      <table><thead><tr><th>Project folder</th><th class="num">Turns</th>
        <th class="num">Cost</th><th class="num">Last activity</th></tr></thead>
      <tbody>${w.projects.map(pr => `<tr><td class="mono">${esc(pr.project)}</td>
        <td class="num">${num(pr.turns)}</td><td class="num">${usd(pr.cost)}</td>
        <td class="num">${ago(pr.last)}</td></tr>`).join('')}</tbody></table>
    </details>` : ''}
  </div>

  <div class="card">
    <div class="card-head"><h2>How big is your window?</h2>
      <span class="hint">${c.capacity_basis === 'measured' ? 'measured — nothing to do' : 'one reading pins this exactly'}</span></div>

    <p class="small">Tracking gives exact dollars of work. Turning that into “percent of your window”
      needs to know how big your window is, and Anthropic doesn’t publish that number. So pick your
      plan for a starting estimate, then let one real reading replace it.</p>

    <div class="field" style="max-width:320px">
      <label>Your plan</label>
      <select onchange="setPlan(this.value)">
        ${(c.plans || []).map(pl => `<option value="${pl.id}" ${pl.id === c.plan ? 'selected' : ''}>${esc(pl.label)}</option>`).join('')}
      </select>
    </div>

    <div class="banner ${c.capacity_basis === 'measured' ? 'good' : ''}">
      ${c.capacity_basis === 'measured'
        ? `<strong>Already measured.</strong> Your window works out to about
           ${usd(c.capacity_usd5)} of list-price work per 5 hours. This came from a real reading, so
           the percentages are yours, not a guess.`
        : `<strong>Pin it exactly, once.</strong> Open your usage view — the ring next to the model
           picker in the desktop app, or <span class="mono">/usage</span> in the terminal — and type
           the two percentages in. PromptMeter divides them into the spend it has already recorded
           and solves for your real window size. You never have to do it again.`}
    </div>
    <div class="row mt12"><button class="${c.capacity_basis === 'measured' ? '' : 'btn-primary'}"
      onclick="manualEntry()">${c.capacity_basis === 'measured' ? 'Re-calibrate' : 'Calibrate now'}</button></div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Live status line</h2>
      <span class="hint">terminal only · optional extra precision</span></div>

    <p class="small">On top of the automatic tracking, the terminal version of Claude Code can hand
      PromptMeter the exact percentages Anthropic enforces on, second by second. This is optional —
      tracking already works without it — and it does nothing in the desktop app, which has no status
      line to attach to.</p>

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

async function doScan(full) {
  try {
    const r = await api('/api/setup/scan', { method: 'POST', body: { full: !!full } });
    toast(r.added ? `Found ${r.added} new turn${r.added === 1 ? '' : 's'}.`
                  : `Up to date — ${num(r.turns)} turns recorded.`);
    S.status = null;
    render();
  } catch (e) { toast(e.message, true); }
}

async function setPlan(plan) {
  try {
    await api('/api/setup/plan', { method: 'POST', body: { plan } });
    toast('Plan set. Percentages recalculated.');
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
    closeModal(); toast('Erased.'); go('dashboard');
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

/* ---------------------------------------------------- selection bar */
//
// Set once, applies everywhere a model/effort/plan gets used, instead of
// re-picking it on every prompt. Model and effort persist server-side (see
// PATCH /api/settings) the same way `plan` already did, so the choice
// follows you to another browser pointed at this same install — localStorage
// is only the instant-paint fallback before that first server round-trip.
// Surface is informational only: it does not change any cost math, since
// nothing in this app currently measures a real difference between terminal
// and desktop overhead (see ARCHITECTURE.md §8, "honest failure over silent
// guessing") — Cowork/browser usage has no option here at all, because
// nothing here can see it either way.

function renderSelectionBar() {
  const host = document.getElementById('selection-bar');
  if (!host) return;
  const cap = (S.status || {}).capacity || {};
  const measured = cap.confidence === 'measured';

  host.innerHTML = `
  <div class="selbar">
    <div class="selbar-group">
      <label>Surface</label>
      <select onchange="setSurface(this.value)">
        <option value="terminal" ${S.surface === 'terminal' ? 'selected' : ''}>Claude Code — terminal</option>
        <option value="desktop" ${S.surface === 'desktop' ? 'selected' : ''}>Claude Code — desktop app</option>
      </select>
    </div>
    <div class="selbar-group">
      <label>Plan</label>
      <select onchange="setPlan(this.value)">
        ${(S.plans || []).map(p => `<option value="${p.id}" ${p.id === cap.plan ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}
      </select>
      <span class="chip small ${measured ? 'good' : ''}" title="${measured
        ? 'Solved from a real usage reading you synced.'
        : 'A starting estimate — sync a reading on Setup to measure it exactly.'}">${measured ? 'measured' : 'estimate'}</span>
    </div>
    <div class="selbar-group">
      <label>Model</label>
      <select onchange="setDefaultModel(this.value)">
        ${(S.vendors || []).map(v => `<optgroup label="${esc(v.label)}">${v.models.map(m =>
          `<option value="${m.id}" ${m.id === S.model ? 'selected' : ''}>${esc(m.label)}</option>`).join('')}</optgroup>`).join('')}
      </select>
    </div>
    <div class="selbar-group">
      <label>Effort</label>
      <select onchange="setDefaultEffort(this.value)">
        ${(S.efforts || []).map(x => `<option value="${x}" ${x === S.effort ? 'selected' : ''}>${x}</option>`).join('')}
      </select>
    </div>
    <div class="selbar-hint small muted">Applies to every new plan — set once, not per prompt.</div>
  </div>`;
}

async function setSurface(v) {
  S.surface = v;
  try { await api('/api/settings', { method: 'PATCH', body: { surface: v } }); }
  catch (e) { toast(e.message, true); }
}

async function setDefaultModel(v) {
  S.model = v;
  localStorage.setItem('pm-model', v);
  try { await api('/api/settings', { method: 'PATCH', body: { model: v } }); }
  catch (e) { toast(e.message, true); }
}

async function setDefaultEffort(v) {
  S.effort = v;
  localStorage.setItem('pm-effort', v);
  try { await api('/api/settings', { method: 'PATCH', body: { effort: v } }); }
  catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ router */

function go(route) { S.route = route; S.id = null; location.hash = route; render(); }
function goProject(id) { S.route = 'project'; S.id = id; S.cache.openStep = null; location.hash = 'project/' + id; render(); }

const VIEWS = {
  dashboard: viewDashboard, plan: viewPlan, projects: viewProjects,
  project: viewProject, history: viewHistory, setup: viewSetup,
};

let renderSeq = 0;

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
    html = await (VIEWS[S.route] || viewDashboard)();
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
  if (S.route === 'project' && currentProjTab() === 'graph') drawGraph();
  await sideMeter();
  renderSelectionBar();
}

async function sideMeter() {
  try {
    const s = S.status || await api('/api/status');
    S.status = s;
    const f = s.five_hour, w = s.seven_day;
    document.getElementById('side-meter').innerHTML = `
      <div style="margin-bottom:8px">
        <div class="spread small"><span>5-hour</span><span class="tnum">${pct(f.used)}</span></div>
        ${meter(f.used, dur(f.seconds_to_reset) + ' to reset', '')}
      </div>
      <div>
        <div class="spread small"><span>Weekly</span><span class="tnum">${pct(w.used)}</span></div>
        ${meter(w.used, dur(w.seconds_to_reset) + ' to reset', '')}
      </div>`;
  } catch { /* daemon busy */ }
}

function parseHash() {
  const h = (location.hash || '#dashboard').slice(1);
  const [r, id] = h.split('/');
  S.route = VIEWS[r] ? r : 'dashboard';
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
    if (!m.models.some(x => x.id === S.model)) S.model = m.models[0]?.id || S.model;
    localStorage.setItem('pm-model', S.model);
    localStorage.setItem('pm-effort', S.effort);
  } catch {}

  parseHash();
  render();
  setInterval(async () => { try { S.status = await api('/api/status'); sideMeter(); } catch {} }, 30000);
})();
