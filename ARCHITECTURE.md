# PromptMeter — how it works, component by component

Reference document for the whole system: what each module does, the algorithm it
uses, the formulas, and where each one is weak.

**Scale:** ~3,500 lines of Python across 16 modules, ~2,000 lines of frontend.
Python standard library only — no pip install, no Node, no build step.

**One rule runs through the whole design:** *the deterministic engine owns every
number; a model may only supply structure.* Everything that produces a cost, a
probability, or a schedule is plain code you can read and check against history.
Models are optional and are never asked what something will cost.

---

## 1. System shape

```
   INPUTS                    ENGINE                        OUTPUTS
 ┌──────────────┐    ┌─────────────────────────┐    ┌──────────────────┐
 │ status line  │──▶ │ meter    window ledgers │──▶ │ Windows page     │
 │ transcripts  │──▶ │ watcher  transcript tail│    │ rings, pace      │
 │ your reading │──▶ │ learning history → priors│   │                  │
 └──────────────┘    ├─────────────────────────┤    ├──────────────────┤
 ┌──────────────┐    │ estimator tokens, class │    │ Plan page        │
 │ your prompt  │──▶ │ scope     how big       │──▶ │ cost band, risk  │
 │ (+ planner)  │    │ pricing   loop cost     │    │ split, schedule  │
 └──────────────┘    │ segmenter split → DAG   │    │                  │
                     │ planner   orchestration │    ├──────────────────┤
 ┌──────────────┐    ├─────────────────────────┤    │ Projects         │
 │ your files   │──▶ │ oracles   done-ness     │──▶ │ step graph       │
 └──────────────┘    └─────────────────────────┘    └──────────────────┘
                                 │
                         SQLite (7 tables)
```

**Two independent loops.** The *measurement* loop (watcher → meter) records what
actually happened. The *estimation* loop (estimator → planner) predicts what will
happen. `learning` is the bridge: measurements become the priors for future
predictions. They can run without each other — estimation works on day one with
no history, and measurement works whether or not you ever plan a prompt.

---

## 2. Storage — `db.py` (205 lines)

SQLite, one file at `~/.promptmeter/promptmeter.db`, WAL journal mode.

**Thread-local connections.** The HTTP server is threaded and SQLite connection
objects are not thread-safe. Each thread lazily creates and caches its own
connection in a `threading.local()`. WAL lets readers and the writer proceed
concurrently, which matters because the watcher thread writes every 20 seconds
while the UI reads.

**Schema-on-open, plus a small migration runner.** `executescript` with
`CREATE TABLE IF NOT EXISTS` runs on every start — adding a table is still
free. A column added to an *existing* table goes through `MIGRATIONS`, a
numbered list of functions run once (tracked in `schema_version`), each
idempotent via `PRAGMA table_info` checks. As of September 2026, migration 1
adds `workspace_id` (default `'local'`) to `projects`/`meter` — deliberately
narrow multi-seat readiness, nothing reads it yet — migration 2 adds `approved`
to `steps` (the shell-oracle confirmation gate), and migration 3 adds
`is_demo` to `projects`/`steps`/`iterations`/`meter` (so seeded sample data
never pollutes real totals).

**Seven tables, three with an added column since the schema was first written**

| Table | Holds | Written by |
|---|---|---|
| `projects` | one planned prompt, its estimate and risk at creation, plus `workspace_id`/`is_demo` | planner |
| `steps` | segments of a project: prompt, model, budget, oracle, progress, plus `approved`/`is_demo` | planner |
| `iterations` | one pass at a step: summary, tokens, cost, verdict, plus `is_demo` | you, via the UI |
| `turns` | every assistant turn Claude Code wrote to disk | watcher |
| `meter` | percentage readings from the status line or from you, plus `workspace_id`/`is_demo` | meter |
| `scanned` | byte offset reached in each transcript file | watcher |
| `settings` | JSON key-value: calibration, plan, provider config (keys, DPAPI-wrapped on Windows), user's default model/effort/surface | everything |
| `schema_version` | which numbered migrations have run | db.migrate() |

`settings` is a JSON blob store rather than columns, so new configuration never
needs a schema change — the migration runner exists for the tables above it,
which do have real columns.

---

