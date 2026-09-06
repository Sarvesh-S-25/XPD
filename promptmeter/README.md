# `promptmeter/` — the engine

All the logic. Python standard library only: no pip install, no framework, no
build step. Nothing here talks to the network except `providers.py`, and that is
optional and off by default.

**One rule holds everywhere:** the deterministic code owns every number. A model
may supply *structure* (a list of steps) but is never asked what something will
cost, how risky it is, or when to schedule it.

---

## Reading order

If you are opening this for the first time, read the files in this order —
each one only depends on the ones above it.

```
db.py         where everything is stored
pricing.py    what a token costs, and the loop formula
estimator.py  turns a prompt into a cost band and a risk score
planner.py    turns that into a project with steps and a schedule
server.py     puts it all on http://127.0.0.1:7777
```

---

## Every file

### Foundations

| File | Lines | What it is |
|---|---|---|
| `db.py` | 205 | SQLite access. Thread-local connections (the HTTP server is threaded, SQLite handles are not), WAL mode, schema created on open. Seven tables. Also the JSON key-value `settings` store used for calibration, plan choice and provider config. |
| `pricing.py` | 351 | The model catalogue — 13 models across Anthropic, OpenAI, Google, plus a free local entry — and the cost formula. `loop_budget()` is the heart of the project: it accounts for the conversation an agent re-sends every turn, the context window filling up, compaction, thinking tokens and reasoning effort. |

### Measurement — what actually happened

| File | Lines | What it is |
|---|---|---|
| `watcher.py` | 259 | Tails Claude Code's session transcripts under `~/.claude/projects/`. Remembers a byte offset per file so a rescan costs O(new bytes). Runs on a background thread every 20 seconds. This is why usage tracking needs no setup. |
| `meter.py` | 495 | The 5-hour and 7-day window ledgers. Reconstructs an anchored window from turn timestamps, ticks a stored reading forward, solves for your plan's true capacity from one percentage, computes burn rate and the projected exhaustion time, and runs the leak detectors. The hardest file here. |

### Estimation — what will happen

| File | Lines | What it is |
|---|---|---|
| `estimator.py` | 321 | Token counting (heuristic, self-correcting), task classification by weighted keyword scoring, and the logistic risk score. Produces the P50/P95 cost band. |
| `scope.py` | 174 | How *big* a prompt is, as opposed to what kind. Counts subsystems, platforms, data entities, listed requirements, integrations, and whether it names a product to clone. Without this, "build a todo app" and "build Zomato" estimate identically. |
| `learning.py` | 223 | Blends built-in priors with your observed history using empirical-Bayes shrinkage (`n/(n+6)`). Reads both ledgers — turns captured automatically *and* iterations you logged by hand. |
| `segmenter.py` | 211 | Splits a prompt into work items, extracts the artifacts each produces, infers dependencies, and lays them out in stages. Also the savings decomposition. |
| `planner.py` | 394 | Orchestration. Decides whether to split, routes cheap steps to cheaper models *within your chosen vendor*, packs steps into 5-hour windows, attributes captured turns to running steps, computes progress, and rebuilds the next prompt for a resumable step. |

### Judgement and presentation

| File | Lines | What it is |
|---|---|---|
| `oracles.py` | 155 | Machine-checkable "is it done" tests: file exists, command exits 0, tests pass, JSON valid, output stopped changing. This is what lets the iteration loop run without a model deciding when to stop. |
| `plain.py` | 170 | Translates the numbers into sentences. `421% of your 5-hour window` becomes *"About 4.2 full sessions — you would hit the limit 3 times and wait roughly 20 hours."* Presentation only; nothing here feeds back into an estimate. |

### Optional and plumbing

| File | Lines | What it is |
|---|---|---|
| `providers.py` | 277 | Optional planners: Ollama (local, free), Claude, OpenAI, Gemini. Asks a model to *enumerate the work*, never to price it. Handles fenced JSON, key masking and friendly auth errors. |
| `installer.py` | 254 | Writes the Claude Code status-line setting safely: refuses to touch an unparseable settings file, backs up first, writes atomically, and can undo itself. |
| `server.py` | 614 | The HTTP server and JSON API. A decorator-based router over `http.server`, ~30 routes, static file serving with a traversal guard. |
| `__main__.py` | 98 | `python -m promptmeter`. Flags: `--port`, `--no-browser`, `--demo`, `--where`, `--connect`, `--disconnect`. |
| `demo.py` | 115 | Sample data so the interface is legible before you have used it for real. |
| `data/` | — | Editable JSON: prices, plan sizes, task priors. See `data/README.md`. |

---

## The two loops

The engine is really two independent halves that meet in one place.

```
MEASUREMENT           watcher ──▶ turns table ──▶ meter ──▶ window state
                                       │
                                       ▼
                                   learning          ← the bridge
                                       │
                                       ▼
ESTIMATION            scope ──▶ estimator ──▶ planner ──▶ steps + schedule
                                    ▲
                                 pricing
```

Measurement works whether or not you ever plan a prompt. Estimation works on day
one with no history at all. `learning` is the only connection: what was measured
becomes the prior for what is predicted next.

---

## Conventions

- **No exceptions escape to the user.** Every route returns JSON; the router
  catches and reports.
- **Refuse rather than guess.** A corrupt settings file is not overwritten. A
  capacity fit with no supporting spend is skipped, not fudged.
- **Editable data beats hardcoded constants.** Prices, priors and plan sizes are
  JSON, because they go stale and a stale constant is a silently wrong app.
- **Everything is `from __future__ import annotations`** so the type hints work
  on Python 3.9.
