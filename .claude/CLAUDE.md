# PromptMeter — project instructions

PromptMeter is a local app that estimates what a Claude prompt will cost, scores
the chance it hits a wall mid-run, splits it when splitting helps, tracks project
progress against real usage, and learns your own history over time. It is
**Python standard library only** — no `pip install`, no Node, no build step, no
account, no network calls. One SQLite file in `~/.promptmeter/` is the whole
database. `web/` is three files of vanilla JS/CSS/HTML served directly by the
stdlib HTTP server in `promptmeter/server.py` — no framework, no bundler.

Read `README.md` before touching product behaviour — it explains the nine-step
user flow, the five screens, and every formula in plain language. Read
`ARCHITECTURE.md` before touching a module — it goes file by file, names the
sixteen modules, states the design rules explicitly (Section 8), and tables the
named algorithms (Section 6). Read `PLS-DO.md` before starting new work — it is
the live backlog: what was fixed in the last audit pass, and fifteen open items
ranked by severity across security, accuracy, product and engineering. Check a
task against it before assuming it is new: if what's being asked matches an open
item (e.g. E1 "there are no tests", S1 "oracle_spec runs shell", A1 "the band
isn't calibrated"), say which item it is and work from that description rather
than rediscovering the problem.

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
  records of the same spend. Anything that computes progress, spend or learning
  must read both — reading iterations only was a real, fixed bug (PLS-DO #2/#3)
  and one place (`model_table()` on History, PLS-DO A5) still has it open. Don't
  add a second occurrence.
- **`web/app.js` — every interpolation goes through `esc()`.** Views build HTML
  by string concatenation; this is the only thing between a pasted prompt and an
  XSS bug. No exceptions. Colours come from CSS custom properties
  (`var(--series-1)`), never hex literals — see `web/README.md`.
- **A meter with no reading renders hatched/`.unknown`, never as an empty bar.**
  Empty and unknown must never look alike; this was a real bug.
- **Model routing must stay within the same vendor** when picking a cheaper tier
  (`route_model` in `promptmeter/pricing.py` / `estimator.py`) — routing a
  downgraded Gemini or GPT model to a hardcoded Claude model was the first bug
  fixed in the last audit pass.
- **`oracles.py`'s `command`/`tests` checks run with `shell=True`** against a
  free-text field the browser can POST to. This is a known, open gap (PLS-DO
  S1) — don't casually extend what that field can do without also addressing
  the missing token/confirmation guard.
- **API keys live in plaintext in SQLite** (PLS-DO S2), also known and open.
  Don't add more secrets to that table as if it were safe storage; say so if a
  task would.
- **Schema changes need a hand-written `ALTER`.** Tables are `CREATE TABLE IF
  NOT EXISTS` only (PLS-DO E6) — there is no migration system, so adding a
  column blindly loses existing users' data on upgrade.
- **There is no test suite yet** (PLS-DO E1, ranked as the top priority open
  item). Don't claim "tests pass" — there aren't any. See below for what to run
  instead.

## Before finishing any change

There is no `npm run check` equivalent, so verification here is manual:

1. **A change to `promptmeter/`** — at minimum, confirm the module still
   imports and the app still starts: `python -m promptmeter` (or
   `python -c "import promptmeter.<module>"` for a quick syntax/import check).
   If the change touches a formula named in `ARCHITECTURE.md` §6 (loop cost,
   window reconstruction, capacity solving, segmentation, shrinkage, scope
   scoring), trace through it by hand against a concrete example before calling
   it done — there is nothing else catching a regression.
2. **A change to `web/`** — start the server and check it in a real browser.
   The `run` skill can launch the app and screenshot the affected screen; use
   it rather than asserting the UI looks right un-viewed.
3. **A change to `shim/statusline.py`** — see `TERMINAL-SETUP.md` and
   `shim/README.md`; this one is genuinely hard to verify without a live
   terminal Claude Code session, so say so rather than assuming it works.
4. If the change is significant enough to belong in a changelog, it belongs as
   a new dated entry in `PLS-DO.md`'s style, not a silent fix — say what was
   wrong, what changed, and what is still open.

If asked to write the test suite (PLS-DO E1), that is real, wanted work — not a
detour.

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
repo's specifics as well as `ARCHITECTURE.md` and `PLS-DO.md` do — **where the
two conflict, this repo's own docs win.** Gemini also has no special knowledge
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