## 3. Measurement

### 3.1 `watcher.py` (259 lines) — tailing Claude Code's transcripts

Claude Code appends one JSON object per line to
`~/.claude/projects/<project>/<session>.jsonl`. Assistant lines carry an exact
`usage` block. **Both the terminal and the desktop Code tab write here**, which
is why this needs no configuration.

**Algorithm — incremental tail with offset memory.**

```
every 20s, for each *.jsonl under ~/.claude/projects:
    size = stat(file)
    offset = scanned[file].offset          (0 if unseen)
    if size < scanned[file].size: offset = 0        # truncated/rotated → reread
    if size == scanned[file].size and offset >= size: skip   # nothing new
    seek(offset); parse each new line; INSERT OR IGNORE
    scanned[file] = (tell(), size, now)
```

Cost is O(new bytes), not O(file). A 15 MB transcript rescans in milliseconds
once seen. `INSERT OR IGNORE` on the message `uuid` primary key makes the whole
operation idempotent — a full re-read inserts nothing new.

**Per-turn cost.** The `usage` block distinguishes four token kinds, priced
differently:

```
cost = ( input_tokens              × p_in
       + cache_read_tokens         × p_in × 0.1
       + cache_write_1h_tokens     × p_in × 2.0
       + cache_write_5m_tokens     × p_in × 1.25 ) / 1e6
     +   output_tokens             × p_out / 1e6
```

Cache creation is split into `ephemeral_1h_input_tokens` and
`ephemeral_5m_input_tokens`; when only the total is present we assume 1-hour,
which is the subscription default. **Thinking tokens are already inside
`output_tokens`** (`thinking_tokens` is a nested breakdown, not an addition), so
reasoning cost is captured without a separate term.

**Blind spot, by construction:** Cowork sessions run on Anthropic's servers and
claude.ai runs in a browser. Neither writes here, so neither is visible.

**Learning `thinking_share` from real transcripts (September 2026).** A turn
that actually reasoned carries `content` blocks alongside `usage`
(`{"type": "thinking", "thinking": "..."}` next to `{"type": "text", ...}`).
The watcher reads these too — not for cost, `usage` already covers that — only
to feed `pricing.learn_thinking_share()` the character share of thinking vs.
text length, EWMA-blended per model once 5+ such turns exist. This can't be
learned per effort level: nothing in the transcript records which effort
produced a given turn, so `pricing.py`'s static `_TH`/`_TH_LIGHT` tables stay
in charge of the effort *shape* until enough of your own data exists to
override the whole curve for that model.

### 3.2 `meter.py` (495 lines) — the two window ledgers

The hardest part of the system, because the ground truth is partly unobservable.

**Source precedence:**

1. **Status line** — exact percentages, if a reading is under an hour old.
2. **Anchored reading + local spend** — a past reading, ticked forward.
3. **Local spend only** — transcripts converted with an estimated capacity.

**Algorithm — anchor and tick.** A reading gives a true percentage at a known
instant and true reset times. From there:

```
if now ≥ resets5:                       # window rolled over since the reading
    base5 = 0
    while now ≥ resets5: resets5 += 5h  # advance to the live window
    since  = resets5 − 5h
else:
    base5 = reading.pct5
    since  = reading.timestamp

pct5 = min(100, base5 + 100 × spend_since(since) / capacity5)
```

The bars therefore fall back to zero on their own at a reset and climb again
from local work, without asking you for anything.

**Algorithm — window reconstruction with no reading.** The 5-hour window is
*anchored*, not rolling: it opens on your first turn after the previous one
closed. Replaying turns in order recovers where the current one opened:

```
start = None; spent = 0
for turn in turns(last 7 days, ascending):
    if start is None or turn.ts ≥ start + 5h:   # previous window lapsed
        start, spent = turn.ts, 0
    spent += turn.cost
if now ≥ start + 5h: spent = 0                  # lapsed while idle
```

O(n) in turns, single pass. The weekly window is treated as a trailing 7-day sum.

**Algorithm — capacity solving.** Anthropic publishes no absolute limits, so we
solve for one. Given a true percentage and the dollars the ledger recorded in
that same window:

```
capacity = spend_in_window / (percentage / 100)
```

