# PromptMeter — project instructions

PromptMeter is a local app that estimates what a Claude prompt will cost, scores
the chance it hits a wall mid-run, splits it when splitting helps, tracks project
progress against real usage, and learns your own history over time. It is
**Python standard library only** — no `pip install`, no Node, no build step, no
account, no network calls. One SQLite file in `~/.promptmeter/` is the whole
database. `web/` is three files of vanilla JS/CSS/HTML served directly by the
stdlib HTTP server in `promptmeter/server.py` — no framework, no bundler.

Read `README.md` before touching product behaviour — it explains the nine-step
user flow, the five screens, and how to run and connect everything. Read
`ARCHITECTURE.md` before touching a module — it goes file by file, states the
design rules explicitly (§11), tables the named algorithms (§9), documents the
tunable data files (§8) and the frontend's conventions (§6), and — in §12 — is
the live backlog: what was fixed in each audit pass, and what's still open,
ranked by severity across security, accuracy, product and engineering. Check a
task against §12 before assuming it is new: if what's being asked matches an
open item (e.g. P3 "step status is still manual", A2 "the priors are
estimates, not measurements"), say which item it is and work from that
description rather than rediscovering the problem.

There is no phased build plan here and no `npm run` anything — this is not a
Node project. `node` only appears once in this repo, in `.claude/scripts/gemini.mjs`,
a tooling script for consulting Gemini; it has nothing to do with the app.

## Conventions worth knowing before editing

- **The deterministic engine owns every number; a model may only supply
  structure.** Cost, probability and schedule all come from plain arithmetic you
  can check against history (`ARCHITECTURE.md` §8). Never let a planner model
  (Ollama / API key, `promptmeter/providers.py`) return a number that gets used
  directly as a cost or a percentage — it may only propose steps or structure.
- **Percent is ground truth; tokens are the inference**, not the other way
  round — Anthropic meters and publishes percentages, not token limits.
- **Both usage ledgers matter.** Manual iterations (typed in) and the watcher's
  automatic turns (tailed from Claude Code's transcripts) are two independent
  records of the same spend. Anything that computes progress, spend or
  learning must read both — reading iterations only was a real bug, fixed
  everywhere it was found (`ARCHITECTURE.md` §12). Don't add a new occurrence.
- **`web/app.js` — every interpolation goes through `esc()`.** Views build HTML
  by string concatenation; this is the only thing between a pasted prompt and an
  XSS bug. No exceptions. Colours come from CSS custom properties
  (`var(--accent)`, `var(--series-1)`), never hex literals — see
  `ARCHITECTURE.md` §6.
- **A meter with no reading renders hatched/`.unknown`, never as an empty bar.**
  Empty and unknown must never look alike; this was a real bug.
- **Model routing must stay within the same vendor** when picking a cheaper tier
  (`route_model` in `promptmeter/pricing.py` / `estimator.py`) — routing a
  downgraded Gemini or GPT model to a hardcoded Claude model was the first bug
  fixed, ever (`ARCHITECTURE.md` §12).
- **`oracles.py`'s `command`/`tests` checks require a one-time `approved` flag**
  before they'll run at all, and the server requires a per-process CSRF token
  on every mutating route (`ARCHITECTURE.md` §5.7, §6). Don't weaken either
  guard to make a check "more convenient."
- **API keys are DPAPI-encrypted at rest on Windows**, plaintext elsewhere —
  no stdlib OS-keychain exists for macOS/Linux (`ARCHITECTURE.md` §5.8,
  `dpapi.py`). Don't add a `pip install keyring`-style dependency to close
  that gap; the project's zero-install rule wins over closing it fully.
- **Schema changes go through the migration runner in `db.py`**, never a
  hand-edited `CREATE TABLE` statement — that does nothing for a database that
  already has the table (`ARCHITECTURE.md` §3).
- **There is a real test suite** (`tests/`, stdlib `unittest`,
  `python -m unittest discover tests`) — run it, and extend it when you touch
  a formula it covers. It is not exhaustive; see `ARCHITECTURE.md` §12 for
  what's still open.

## Before finishing any change

There is no `npm run check` equivalent, so verification here is manual:

1. **A change to `promptmeter/`** — run `python -m unittest discover tests`
   first; it covers the formulas in `ARCHITECTURE.md` §9 (loop cost, window
   reconstruction, capacity solving, segmentation, shrinkage, scope scoring).
   Also confirm the module imports and the app still starts
   (`python -m promptmeter`). If the change touches a formula the suite
   doesn't cover, trace through it by hand against a concrete example, and
   consider adding a test.
2. **A change to `web/`** — start the server and check it in a real browser.
   The `run` skill can launch the app and screenshot the affected screen; use
   it rather than asserting the UI looks right un-viewed.
3. **A change to `shim/statusline.py`** — see `README.md`'s live-meter
   walkthrough and `ARCHITECTURE.md` §7; this one is genuinely hard to verify
   without a live terminal Claude Code session, so say so rather than
   assuming it works.
4. If the change is significant enough to belong in a changelog, add a dated
   entry to `ARCHITECTURE.md` §12 in its existing style, not a silent fix —
   say what was wrong, what changed, and what is still open.

---

## Consult Gemini before you start

Gemini is the second opinion on this project, reached through the **Antigravity
CLI** (`agy`) — the standalone `gemini` CLI can no longer sign in with a Google
account. **Any task with a design decision in it gets referenced with Gemini
before implementation begins** — a feature, a refactor, a formula or schema
change, a bug whose cause is not yet known, or anything touching more than one
module.

Two ways, depending on size:

- **A whole task** → hand it to the `gemini-planner` subagent. It gathers
  context, asks Gemini for a plan, checks the answer against this repo, and
  returns a plan with the disagreements marked.
- **A single question** → the `gemini-call` skill, or directly:

  ```bash
  node .claude/scripts/gemini.mjs --dirs promptmeter "your question"
  ```

  Point `--dirs` at whichever real directories the question is about —
  `promptmeter` (the engine), `web` (the interface), or `shim` (the live
  meter) — not a placeholder.

**Prefer `--dirs` over reading files into context.** `agy` is an agent with its
own file tools, so `--dirs` tells the wrapper where to read from rather than
shipping file contents through the conversation. Its context is large and mine
is the scarce resource.

The wrapper is read-only by intent — it never passes
`--dangerously-skip-permissions`, so Gemini can read to answer but cannot change
anything. It advises; I act.

**Report what it said, then judge it.** Say what Gemini recommended, where I
agree, and where I do not and why. Never relay its answer as settled, and never
adopt it silently. It has not read this conversation and does not know this
repo's specifics as well as `ARCHITECTURE.md` does — **where the two conflict,
this repo's own docs win.** Gemini also has no special knowledge
that this project is stdlib-only Python with a vanilla-JS frontend; if it
proposes a dependency, a build step, or a framework, that is very likely wrong
for this repo and should be flagged, not adopted.

**Skip it, and say so, for:** single-line edits, typos, renames, running a
command, reading a file, anything already specified precisely enough to just
do, and follow-ups inside a task Gemini has already reviewed.

**Exit code 2 is a setup problem** — nothing installed, or authentication
failed. Stop and say so rather than proceeding as though the review happened;
retrying will not fix it. A quota error is exit 1: say so in one line and carry
on alone.
