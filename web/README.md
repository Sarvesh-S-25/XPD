# `web/` — the interface

Three files, no build step, no framework, no `node_modules`. The Python server
serves this folder directly; editing a file and refreshing the browser is the
whole development loop.

| File | Lines | What it is |
|---|---|---|
| `index.html` | 35 | The shell. Sidebar, an empty `<header id="selection-bar">` + `<main>` pair, two `<script>`/`<link>` tags. Everything else is rendered by JavaScript. |
| `styles.css` | ~400 | Design tokens and components. No utility framework. |
| `app.js` | ~1,800 | The entire application: routing, data fetching, and every screen. |

---

## How `app.js` is organised

It reads top to bottom in the order things happen.

```
1. state + api()      one fetch wrapper, one global S object
2. helpers            esc(), usd(), tokens(), dur(), when(), ago()
3. components         ring(), meter(), progress(), spark(), riskChip()
4. views              one async function per screen, returns an HTML string
5. actions            everything the buttons call
6. router             hash-based, no library
```

**Views are async functions that return HTML strings.** `render()` awaits the
current view and assigns the result to `innerHTML`. No virtual DOM, no
reconciliation — the whole screen is rebuilt on every change. At this scale that
is simpler and fast enough.

**The five screens**

| Route | Function | What it answers |
|---|---|---|
| `#dashboard` | `viewDashboard` | How much of my limit is left, and when do I run out? |
| `#plan` | `viewPlan` | What will this prompt cost, and should I split it? |
| `#projects` | `viewProjects` | What am I working on and how far along is it? |
| `#project/:id` | `viewProject` | Steps, iterations, the step graph. |
| `#history` | `viewHistory` | What has it learned, and were its estimates right? |
| `#setup` | `viewSetup` | Tracking status, calibration, providers, the status line. |

---

## Rules the code follows

**Every interpolation goes through `esc()`.** Views build HTML by string
concatenation, so this is the only thing standing between a prompt containing
`<script>` and an XSS bug. There are no exceptions to this rule.

**Colours come from CSS custom properties, never literals.** `var(--series-1)`,
not `#2a78d6`. That is what makes the dark theme a single block of overrides
rather than a rewrite.

**Charts are hand-written SVG.** The rings, meters, sparkline and step graph are
all built by generating SVG strings. No charting library, so no dependency and
no bundle.

**Plain language leads, numbers follow.** The Plan screen opens with a sentence
("Do not send this as one prompt") and hides percentages and dollars behind
*"The exact numbers"*. The server computes both; the UI chooses the order.

---

## The selection bar

`renderSelectionBar()` fills the `<header id="selection-bar">` above `<main>`,
outside the router — it survives every route change and is redrawn once per
`render()` call, after `sideMeter()` so it always has a fresh `S.status`.
Surface, plan, model and effort are set once here instead of per prompt.
Model and effort persist server-side via `PATCH /api/settings` (`localStorage`
is only the instant-paint fallback before that first round-trip resolves);
plan already persisted server-side before this existed. Surface is
informational only — it never adjusts any cost math, because nothing in this
app currently measures a real difference between terminal and desktop
overhead (see `ARCHITECTURE.md` §8, "honest failure over silent guessing").

---

## The design system in `styles.css`

**The identity (September 2026 redesign): an instrument, not a SaaS
dashboard.** PromptMeter's actual subject is a meter — a precise readout of
what work costs — so the visual language borrows from real instrumentation
rather than generic app chrome: monospaced, tabular numerals standing in for
a digital readout wherever a number is the point of the screen (`.hero`,
`.stat-value`, `.ring-meta .n`, table `.num` cells, `.kv dd`), flat panels
with no drop shadows (a gauge is flat-mounted, not floating), a radius scale
with real hierarchy (`--r` 12px for panels, `--r-sm` 6px for controls —
never one border-radius applied to everything), and — used exactly once,
deliberately, on the two numbers the whole product exists to show — twelve
gauge tick marks around the Dashboard's rings (`ring()` in `app.js`).

Tokens are declared once on `:root` and overridden in two dark-mode blocks — one
for the OS setting, one for the in-app toggle.

| Group | Tokens |
|---|---|
| Surfaces | `--surface-1`, `--page`, `--raise` |
| Text | `--text-primary`, `--text-secondary`, `--text-muted` |
| Lines | `--grid`, `--baseline`, `--border` |
| Brand / interactive | `--accent`, `--accent-ink` — buttons, links, focus rings, the active nav item and tab |
| Series | `--series-1..4` — categorical, assigned to model tiers in fixed order |
| Status | `--good`, `--warning`, `--serious`, `--critical` |
| Meters | `--track`, `--track-warm` |
| Radius | `--r` (panels), `--r-sm` (controls) |

**`--accent` is deliberately not `--series-1`.** Before this redesign, the
app's primary/brand colour and `--series-1` (Opus's tier colour on a model
chip) were the same blue — one hue silently meaning two different things.
`--accent` is a separate signal-teal reserved for UI chrome; `--series-1..4`
and the status colours stay exactly the colour-blind-validated reference
values they always were and are never repurposed as decoration.

The palette is colour-blind-validated. Two details that were bugs before they
were rules: the dark-mode meter track must be the *darkest* step of the blue
ramp, or an empty meter reads as a full one; and a meter with no reading renders
as a hatched `.unknown` track, never as an empty bar, because empty and unknown
must not look alike.

Components: `.card`, `.banner`, `.chip`, `.meter`, `.progress-track`, `.step`,
`.stepn` (numbered setup steps), `.selbar` (the selection bar), `.modal-bg`,
`.toast`.

`.chip.warning`/`.chip.serious` carry their own background wash
(`--warning-bg`/`--serious-bg`), not just a swatch-dot colour — the two used
to be visually near-identical text-on-`--page`, distinguished only by an
8px dot, easy to miss at normal reading distance.

---

## Adding a screen

1. Write `async function viewThing() { return \`<html>\` }`
2. Add it to the `VIEWS` map
3. Add a `<button class="nav-item" data-route="thing">` to `index.html`

That is the whole procedure. There is no build, no registration, no config.