Smoothed 50/50 against any previous value so a single odd reading can't swing
it. Guarded two ways: percentages under 3% are refused (dividing by a small
number amplifies error), and spend under $0.05 is refused — that means the
usage happened somewhere transcripts can't see, and fitting to it would produce
a fiction. In that case the reading is still stored as an anchor; only the
capacity fit is skipped.

**Algorithm — least-squares α (legacy path).** With a stream of status-line
samples, `α` = percent consumed per list-price dollar is fitted through the
origin: `α = Σ(pct·cost) / Σ(cost²)`. Pairs that straddle a reset — where the
percentage went *down* — are discarded. The direct capacity solve above
supersedes this, but it still refines the estimate when readings stream in.

**Burn rate and exhaustion.** Trailing-window differencing over turns:
`burn = Σcost / hours_spanned`, converted to percent via capacity. Then
`exhausts_at = now + remaining / burn`, and `sustainable = remaining / hours_to_reset`.
Comparing those two is the whole "are you over pace" verdict.

**Regime classification.** Which limit actually binds you:

```
burst       if burn₅ × 5h  ≥ 100%     # you can drain the session window
sustained   if burn₇ × 7d  ≥ 100%     # the weekly window is what stops you
comfortable otherwise
```

This matters because the advice inverts: burst users benefit from deferring,
sustained users don't — they need cheaper models and smaller context.

**Leak detection.** Threshold rules over recent turns, each flagged at ≥10% of
recent cost: cache misses (`cache_write` large, `cache_read` ≈ 0), long context
(`ctx_pct ≥ 70`), Opus-by-default (Opus ≥ 50% of spend), and untracked usage
(metered turns far exceeding logged iterations).

---

## 4. Estimation

### 4.1 `pricing.py` (112 lines) — the cost model

Prices live in editable JSON, not constants, because a stale price table is a
quietly wrong app.

**The core formula — `loop_cost`.** This is the piece most cost calculators get
wrong. An agent re-sends the whole conversation every turn, so:

```
Σ input  = T·B + g·T(T−1)/2          ← quadratic in turn count
Σ output = T·ō
```

`T` turns, `B` base context, `g` growth per turn, `ō` output per turn. With a
cached prefix the first term collapses:

```
base_part = B × 2.0          (one 1-hour cache write)
          + B × (T−1) × 0.1  (reads)
```

The `g·T(T−1)/2` term is the sum of an arithmetic series — turn *k* carries *k·g*
tokens of accumulated conversation. At 40 turns it is **96–99% of the input
bill**, which is the single most important fact the tool exists to surface.

**Multi-vendor catalogue.** Anthropic, OpenAI and Google in one table, keyed on
what differs between them: cache pricing (Anthropic charges to *write*, the
others cache free), context window (200k–1.05M), max output per reply, thinking
share by effort, and Gemini's above-200k long-context rate tier.

**`loop_budget` — full token accounting.** Extends the cost formula with the two
things a window imposes:

```
per_turn_out = min(prior_out × effort_multiplier, max_output)
peak_ctx     = base + growth × (T − 1) + per_turn_out

if peak_ctx > window:                       # the run must compact
    compactions = ceil((peak_ctx − window) / (window × 0.5))
    compact_in  = compactions × window × 0.70
    compact_out = compactions × min(4000, max_output)
```

`effort_multiplier = (1 − share_default) / (1 − share_effort)` — thinking is
produced *on top of* the visible answer, so raising effort raises total output
and therefore cost. Priors were learned at default effort, which anchors it.

### 4.2 `estimator.py` (315 lines)

**Token counting.** No official offline tokenizer exists, so:

```
tokens = (chars / 3.6) × density × correction
density = 1 + min(0.35, symbol_count / chars × 4)      # code tokenises denser
```

`correction` is learned by EWMA (`0.9·old + 0.1·observed`) whenever a real usage
block is available, so the estimate converges on your actual ratio.

**Task classification — weighted keyword scoring.** Seven classes, each with
weighted regex signals. Every class is scored, the highest wins; first-match
ordering would be wrong because prompts contain signals for several classes.
Below a threshold of 1.5 it defaults to `multi_file_feature`.

