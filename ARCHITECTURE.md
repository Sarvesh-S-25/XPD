# PromptMeter — architecture, conventions, and project history

Everything about the project that isn't "how do I start it and use it" — that
lives in **[`README.md`](README.md)**. This file is the rest: what each module
does, the algorithm it uses, the formulas, the data files you're meant to
tune, the frontend's conventions and design system, the live-meter bridge's
protocol, where the system is weak, the design decisions worth knowing, and
the full audit history of what's been found and fixed.

**Scale:** ~3,700 lines of Python across 17 modules, ~1,800 lines of frontend.
Python standard library only — no pip install, no Node, no build step.

**One rule runs through the whole design:** *the deterministic engine owns every
number; a model may only supply structure.* Everything that produces a cost, a
probability, or a schedule is plain code you can read and check against history.
Models are optional and are never asked what something will cost.

## Contents

1. System shape
2. The engine package (`promptmeter/`) — map and conventions
3. Storage — `db.py`
4. Measurement — `watcher.py`, `meter.py`
5. Estimation — `pricing.py`, `estimator.py`, `scope.py`, `learning.py`, `segmenter.py`, `planner.py`, `oracles.py`, `providers.py`
6. Delivery — `server.py`, `installer.py`, `web/`
7. The live meter bridge (`shim/`)
8. The tunable data files (`promptmeter/data/`)
9. Named algorithms, in one table
10. Where it is weak
11. Design decisions worth knowing
12. Project history — audit findings, fixed and open

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
                         SQLite (8 tables)