Then **scope escalation**: a repo-wide noun (`codebase`, `entire project`) plus
open-ended language promotes the class to at least `refactor`, because
"fix everything in the codebase" reads as a single-file edit by verb alone.

**Risk scoring — logistic with a damping term.**

```
scale = clamp(log1p(T₉₅) / log1p(40), 0.12, 1.0)
z = −2.2 + scale × Σ(wᵢ · featureᵢ) + budget_pressure
p = 1 / (1 + e^−z)
```

Count-type features are compressed with `log1p(v)×1.6` so ten files don't score
ten times one file. The **scale** term is the important one: runaway features
(no turn cap, shell access, vague wording) are weighted by how much room the task
has to run away in, so a two-turn question can't score high no matter how
loosely worded.

```
budget_pressure = min(2.2, 1.5 × ln(P95_cost / remaining_budget))   if > 1
```

Capped, because past "definitely doesn't fit" the extra information lives in the
cost band, not the probability — without the cap every large job saturated at 99%.

Bands: green <10%, amber <30%, red <60%, critical ≥60%. Risk is defined as
**P(this run hits a wall)**, which is why the History screen can check it.

**Producing the band.** Evaluate `loop_cost` twice — at `(T₅₀, ō₅₀)` and
`(T₉₅, ō₉₅)`. This is a *bracket*, not a fitted predictive distribution.

### 4.3 `scope.py` (174 lines) — how big is this, really

Without this, `build a todo app` and `build a food delivery app with payments,
tracking, a dashboard and a mobile client` estimate identically — the class
prior dominates and the prompt text contributes almost nothing.

**Counted features:** 14 subsystem patterns (auth, payments, search, realtime,
maps, notifications, admin, chat, media, cart, reviews, scheduling, recommend,
infra), 5 platform patterns, ~20 third-party integrations, ~40 domain entity
nouns, bullet count, and a clone detector (`like zomato`, `clone of airbnb`).

**Scoring:**

```
score = 1.0
      + 0.30 × (subsystems + implied)
      + 0.25 × (platforms − 1)
      + 0.08 × entities
      + 0.12 × min(bullets, 12)
      + 0.15 × integrations

multiplier = clamp((score / reference)^0.75, 0.30, 3.20)
```

Two deliberate choices. **Implied subsystems**: naming a product to clone adds
scope up to 5 subsystems, because "like zomato" carries requirements the prompt
never states. **The 0.75 exponent** compresses extremes — scope evidence is
suggestive, not proof, so a prompt scoring 3× the reference gets ~2.3×.

`reference` is the typical scope the class prior was written for — 2.6 for
`build_app`, 1.1 for `single_file_edit` — so the multiplier is relative to its
own class rather than absolute.

**From a drafted plan** (`from_plan`) the same machinery runs on step count and
average complexity instead of keywords: `score = 1 + 0.30 × n × (avg_complexity / 3)`.

### 4.4 `learning.py` (165 lines) — priors giving way to your data

**Shrinkage blending.** For each (task class, model) pair:

```
w = n / (n + 6)
prediction = prior × (1 − w) + observed × w
```

`k = 6` means two samples sit at 25% weight and fifty at 89%. This is the standard
empirical-Bayes shrinkage estimator, and it's what stops one weird project from
rewriting your priors.

**Quantiles** are computed by linear interpolation on the sorted sample —
adequate for small n and dependency-free. `T₉₅` is floored at `1.15 × T₅₀` and
`ō₉₅` at `1.2 × ō₅₀` so the band can never collapse.

**Growth measurement.** `g` is the median of per-turn deltas in total input
tokens across consecutive iterations of the same step — a directly observed
quantity, not a guess.

**P95-band calibration (September 2026), `calibration_factor()`.** The same
shrinkage weight above, applied to a different pair: the model's own
cost_p95:cost_p50 ratio (`prior_ratio`, from `pricing.loop_budget`) blended
with the empirical 95th-percentile of `actual/predicted_p50` across this task
class's own completed steps (`accuracy()` already collected the pairs for the
History screen's "predicted vs actual" table). Below 8 samples for a class
— the same threshold `profile()` uses for its "measured" label — this returns
`calibrated: False` and `estimator.estimate()` leaves the band exactly as
`loop_budget` computed it. This is explicitly *not* split-conformal
prediction: real conformal guarantees need roughly 19+ exchangeable samples
for valid 95% coverage, and this app accumulates dozens of projects per class
over normal use, not thousands. Treat it as what it is — a labelled, shrinkage-
blended approximation that gets more trustworthy as your own history grows —
never as a statement that the P95 figure has formal 95% coverage.