```

**Two independent loops.** The *measurement* loop (watcher → meter) records what
actually happened. The *estimation* loop (estimator → planner) predicts what will
happen. `learning` is the bridge: measurements become the priors for future
predictions. They can run without each other — estimation works on day one with
no history, and measurement works whether or not you ever plan a prompt.

---

## 2. The engine package (`promptmeter/`) — map and conventions

All the logic. Python standard library only: no pip install, no framework, no
build step. Nothing here talks to the network except `providers.py`, and that
is optional and off by default.

**Reading order**, if you're opening this for the first time — each file only
depends on the ones above it:

```
db.py         where everything is stored
pricing.py    what a token costs, and the loop formula
estimator.py  turns a prompt into a cost band and a risk score
planner.py    turns that into a project with steps and a schedule
server.py     puts it all on http://127.0.0.1:7777
```

**Every file, by role:**

| File | Lines | What it is |
|---|---|---|
| `db.py` | 205 | SQLite access, migrations, the JSON key-value `settings` store. §3 |
| `pricing.py` | ~360 | The model catalogue and the cost formula, `loop_budget()`. §5.1 |
| `watcher.py` | ~270 | Tails Claude Code's session transcripts. §4.1 |
| `meter.py` | 495 | The 5-hour and 7-day window ledgers. §4.2 |
| `estimator.py` | 321 | Token counting, task classification, the logistic risk score. §5.2 |
| `scope.py` | 174 | How *big* a prompt is, as opposed to what kind. §5.3 |
| `learning.py` | ~250 | Blends built-in priors with your observed history; P95-band calibration. §5.4 |
| `segmenter.py` | 211 | Splits a prompt into work items and a dependency DAG. §5.5 |
| `planner.py` | 394 | Orchestration: split decision, routing, scheduling, resumption. §5.6 |
| `oracles.py` | ~165 | Machine-checkable "is it done" tests, gated by a shell-command approval flag. §5.7 |
| `plain.py` | ~200 | Translates numbers into sentences, branching on vendor. §11 |
| `providers.py` | 277 | Optional planners: Ollama, Claude, OpenAI, Gemini. §5.8 |
| `dpapi.py` | ~90 | Windows DPAPI at-rest encryption for provider keys. §12 |
| `installer.py` | 254 | Writes the Claude Code status-line setting safely. §6 |
| `server.py` | ~700 | The HTTP server and JSON API. §6 |
| `__main__.py` | ~100 | `python -m promptmeter`. Flags: `--port`, `--no-browser`, `--demo`, `--where`, `--connect`, `--disconnect`. |
| `demo.py` | ~130 | Sample data, flagged `is_demo` so it never pollutes real totals. §12 |
| `data/` | — | Editable JSON: prices, plan sizes, task priors. §8. |

**Conventions:**

- **No exceptions escape to the user.** Every route returns JSON; the router
  catches and reports.
- **Refuse rather than guess.** A corrupt settings file is not overwritten. A
  capacity fit with no supporting spend is skipped, not fudged.
- **Editable data beats hardcoded constants.** Prices, priors and plan sizes are
  JSON, because they go stale and a stale constant is a silently wrong app.
- **Everything is `from __future__ import annotations`** so the type hints work
  on Python 3.9.
- **Generated code stays in this package or `tests/`.** No third-party
  dependency has ever been added; a new one would break the zero-install
  promise §11 names as a real design decision, not an accident.

---

## 3. Storage — `db.py` (205 lines)

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

**Eight tables, three with an added column since the schema was first written**

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

## 4. Measurement

### 4.1 `watcher.py` — tailing Claude Code's transcripts

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
claude.ai runs in a browser. Neither writes here, so neither is visible. A
synced reading (§4.2) absorbs that usage into the anchor even though it can't
be attributed to a specific turn — the percentages you read already include
it, so the ledger self-corrects at every reading rather than staying wrong.
The same correction applies to other devices: `/usage` and this watcher are
both computed from local history on *this* machine only.

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

### 4.2 `meter.py` (495 lines) — the two window ledgers

The hardest part of the system, because the ground truth is partly unobservable.

A Pro or Max plan has two limits: a **rolling 5-hour window** and a **7-day
weekly window**. Both are shared across Claude chat, Claude Code and Cowork —
same pool, three surfaces. Opus has a separate limit on top of those. Anthropic
does not publish the numbers behind them — Max 5x is documented as "five times
more usage per session than Pro," with no token figure anywhere — so this
module reads the **percentages**, the quantity actually enforced, and fits
everything else from those.

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

Once samples come in, this is one number — percent of window consumed per
dollar of list-price work, inverted — giving your plan's real capacity in
dollars-equivalent per window: the limit that isn't published, derived from
your own usage. It's labelled *default* / *learning* / *measured* on the
Windows and Setup pages so you always know how much to trust it.

**Algorithm — least-squares α (legacy path).** With a stream of status-line
samples, `α` = percent consumed per list-price dollar is fitted through the
origin: `α = Σ(pct·cost) / Σ(cost²)`. Pairs that straddle a reset — where the
percentage went *down* — are discarded. The direct capacity solve above
supersedes this, but it still refines the estimate when readings stream in.
(`meter._fit()` itself no longer feeds `alphas()` — see §10, dead code.)

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

## 5. Estimation

### 5.1 `pricing.py` — the cost model

Prices live in editable JSON (§8), not constants, because a stale price table is
a quietly wrong app.

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
bill**, which is the single most important fact the tool exists to surface. A
"20k prompt" that runs 30 turns is a $10 request, not a $0.13 one; turn count
is the variable the estimator predicts hardest, because input size is measured
directly while output length and turn count are genuinely uncertain — the
estimator predicts a **band** (typical and worst case), never a single number.

**Multi-vendor catalogue.** Anthropic, OpenAI and Google in one table, keyed on
what differs between them: cache pricing (Anthropic charges to *write*, the
others cache free), context window (200k–1.05M), max output per reply, thinking
share by effort, and Gemini's above-200k long-context rate tier. Full field
reference in §8.

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

Compaction is a real cost, not just a limit: when the conversation outgrows the
window the agent must summarise and continue — a large read plus a summary
write — and this accounting counts it rather than treating the window as a
hard wall.

`effort_multiplier = (1 − share_default) / (1 − share_effort)` — thinking tokens
are billed as output tokens by every vendor, and are produced *on top of* the
visible answer, so raising effort raises total output and therefore cost.
Priors were learned at default effort (`medium` ≈ 1.0×, `max` ≈ 3×), which
anchors the multiplier.

### 5.2 `estimator.py` (321 lines)

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

**Risk scoring — logistic with a damping term.** Risk here has a real
definition: **the probability this run hits a wall** — the tail of the
predicted cost distribution above what's left in your window. It's a number
you can check, and the History screen checks it (§5.4, §12).

```
scale = clamp(log1p(T₉₅) / log1p(40), 0.12, 1.0)
z = −2.2 + scale × Σ(wᵢ · featureᵢ) + budget_pressure
p = 1 / (1 + e^−z)
```

Count-type features are compressed with `log1p(v)×1.6` so ten files don't score
ten times one file. The **scale** term is the important one: runaway features
(no turn cap, shell access, vague wording) are weighted by how much room the task
has to run away in, so a two-turn question can't score high no matter how
loosely worded. In practice the big four drivers are: no turn cap, shell access,
no acceptance criteria, and open-ended wording ("keep going until it works").

```
budget_pressure = min(2.2, 1.5 × ln(P95_cost / remaining_budget))   if > 1
```

Capped, because past "definitely doesn't fit" the extra information lives in the
cost band, not the probability — without the cap every large job saturated at 99%.

Bands: green <10%, amber <30%, red <60%, critical ≥60%.

**Producing the band.** Evaluate `loop_cost` twice — at `(T₅₀, ō₅₀)` and
`(T₉₅, ō₉₅)`. This is a *bracket*, not a fitted predictive distribution (see
§5.4 for the calibration step that partially addresses this, and §10 for what
it still doesn't claim).

### 5.3 `scope.py` (174 lines) — how big is this, really

The task class says *what kind* of work it is; scope says *how much*. Without
this, `build a todo app` and `build a food delivery app with payments, tracking,
a dashboard and a mobile client` estimate identically — the class prior
dominates and the prompt text contributes almost nothing.

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
own class rather than absolute (`build a todo app` lands around 0.5×; a
food-delivery app with payments, tracking, a dashboard and a mobile client
lands above 1.2×).

**From a drafted plan** (`from_plan`) the same machinery runs on step count and
average complexity instead of keywords: `score = 1 + 0.30 × n × (avg_complexity / 3)`.
This is the path used when a provider (§5.8) drafts the plan instead of the
keyword heuristic — **the model never sets a price**, either way; it lists the
steps, PromptMeter costs them with its own numbers, which stay checkable
against your history.

### 5.4 `learning.py` — priors giving way to your data

**Shrinkage blending.** For each (task class, model) pair:

```
w = n / (n + 6)
prediction = prior × (1 − w) + observed × w
```

`k = 6` means two samples sit at 25% weight and fifty at 89%. This is the standard
empirical-Bayes shrinkage estimator, and it's what stops one weird project from
rewriting your priors. The History screen labels each row *prior only* /
*learning* / *measured* so you always know which you're looking at, and reads
**both** usage ledgers — turns captured automatically *and* iterations you
logged by hand — for this blend.

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

### 5.5 `segmenter.py` (211 lines) — prompt → dependency graph

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
is a made-up percentage, and the table reconciles. **Splitting a prompt does
not save money by itself** — each step re-sends its own context — so the app
only splits when it helps, and names which of three things pays for it: less
context re-sent per turn (usually the largest), cheaper models on the simple
steps, or each step carrying only what it touches. If a split comes out *more*
expensive, the UI says so and names the only two reasons left to do it anyway:
it fits inside your window, and a failed step costs one step's budget instead
of the whole run's.

### 5.6 `planner.py` (324 lines) — orchestration

**Model routing.** Keyword-driven tier movement: a segment matching cheap
markers (`readme`, `boilerplate`, `format`, `typo`) and no hard markers
(`architect`, `security`, `concurrency`) drops one tier — **within the same
vendor only** (a fixed bug: routing used to jump to a hardcoded Claude model
regardless of which vendor you'd picked; see §12, "Fixed in this pass" #1).

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
not redo the items above,"* plus the iteration cap and the acceptance check.
This makes an interrupted run resumable instead of restarting from zero — the
**Copy prompt** button is what surfaces this.

### 5.7 `oracles.py` — stopping without a model

The answer to "can the iteration loop run without an AI model?" — yes, if
done-ness is machine-checkable.

| Kind | Passes when |
|---|---|
| `file_exists` | exists and ≥16 bytes |
| `command` / `tests` | subprocess exit code 0, 120s timeout — refuses to run at all until `steps.approved` is set (September 2026; §12, S1) |
| `contains` | regex over file, `path::pattern`, literal fallback on bad regex |
| `json_valid` | parses |
| `no_progress` | output identical to previous iteration → converged, stop |
| `manual` | you decide |

Test commands are auto-detected by marker file (`pyproject.toml`/`pytest.ini` →
`pytest -q`, `package.json` → `npm test`, `go.mod` → `go test ./...`, `Cargo.toml`
→ `cargo test -q`).

When no deliverable is named, one is **derived from the task class** by lookup
table — not invented by a model. Three universal stops are always active:
iteration cap, spend past P95, and no-progress.

### 5.8 `providers.py` (277 lines) — optional planners

Four backends behind one interface: `ollama` (local, free), `anthropic`,
`openai`, `gemini`. All via `urllib` — no SDKs. Leave the method on
**Built-in** (the default) and nothing here is ever used — no key, no network,
no cost.

**The design rule.** The provider is asked to *enumerate work*, never to
estimate cost. It returns `{items: [{title, detail, complexity 1-5}], notes}`.
`scope.from_plan` (§5.3) converts that to a multiplier and the deterministic
engine prices it. Models are good at listing steps and uncalibrated at
predicting token spend; this split keeps the calibratable part calibratable.

**Robust JSON extraction.** Models wrap JSON in fences and prose, so: strip
fences → `json.loads` → on failure, regex the outermost `{...}` → accept
`items` / `steps` / `tasks` as the list key → accept bare strings as items →
clamp complexity to 1–5.

**Key handling.** Stored in `settings`; `public_config()` masks to
`sk-ope…3456` and the full key is never returned to the browser. A masked value
echoed back is ignored rather than saved as the key.

**`.env` fallback (September 2026).** `load_dotenv()` is a ~15-line stdlib-only
parser (no `pip install python-dotenv`) called once at `server.serve()`
startup — reads `KEY=VALUE` lines from a `.env` at the project root into
`os.environ`, skipping comments/blanks, never overwriting a variable the real
environment already set. `config()` then falls back to
`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` only for a provider with
nothing saved in `settings` — a key pasted into the Setup page always wins,
and removing that saved key reveals the `.env` value again rather than
requiring you to re-paste it. `public_config()`'s `key_storage` reports
`"environment"` for a key sourced this way, distinct from `"encrypted"` /
`"plaintext"` / `"unreadable"`, so the Setup page never claims a `.env`-backed
key is protected by DPAPI when it isn't. See `.env.example` at the project
root.

As of September 2026,
`save_config()`/`config()` are split deliberately: `config()` decrypts for
internal use (calling a provider's API), `save_config()` reads and writes the
*raw* stored dict directly rather than round-tripping through `config()` —
doing the latter was a real bug caught during implementation, since it would
have re-persisted every other provider's already-DPAPI-encrypted key back to
disk in plaintext every time any single key changed. See §12 (S2) and §3's
`settings` row.

---

## 6. Delivery

### `server.py` (~700 lines) — stdlib HTTP

`ThreadingHTTPServer` bound to 127.0.0.1. A decorator-based router compiles
`/api/steps/{id}` into a named-group regex once at import; dispatch is a linear
scan over ~32 routes, which at localhost volumes is free. Static files are served
with a `relative_to` containment check against the web root to block traversal.
Unknown paths fall through to `index.html` so the SPA's hash routes work.

**Request-level security (September 2026, §12 S1 broadened).** Every
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
shell-oracle gap §12 S1 originally scoped.

**`GET`/`PATCH /api/settings`.** The persistent selection bar's server-side
half: default model, effort, and surface, one `settings` key per field
(`default_model`/`default_effort`/`default_surface`). `plan` already had its
own route (`/api/setup/plan`) before this existed and keeps it.

### `installer.py` (254 lines) — editing settings safely

Merges one key into `~/.claude/settings.json`: parse (tolerating `//` comments)
→ **refuse if unparseable** rather than clobber → timestamped backup → write to
`.tmp` → atomic `replace()`. Ownership is detected by *script path*, not a magic
word, because the containing folder is whatever you named it. See §7 for the
protocol this connects to.