### 4.5 `segmenter.py` (211 lines) — prompt → dependency graph

Four passes, all deterministic:

1. **Cut into items.** Prefer structure — bullets or numbered lists, then
   paragraph breaks. Fall back to sentence splitting keeping only sentences
   headed by an imperative verb. Fragments carrying no action merge into the
   previous item.
2. **Extract artifacts** — file paths by extension regex, quoted names,
   PascalCase identifiers with known suffixes (`Service`, `Controller`, …).
3. **Infer edges.** Item B depends on A if B references an artifact A
   introduced, if B opens with an ordering cue (`then`, `after`, `using the`),
   or if B opens with a back-reference pronoun and has no other anchor.
4. **Stage assignment** — longest-path depth: `stage[i] = max(stage[deps]) + 1`.
   Because items are processed in order and edges only point backwards, this is
   a single O(V+E) pass with no cycle risk.

**Savings decomposition.** Three separate real estimates per segment — same
model at full context, routed model at full context, routed model at pruned
context — so the difference between consecutive lines *is* the saving. Nothing
is a made-up percentage, and the table reconciles.

### 4.6 `planner.py` (324 lines) — orchestration

**Model routing.** Keyword-driven tier movement: a segment matching cheap
markers (`readme`, `boilerplate`, `format`, `typo`) and no hard markers
(`architect`, `security`, `concurrency`) drops one tier.

**Sub-segment turn scaling.** `scale = 1.25 / n_items`. Splitting removes
coordination overhead, so the parts sum to slightly more than the whole — never
to *n* whole prompts, which was a real bug earlier where every segment inherited
the full parent estimate.

**Window packing — greedy first-fit over an anchored calendar.**

```
cap = remaining₅ − margin
for step in topological order:
    if weekly_used + need₇ > remaining₇: block it
    if window.used + need₅ > window.budget and window not empty:
        open the next window (+5h)
    place step; window.used += need₅
```

Greedy rather than optimal because the objective is *fill each window before it
evaporates* — unused plan percent is lost at reset, so packing full dominates
balancing. A proper RCPSP solve (OR-Tools CP-SAT) would improve makespan on
complex DAGs; greedy was chosen to keep the zero-dependency promise.

**Progress estimation.** Three signals, max-combined:

```
progress = max(iterations / T₅₀ × 0.6, spend / cost₅₀ × 0.6)   capped at 0.85
         → 1.0 when the oracle passes
```

Capped below 1 because only a passing deliverable check means done. Project
progress is the cost-weighted mean across steps, so a large step counts more.

**Prompt reconstruction for resumption.** On a red or critical step, all logged
summaries are folded into the next prompt under *"work already completed — do
not redo the items above"*, plus the iteration cap and the acceptance check.
This makes an interrupted run resumable instead of restarting from zero.

### 4.7 `oracles.py` (155 lines) — stopping without a model

The answer to "can the iteration loop run without an AI model?" — yes, if
done-ness is machine-checkable.

| Kind | Check |
|---|---|
| `file_exists` | exists and ≥16 bytes |
| `command` / `tests` | subprocess exit code 0, 120s timeout — refuses to run at all until `steps.approved` is set (September 2026; PLS-DO S1) |
| `contains` | regex over file, `path::pattern`, literal fallback on bad regex |
| `json_valid` | parses |
| `no_progress` | output identical to previous iteration → converged, stop |
| `manual` | you decide |

Test commands are auto-detected by marker file (`pyproject.toml` → `pytest -q`,
`package.json` → `npm test`, `go.mod` → `go test ./...`).

When no deliverable is named, one is **derived from the task class** by lookup
table — not invented by a model. Three universal stops are always active:
iteration cap, spend past P95, and no-progress.

### 4.8 `providers.py` (277 lines) — optional planners

Four backends behind one interface: `ollama` (local, free), `anthropic`,
`openai`, `gemini`. All via `urllib` — no SDKs.

**The design rule.** The provider is asked to *enumerate work*, never to
estimate cost. It returns `{items: [{title, detail, complexity 1-5}], notes}`.
`scope.from_plan` converts that to a multiplier and the deterministic engine
prices it. Models are good at listing steps and uncalibrated at predicting token
spend; this split keeps the calibratable part calibratable.

**Robust JSON extraction.** Models wrap JSON in fences and prose, so: strip
fences → `json.loads` → on failure, regex the outermost `{...}` → accept
`items` / `steps` / `tasks` as the list key → accept bare strings as items →
clamp complexity to 1–5.

**Key handling.** Stored in `settings`; `public_config()` masks to
`sk-ope…3456` and the full key is never returned to the browser. A masked value
echoed back is ignored rather than saved as the key. As of September 2026,
`save_config()`/`config()` are split deliberately: `config()` decrypts for
internal use (calling a provider's API), `save_config()` reads and writes the
*raw* stored dict directly rather than round-tripping through `config()` —
doing the latter was a real bug caught during implementation, since it would
have re-persisted every other provider's already-DPAPI-encrypted key back to
disk in plaintext every time any single key changed. See `dpapi.py` and
§2's `settings` row.

---

## 5. Delivery

### `server.py` (~660 lines) — stdlib HTTP

`ThreadingHTTPServer` bound to 127.0.0.1. A decorator-based router compiles
`/api/steps/{id}` into a named-group regex once at import; dispatch is a linear
scan over ~32 routes, which at localhost volumes is free. Static files are served
with a `relative_to` containment check against the web root to block traversal.
Unknown paths fall through to `index.html` so the SPA's hash routes work.

**Request-level security (September 2026, PLS-DO S1 broadened).** Every
request is checked before routing: the `Host` header must name this server's
own bind address (`_host_ok()`) — otherwise a DNS-rebinding attack (the same
bug class that has hit Ollama and Jupyter) could get a browser to treat an
attacker's domain as same-origin with 127.0.0.1. Every mutating method
(POST/PATCH/DELETE) must also carry `CSRF_TOKEN` — a random value generated
once per process (`secrets.token_hex(16)`, never written to disk) — as the
`X-PromptMeter-Token` header; a forged cross-origin form POST cannot set a
custom header, and the server sends no permissive CORS headers, so a page on
another origin can't read the response and mint that header itself. The
frontend reads the token from `GET /api/status` (a GET is exempt from the
check) at boot, before it can need it. `POST /api/reset` additionally requires
`{"confirm": "RESET_ALL_DATA"}` in the body — it used to wipe the entire
database on an unconfirmed, unauthenticated POST, a worse sibling of the
shell-oracle gap PLS-DO S1 originally scoped.

**`GET`/`PATCH /api/settings`.** The persistent selection bar's server-side
half: default model, effort, and surface, one `settings` key per field
(`default_model`/`default_effort`/`default_surface`). `plan` already had its
own route (`/api/setup/plan`) before this existed and keeps it.

### `installer.py` (254 lines) — editing settings safely

Merges one key into `~/.claude/settings.json`: parse (tolerating `//` comments)
→ **refuse if unparseable** rather than clobber → timestamped backup → write to
`.tmp` → atomic `replace()`. Ownership is detected by *script path*, not a magic
word, because the containing folder is whatever you named it.

### `web/` (~1,800 lines) — no build step

Vanilla JS, hash routing, `async` view functions returning HTML strings. Every
interpolation goes through `esc()`. Charts are hand-written SVG. Colours are CSS
custom properties from a CVD-validated palette, declared once and swapped for
dark mode in one place.

**The selection bar (September 2026)**, `renderSelectionBar()`, fills
`<header id="selection-bar">` above `<main>` — outside the router, so it
survives every route change. Surface, plan, model and effort are set once
here instead of per prompt; model/effort persist server-side via
`PATCH /api/settings` the same way `plan` already did (`localStorage` is only
the instant-paint fallback before that first round-trip resolves). Surface is
informational only — restricted to `terminal`/`desktop`, the only two
`meter.py` can actually see, and it never adjusts cost math, since nothing
here measures a real difference between them (§8's "honest failure over
silent guessing").

---

## 6. Named algorithms, in one table

| Where | Algorithm |
|---|---|
| watcher | incremental file tailing with offset memory; idempotent upsert by UUID |
| meter | anchored-window reconstruction; single-pass window replay |
| meter | capacity solving by division; least-squares through the origin for α |
| meter | trailing-window differencing for burn rate |
| estimator | weighted multi-class keyword scoring |
| estimator | logistic regression with log-compressed counts and a damping scale |
| estimator | EWMA self-calibration of the token ratio |
| pricing | arithmetic-series summation of quadratic context growth |
| learning | empirical-Bayes shrinkage (`n/(n+k)`); interpolated quantiles |
| learning | shrinkage-blended P95:P50 ratio calibration (labelled approximation, not split-conformal) |
| pricing | EWMA-learned per-model thinking-share, overriding the static effort curve once trusted |
| dpapi | Windows DPAPI (`CryptProtectData`/`CryptUnprotectData`), user-scoped, via `ctypes` |
| scope | additive feature scoring with power-law compression |
| segmenter | rule-based dependency inference; longest-path DAG layering |
| planner | greedy first-fit bin packing over a time-anchored calendar |
| oracles | fixed-point detection (no-progress convergence) |
| providers | tolerant JSON extraction with fallbacks |

---

## 7. Where it is weak

**The band is not statistically calibrated in the formal sense.** As of
September 2026, `learning.calibration_factor()` blends the prior P95:P50
spread with your own predicted-vs-actual history once a task class has 8+
completed steps — a real improvement, but explicitly a labelled shrinkage
approximation, not textbook split-conformal prediction (that needs ~19+
samples for guaranteed coverage; see §4.4). Below that threshold, or for a
class you haven't used yet, the band is still exactly the prior-quantile
figure this paragraph originally described. Read P95 as *pessimistic*, not as
"95% of runs land under this."

**Token counting is a heuristic.** No official offline tokenizer exists.
Community ones model the Claude 3-era tokenizer and undercount current models by
roughly 30%. The EWMA correction mitigates this only where real usage is seen.

**Cowork and browser usage are invisible.** They leave nothing on disk. A
reading absorbs them into the anchor, but attribution is impossible.

**List price ≠ your bill.** Everything is denominated in list-price dollars,
which is the right unit for comparing work but is not what a flat-rate plan
charges you.

**Greedy scheduling is not optimal.** Fine for the "fill the window" objective,
suboptimal on wide DAGs with mixed step sizes.

**Segmentation is shallow.** Rule-based dependency inference gets roughly the
right shape on structured prompts and gives up on a single long sentence — which
is exactly when the optional planner earns its place.

---

## 8. Design decisions worth knowing

**Standard library only.** FastAPI + React would have meant a pip install, a node
build and a dependency tree that rots. On Windows that is the difference between
"works" and "works if". Cost: ~150 extra lines of routing.

**Deterministic by default, model optional.** Every number is reproducible and
checkable. A model can only ever add structure, never a figure.

**Percent as the currency — for Claude models.** Anthropic enforces on
percentages and publishes no token limits, so percent is ground truth and
tokens are the inference — not the other way round. This is specifically a
Claude Pro/Max plan-window fact, though: `plain.py` (September 2026) branches
on `spec.vendor` so a GPT/Gemini/local estimate speaks in tokens and turns
instead — showing "45% of your 5-hour window" for a model that isn't billed
against any Claude window would be actively wrong, not just Claude-flavoured.
The Windows/Dashboard screen's own window tracking stays Claude-specific on
purpose (it is quite literally tracking an Anthropic subscription plan), but
an *estimate's* plain-language narrative no longer assumes you're looking at
one.

**Honest failure over silent guessing.** Refuse to parse a broken settings file.
Refuse to fit capacity to usage that isn't there. Show *"no reading yet"* as a
hatched bar rather than an empty one that reads as zero. Two September 2026
examples of the same rule: a DPAPI key that can't be decrypted on this
machine reports `"unreadable"` rather than silently failing auth or crashing;
the selection bar's "surface" picker stays informational only because nothing
here actually measures a terminal-vs-desktop cost difference to adjust for.