### `web/` (~1,800 lines across 3 files) — no build step

No framework, no `node_modules`. The Python server serves this folder
directly; editing a file and refreshing the browser is the whole development
loop.

| File | Lines | What it is |
|---|---|---|
| `index.html` | 35 | The shell. Sidebar, an empty `<header id="selection-bar">` + `<main>` pair, two `<script>`/`<link>` tags. Everything else is rendered by JavaScript. |
| `styles.css` | ~400 | Design tokens and components. No utility framework. |
| `app.js` | ~1,800 | The entire application: routing, data fetching, and every screen. |

**How `app.js` is organised** — it reads top to bottom in the order things happen:

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
reconciliation — the whole screen is rebuilt on every change. At this scale
that is simpler and fast enough.

**The five screens**

| Route | Function | What it answers |
|---|---|---|
| `#dashboard` | `viewDashboard` | How much of my limit is left, and when do I run out? |
| `#plan` | `viewPlan` | What will this prompt cost, and should I split it? |
| `#projects` | `viewProjects` | What am I working on and how far along is it? |
| `#project/:id` | `viewProject` | Steps, iterations, the step graph. |
| `#history` | `viewHistory` | What has it learned, and were its estimates right? |
| `#setup` | `viewSetup` | Tracking status, calibration, providers, the status line. |

**Rules the code follows:**

- **Every interpolation goes through `esc()`.** Views build HTML by string
  concatenation, so this is the only thing standing between a prompt containing
  `<script>` and an XSS bug. No exceptions.
- **Colours come from CSS custom properties, never literals.** `var(--accent)`,
  not a hex code. That is what makes the dark theme a single block of overrides
  rather than a rewrite.
- **Charts are hand-written SVG.** The rings, meters, sparkline and step graph
  are all built by generating SVG strings. No charting library, so no
  dependency and no bundle.
- **Plain language leads, numbers follow.** The Plan screen opens with a
  sentence ("Do not send this as one prompt") and hides percentages and
  dollars behind disclosure. The server computes both; the UI chooses the order.

**The selection bar** (September 2026), `renderSelectionBar()`, fills
`<header id="selection-bar">` above `<main>` — outside the router, so it
survives every route change; it's redrawn once per `render()` call, after
`sideMeter()` so it always has a fresh `S.status`. Surface, plan, model and
effort are set once here instead of per prompt. Model and effort persist
server-side via `PATCH /api/settings` (`localStorage` is only the instant-paint
fallback before that first round-trip resolves); plan already persisted
server-side before this existed. Surface is informational only — restricted
to `terminal`/`desktop`, the only two `meter.py` can actually see, and it
never adjusts cost math, since nothing here measures a real difference
between them (§11, "honest failure over silent guessing").

**The design system in `styles.css`.** The identity (September 2026 redesign):
an instrument, not a SaaS dashboard. PromptMeter's actual subject is a meter —
a precise readout of what work costs — so the visual language borrows from
real instrumentation rather than generic app chrome: monospaced, tabular
numerals standing in for a digital readout wherever a number is the point of
the screen (`.stat-value`, `.ring-meta .n`, table `.num` cells, `.kv dd` —
deliberately *not* `.hero`, which renders a sentence, not a reading), flat
panels with no drop shadows (a gauge is flat-mounted, not floating), a radius
scale with real hierarchy (`--r` 12px for panels, `--r-sm` 6px for controls —
never one border-radius applied to everything), and — used exactly once,
deliberately, on the two numbers the whole product exists to show — twelve
gauge tick marks around the Dashboard's rings (`ring()` in `app.js`).

Tokens are declared once on `:root` and overridden in two dark-mode blocks —
one for the OS setting, one for the in-app toggle.

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

**`--accent` is deliberately not `--series-1`.** Before the redesign, the
app's primary/brand colour and `--series-1` (Opus's tier colour on a model
chip) were the same blue — one hue silently meaning two different things.
`--accent` is a separate signal-teal reserved for UI chrome; `--series-1..4`
and the status colours stay exactly the colour-blind-validated reference
values they always were and are never repurposed as decoration.

The palette is colour-blind-validated. Two details that were bugs before they
were rules: the dark-mode meter track must be the *darkest* step of the blue
ramp, or an empty meter reads as a full one; and a meter with no reading
renders as a hatched `.unknown` track, never as an empty bar, because empty
and unknown must not look alike. `.chip.warning`/`.chip.serious` also carry
their own background wash (`--warning-bg`/`--serious-bg`), not just a
swatch-dot colour — the two used to be visually near-identical
text-on-`--page`, distinguished only by an 8px dot.

Components: `.card`, `.banner`, `.chip`, `.meter`, `.progress-track`, `.step`,
`.stepn` (numbered setup steps), `.selbar` (the selection bar), `.modal-bg`,
`.toast`.

**Adding a screen:** write `async function viewThing() { return \`<html>\` }`,
add it to the `VIEWS` map, add a `<button class="nav-item" data-route="thing">`
to `index.html`. That's the whole procedure — no build, no registration, no config.

---

## 7. The live meter bridge (`shim/`)

One file, `statusline.py`, ~80 lines. Optional. This is the only piece of
PromptMeter that runs *inside* Claude Code rather than beside it.

**Why it exists.** Claude Code's status line is the **only supported place**
where your real plan percentages are handed to a local program. Everything
else — the desktop app's usage dialog, `/usage` — renders them and throws them
away. There is no cached copy on disk and no CLI command that prints them. So
if you want live limits rather than estimates (§4.2's "measured" basis), this
is the mechanism. The full connect-it walkthrough is in
**[`README.md`](README.md)**.

**What Claude Code sends it.** On every render, Claude Code pipes a JSON blob
to this script on stdin:

```json
{
  "session_id": "...",
  "model": { "id": "claude-sonnet-5", "display_name": "Sonnet" },
  "context_window": { "used_percentage": 34.2, "current_usage": { ... } },
  "rate_limits": {
    "five_hour": { "used_percentage": 23.5, "resets_at": 1738425600 },
    "seven_day": { "used_percentage": 41.2, "resets_at": 1738857600 }
  }
}
```

`rate_limits` appears **only for Claude Pro and Max subscribers**, and only
after the first response in a session. On an API key it is absent.

**What it does:** reads stdin; POSTs it verbatim to
`http://127.0.0.1:7777/api/ingest`, with a **0.4 second timeout**, ignoring
every possible failure; prints a one-line bar:
`Sonnet  5h [###.......] 31%  wk [#.........] 12%`.

**The rules it obeys.** It must never block your session — the timeout is
short and every network error is swallowed; if PromptMeter isn't running, the
bar still prints and Claude Code carries on. A status line that hangs would
make Claude Code feel broken, a far worse failure than a missing meter. It
must never crash — malformed JSON, missing keys, absent `rate_limits` all
produce a printed line rather than a traceback. It runs on every render, which
is often, so nothing expensive belongs here.

**Environment variables:**

| Variable | Effect |
|---|---|
| `PROMPTMETER_PORT` | Post somewhere other than 7777 |
| `PROMPTMETER_SELFTEST` | Print the bar but post nothing — used by **Test it** on the Setup page so a check never writes fake data |

**Installing it.** Do not hand-edit. Press **Write the setting** on the Setup
page, or run `python -m promptmeter --connect`. Both work out the absolute
paths for your machine, back up your existing `settings.json`, and merge in a
single key while leaving everything else alone (`installer.py`, above).
`--disconnect` undoes it. The setting it writes:

```json
{ "statusLine": { "type": "command", "command": "\"<python>\" \"<this file>\"" } }
```

**It does nothing in the Claude desktop app**, which has no status line to
attach to. Terminal Claude Code only.

---

## 8. The tunable data files (`promptmeter/data/`)

Three JSON files. These are **data, not code** — deliberately, because prices
change, models get released, and a hardcoded constant that has gone stale is a
quietly wrong app.

Edit any of them in a text editor and restart. If a file is missing or corrupt
it is regenerated from the defaults in `pricing.py`, so you cannot break the
app by editing it badly — worst case you lose your edits.

| File | Holds |
|---|---|
| `models.json` | Every model: prices, context window, output cap, thinking behaviour |
| `plans.json` | How much work a Pro / Max 5x / Max 20x window holds |
| `priors.json` | How long each kind of task typically runs, before it has seen you work |

### `models.json`

One entry per model. Fields:

| Field | Meaning |
|---|---|
| `vendor` | `anthropic` / `openai` / `google` / `local` — groups the picker, and keeps model routing inside your vendor |
| `label` | What you see in the interface |
| `tier` | `haiku` / `sonnet` / `opus` — the routing ladder. A cheap step drops one tier *within the same vendor* |
| `in` / `out` | List price, USD per million tokens |
| `cache_read` | Multiplier on the input price for a cache hit. `0.1` everywhere so far |
| `cache_write_1h` / `_5m` | Multiplier for *writing* the cache. Anthropic charges 2.0 / 1.25; OpenAI and Google cache automatically, so `1.0` |
| `context` | Input context window in tokens. Drives the compaction estimate |
| `max_output` | Ceiling on a single reply. Long answers get truncated or continued |
| `thinking` | Whether the model reasons before answering |
| `thinking_share` | Fraction of output that is reasoning, per effort level |
| `long_context` | Optional. A higher rate tier above a token threshold — Gemini Pro does this above 200k |

**Adding a model** — copy the closest entry, change the values, restart.
Nothing else needs touching; the picker, the estimator and the routing ladder
all read from here.

**`thinking_share` is the softest number in the project.** No vendor discloses
what fraction of output is reasoning; this table is a modelling assumption. It
is the first thing to tune if your estimates run consistently high or low on a
reasoning model.

**As of September 2026 it learns.** Once the watcher has seen at least
`pricing.MIN_THINKING_OBS` (5) of your own real turns that actually contained
a thinking block, `pricing.thinking_share()` switches from this static table
to an EWMA-blended average of your own observed thinking-token share for that
model, stored in `settings["thinking_share_learned"]`. It cannot be learned
per effort level — transcripts never record which effort level produced a
turn — so it is one number per model, blending whatever efforts you've
actually used. Below that threshold, or for a model you haven't used yet,
this table is still exactly what gets used.

Prices were verified against vendor pricing pages in **August 2026**. Check
them before trusting a figure to two decimal places.

**Correction, September 2026:** `context`/`max_output` were stale for
`claude-opus-5`, `claude-sonnet-5`, `claude-sonnet-4-6` and `claude-fable-5`
(each had been capped at the prior generation's 200k-context/64k-output
figures). Fixed against Anthropic's current API documentation — prices were
already correct and untouched. OpenAI/Gemini entries were not re-verified in
this pass. There used to be a second, smaller `pricing.json` in this folder;
it was dead — nothing in the code read it — and has been deleted so there is
one source of truth.

### `plans.json`

How much work one window of your subscription holds, in list-price dollars.

```json
{ "pro": { "label": "Pro", "usd_per_5h": 6.0, "usd_per_week": 48.0 } }
```

**These are estimates, and they are meant to be replaced.** Anthropic
publishes no absolute limits for any plan — Max 5x is documented as "five
times more usage per session than Pro," with no token figure anywhere. So
these are starting points chosen from the relative multipliers.

The moment you sync one real reading, PromptMeter **solves for your true
capacity** (`spend in window ÷ percentage used`, §4.2) and stops using this
file. The Windows page then reads *measured* instead of *plan estimate*. If
you never calibrate, this table is what your percentages are based on.

### `priors.json`

How long each kind of task runs, before the app has watched you work.

```json
{ "build_app": { "label": "Build an app", "turns": [35, 120], "out": [1500, 5000] } }
```

`turns` is `[typical, worst case]`; `out` is output tokens per turn, same
shape. Seven task classes, matched by the keyword classifier in `estimator.py`
(§5.2).

**These are my judgement, not measurements.** They are the least
evidence-backed numbers in the project and everything downstream inherits
them. That matters most in your first dozen estimates.

They stop mattering as you use it: `learning.py` (§5.4) blends these with your
observed history using shrinkage weighting `n/(n+6)` — two runs sit at 25%
weight on your own data, fifty at 89%. The History screen labels each row
*prior only* / *learning* / *measured* so you always know which you are
looking at.

**Tuning them:** if the History screen shows your runs consistently landing
above or below the band for one task type, edit that row. It takes effect on
the next estimate.

---

## 9. Named algorithms, in one table

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
| shim | fire-and-forget POST with a 0.4s timeout — never blocks the caller |

---

## 10. Where it is weak

**The band is not statistically calibrated in the formal sense.** As of
September 2026, `learning.calibration_factor()` blends the prior P95:P50
spread with your own predicted-vs-actual history once a task class has 8+
completed steps — a real improvement, but explicitly a labelled shrinkage
approximation, not textbook split-conformal prediction (that needs ~19+
samples for guaranteed coverage; see §5.4). Below that threshold, or for a
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

**Capacity fitting assumes metering is roughly proportional to list price.**
If that stops holding, the confidence label and the residuals (§12,
"predicted vs actual") will show it.

**Greedy scheduling is not optimal.** Fine for the "fill the window" objective,
suboptimal on wide DAGs with mixed step sizes.

**Segmentation is shallow.** Rule-based dependency inference gets roughly the
right shape on structured prompts and gives up on a single long sentence — which
is exactly when the optional planner earns its place.

---

## 11. Design decisions worth knowing

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

---

## 12. Project history — audit findings, fixed and open

An aggressive pass over the whole project: purpose, functionality, UI, and the
things a stranger would trip on. Written by running the code hard, not by
reading it — then a second, September 2026 pass, planned against this section,
checked against Gemini, and verified by actually running the app end to end.

**Answers to a few things asked along the way:**

- **Jupyter?** No, and none needed. Nothing here is exploratory analysis — the
  maths is closed-form arithmetic that runs in the app itself. A notebook
  would be a place for bugs to hide outside the product.
- **A learning model?** No ML library anywhere. "Learning" is empirical-Bayes
  shrinkage — six lines of arithmetic over your own history (§5.4). The only
  optional model is the planner (§5.8), and it is never asked for a number.
- **What predicts the cost?** A closed-form loop formula (§5.1) over four
  inputs: prompt size (measured), turns and output length (predicted from
  priors that shrink toward your data), and context growth (measured). No
  model in that path.

### Fixed in the first pass

1. **Model routing silently switched vendors — HIGH.** `route_model` mapped
   every downgrade to a hardcoded Claude model. Pick Gemini 3.1 Pro, let the
   planner send a cheap step to "haiku," and you'd get billed for Claude with
   Claude's 200k window and Claude's cache rules. Now routes to the cheapest
   model of the target tier **from the same vendor** (§5.6).
2. **Two ledgers that never met — HIGH.** The watcher recorded every turn
   automatically; project spend read only iterations typed in by hand — a
   whole project with 471 turns captured could still show $0.00 spent, 0%
   progress. Turns are now attributed to whichever step was running when
   recorded, and manual logging is optional rather than load-bearing.
3. **Learning never saw the automatic data — HIGH.** `observations()` joined
   on `iterations` only, so the app could watch fifty real runs and still
   report "prior only" forever. It now reads both ledgers.
4. **The numbers were unreadable — HIGH.** "91% to 421%" and "risk 92%" are
   precise and useless unless you already know how Anthropic meters a plan.
   Added a plain-language layer (`plain.py`) that leads the estimate.
5. **The verdict contradicted the risk — HIGH.** An early version of the
   plain layer could say "Just send it" next to a 48% chance of being cut
   off, because size and risk were computed independently. Risk now gates the
   verdict: critical never reads as safe.
6. **Split banner was green when it meant trouble — LOW.** "This looks too
   big for one run" rendered in the success colour.

### Fixed in the September 2026 pass

- **E1 — no tests, HIGH → fixed.** `tests/`, stdlib `unittest` only (`python
  -m unittest discover tests`, 73 tests). Covers `pricing.loop_budget`, the
  calibration function, `meter`'s window reconstruction and capacity solve,
  `segmenter`'s DAG layering, `dpapi`'s round-trip, `plain.py`'s vendor
  branching, and the migration runner's idempotency. No `pytest` — that would
  be a new dependency, contradicting the project's own zero-install rule.
- **E6 — no schema migrations → fixed.** `db.py` gained `schema_version` and
  a small linear migration runner (§3).
- **S1 — deliverable checks run arbitrary shell commands, MED → fixed and
  widened.** The original scope was too narrow: `POST /api/reset` turned out
  to wipe the entire database with zero confirmation and the same missing
  authentication as the shell-oracle path. Fixed together: a per-process CSRF
  token on every mutating route, a Host-header check (closes DNS rebinding),
  an explicit confirm body on reset, and a one-time UI approval before a
  step's `command`/`tests` check can run at all — changing the oracle's kind
  or spec re-arms that approval (§5.7, §6).
- **S2 — API keys sit in plaintext, MED → fixed on Windows, honestly not
  elsewhere.** `dpapi.py`: Windows DPAPI via `ctypes`, user-scoped, verified
  with a real round-trip. macOS/Linux have no stdlib OS-keychain equivalent,
  so keys there stay plaintext and the Setup page says so per key rather than
  implying uniform protection.
- **A5 — `model_table()` on History still reads iterations only → fixed.**
  Now unions `iterations` and the watcher's `turns` the same way
  `observations()` already did.
- **P2 — demo data is indistinguishable from real data → fixed.** `is_demo`
  on every table `demo.py` writes; a "sample" tag wherever a demo project
  appears; excluded from calibration and the dashboard/Projects/History
  "spent" totals. A "Clear sample data" action removes only tagged rows.
- **A1 / A3 — improved, not fully solved.** A1 (band not calibrated):
  `learning.calibration_factor()` — an honest, labelled approximation, not
  textbook split-conformal (§5.4, §10). A3 (`thinking_share` assumed): now
  learned per model from real transcripts, but one number per model rather
  than per (model, effort), because transcripts never record which effort
  produced a turn — an observability limit, not a design choice (§4.1, §8).
- **Caught mid-implementation, not on this list before:** loading sample data
  didn't invalidate the cached dashboard/Projects totals, so the Projects
  screen showed "$0.00 spent" and "0 projects" for several seconds after
  seeding three sample projects — found by actually clicking through the
  running app rather than trusting the diff. Fixed by nulling the cache the
  same way every other mutating action already did.

### Still open, ranked

**P1 — there is no first run.** A new user lands on "Windows" with hatched
empty bars and no idea what the app is for. No one-screen explanation of the
5-hour window, no guided setup, no "start here." *Do:* a first-run screen —
what this is, pick your plan, sync a reading, done. Now the single biggest
gap between "works" and "usable by someone who is not you" — everything
ranked above it last pass (tests, security, most of the calibration work) is
done.

**P3 — step status is still manual.** Turns attribute automatically, but you
must click Start/Done for a step to have a window to attribute *to*. If you
never click Start, nothing attaches. *Do:* infer a step as started on its
first matching turn, or a "start next step" button one click from the top.

**E2 — never run on Windows, partially verified.** The September 2026 pass
ran the app directly (`python -m promptmeter`) and drove the UI in a real
browser on Windows end to end — model catalogue, selection bar, sample-data
seeding, the CSRF/approval security flow, and both themes all confirmed
working live. `start.bat` itself, and the live status-line install flow
specifically, are still unverified by an actual run — those need someone to
click through `start.bat` and the terminal steps in `README.md` for real.

**A2 — the priors are estimates, not measurements.** `build_app = 35/120
turns` came from judgement, not a dataset (§8, `priors.json`). Shrinkage fixes
it as you log runs, but the first dozen estimates are only as good as those
seven rows. *Do:* replace the hand-guessed priors with real measurements, now
that the shrinkage/calibration machinery to actually use them exists.

**P4 — the app plans work it cannot run.** It tells you what to send and
tracks what happened, but you paste prompts yourself. Deliberate (no API key
needed), but the copy never says so plainly, and a new user may expect it to
execute.

Items that are things to *disclose* rather than fix, because they're inherent
to the approach and named elsewhere in this document: **A4** (Cowork/browser
usage invisible, §10), **E3** (no packaging — no `pyproject.toml`, no `pipx
install`, no single-file exe; distribution is "copy a folder"), **E4** (dead
code, `meter._fit()` — the least-squares α fit runs on every status-line
ingest and no longer feeds anything; harmless, misleading to read), **E5**
(greedy scheduler is not optimal, §10).
