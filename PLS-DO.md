# pls do — audit findings

An aggressive pass over the whole project: purpose, functionality, UI, and the
things a stranger would trip on. Written by running the code hard, not by
reading it.

**Fixed in this pass: 6. Still open: 15**, ranked by what actually matters.

**September 2026 pass: 8 more fixed** (E1, E6, S1, S2, A5, P2, plus a stale-data
fix and a security gap this pass found that the first pass didn't scope). See
"Fixed in the September 2026 pass," below, and the updated "Still open" list —
several items are downgraded from "open" to "genuinely improved but honestly
still not the textbook version."

---

## Answers to the specific things you asked

**Jupyter?** No, and none needed. Nothing here is exploratory analysis — the maths
is closed-form arithmetic that runs in the app itself. A notebook would be a
place for bugs to hide outside the product.

**Installation?** Python 3.9+ and nothing else. No `pip install`, no Node, no
build step, no database server. `start.bat` is the whole thing.

**A learning model?** No ML library anywhere. "Learning" is empirical-Bayes
shrinkage — six lines of arithmetic over your own history. The only optional
model is the planner (Ollama / API key), and it is never asked for a number.

**What predicts the cost?** A closed-form loop formula over four inputs: prompt
size (measured), turns and output length (predicted from priors that shrink
toward your data), and context growth (measured). No model in that path.

---

## Fixed in this pass

### 1. Model routing silently switched vendors — HIGH
`route_model` mapped every downgrade to a hardcoded Claude model. Pick Gemini
3.1 Pro, let the planner send a cheap step to "haiku", and you'd get billed for
Claude with Claude's 200k window and Claude's cache rules. Every multi-vendor
estimate with a split was wrong.

Now routes to the cheapest model of the target tier **from the same vendor**:
Gemini Pro → Gemini Flash, GPT Sol → GPT Terra, and a local model stays local.

### 2. Two ledgers that never met — HIGH
The watcher recorded every turn automatically. Project spend read only
iterations you typed in by hand. So you could do a whole project, have 471 turns
captured on disk, and see **$0.00 spent, 0% progress**.

Turns are now attributed to whichever step was running when they were recorded —
and where step windows overlap, to the one that started most recently, so
nothing double-counts. Progress, spend and the step cards all use whichever
ledger saw more. **Manual logging is now optional rather than load-bearing.**

### 3. Learning never saw the automatic data — HIGH
`observations()` joined on `iterations` only. The app could watch fifty real runs
and still report *"prior only"* forever. It now reads both ledgers, and context
growth is measured from the watcher's turns first. On this machine it went from
`prior only` to `learning` with a measured growth of 1,130 tokens/turn.

### 4. The numbers were unreadable — HIGH *(the one you called out)*
"91% to 421%" and "risk 92%" are precise and useless unless you already know how
Anthropic meters a plan. Added a plain-language layer that leads the estimate:

> **Do not send this as one prompt.** It will almost certainly stop before it is
> finished. Split it, or cap the turns and give it a way to know when it is done.
>
> **How big this is:** About a tenth of a session · **If it goes badly:** More
> than one full session

Percentages, dollars and risk are still there, one click away under *"The exact
numbers."* Dollars are relabelled **"work value (not a bill on a plan)"**,
because on a subscription you never pay them.

### 5. The verdict contradicted the risk — HIGH *(a bug in my own new code)*
First version of the plain layer said **"Just send it"** next to a 48% chance of
being cut off, because size and risk were computed independently. Risk now gates
the verdict: critical never reads as safe.

### 6. Split banner was green when it meant trouble — LOW
"This looks too big for one run" rendered in the success colour.

---

## Fixed in the September 2026 pass

An enterprise-hardening pass, planned against this file, checked against Gemini,
and verified by actually running the app end to end (not just reading the diff).

### E1. There are no tests — HIGH → fixed
`tests/`, stdlib `unittest` only (`python -m unittest discover tests`, 65 tests).
Covers `pricing.loop_budget`, the new calibration function, `meter`'s window
reconstruction and capacity solve, `segmenter`'s DAG layering, `dpapi`'s
round-trip, and the migration runner's idempotency. No `pytest` — that would
have been a new dependency, contradicting the project's own zero-install rule.

### E6. No schema migrations — fixed
`db.py` gained a `schema_version` table and a small linear migration runner.
Every schema change below went through it rather than hand-editing a
`CREATE TABLE` statement, which would have done nothing for an existing
database.

### S1. Deliverable checks run arbitrary shell commands — MED → fixed, and widened
The original scope was too narrow: while implementing the fix, `POST
/api/reset` turned out to wipe the entire database — every project, step,
iteration and setting — with zero confirmation and the same missing
authentication as the shell-oracle path. Fixed together: a per-process CSRF
token required on every mutating route, a Host-header check (closes DNS
rebinding, the same bug class that has hit Ollama and Jupyter), an explicit
`{"confirm": "RESET_ALL_DATA"}` body requirement on reset, and a one-time UI
approval before a step's `command`/`tests` check is allowed to run at all —
changing the oracle's kind or spec re-arms that approval rather than carrying
it over to a different command.

### S2. API keys sit in plaintext — MED → fixed on Windows, honestly not elsewhere
`dpapi.py`: Windows DPAPI via `ctypes` (stdlib only, no `pip install keyring`),
user-scoped, never machine-scoped. Verified with a real round-trip on this
machine. macOS/Linux have no stdlib OS-keychain equivalent, so keys there stay
plaintext — the Setup page's `key_storage` field says so per key rather than
implying uniform protection. A key that can't be decrypted (database moved to
another machine or user) reports as `"unreadable"` rather than crashing.

### A5. `model_table()` on History still reads iterations only → fixed
Now unions `iterations` and the watcher's `turns` the same way `observations()`
already did — a step driven entirely by the automatic watcher, with nothing
manually logged, now shows up in the per-model cost table instead of $0.

### P2. Demo data is indistinguishable from real data → fixed
`is_demo` on every table `demo.py` writes; a "sample" tag wherever a demo
project appears in the UI; excluded from `learning.observations()`,
`accuracy()`, `model_table()`, and the dashboard/Projects/History "spent"
totals. A new "Clear sample data" action removes only tagged rows, leaving
real projects untouched — separate from "Erase everything."

### A1 / A3 — improved, not fully solved
**A1** (band not calibrated): `learning.calibration_factor()` blends the
model's own P95:P50 spread with your own predicted-vs-actual history once a
task class has 8+ completed steps, using the same shrinkage weight the rest of
the learning system already uses. This is an honest, labelled approximation —
not textbook split-conformal prediction, which needs ~19 samples for a
guaranteed-coverage interval and this app sees dozens per class, not
thousands. The UI says "calibrated from your own N past runs," never "95%
means 95%."
**A3** (`thinking_share` assumed): now learned per model from real transcripts
(character share of thinking-block length) once 5+ thinking-bearing turns are
observed for that model — genuinely better than a hardcoded guess, but still
one number per model rather than per (model, effort), because Claude Code's
transcripts never record which effort level produced a turn. That's an
observability limit, not a design choice.

### A caught mid-implementation, not on this list before
Loading sample data didn't invalidate the cached dashboard/Projects totals, so
the Projects screen showed "$0.00 spent" and "0 projects" for several seconds
after seeding three sample projects — found by actually clicking through the
running app rather than trusting the diff. Fixed by nulling the cache the same
way every other mutating action already did.

---

## Still open — ranked

### Security

Both items formerly here (S1 shell-oracle/CSRF, S2 plaintext keys) are fixed —
see "Fixed in the September 2026 pass," above.

### Accuracy

**A1 / A3** — see "Fixed in the September 2026 pass": genuinely improved, but
each has an honest remaining limit (small-sample approximation; one number per
model instead of per effort level).

**A2. The priors are my estimates, not measurements**
`build_app = 35/120 turns` came from judgement, not a dataset. Everything
downstream inherits that. Shrinkage fixes it as you log runs, but the first
dozen estimates are only as good as those seven rows in `priors.json`.

**A4. Cowork and browser usage stay invisible**
Structural, not fixable locally — they leave nothing on disk. A synced reading
absorbs them into the total; attribution is impossible.

### Product / purpose

**P1. There is no first run**
A new user lands on "Windows" with hatched empty bars and no idea what the app
is for. There is no one-screen explanation of the 5-hour window, no guided
setup, no "start here."
*Do:* a first-run screen — what this is, pick your plan, sync a reading, done.

**P3. Step status is still manual**
Turns attribute automatically now, but you must click Start/Done for a step to
have a window to attribute *to*. If you never click Start, nothing attaches.
*Do:* infer a step as started on its first matching turn, or a "start next step"
button that is one click from the top.

**P4. The app plans work it cannot run**
It tells you what to send and tracks what happened, but you paste prompts
yourself. That is a deliberate choice (no API key needed), but the copy never
says so plainly, and a new user may expect it to execute.

### Engineering

**E2. Never run on Windows — partially verified**
The September 2026 pass ran the app directly (`python -m promptmeter`) and
drove the UI in a real browser on Windows, end to end — model catalogue,
selection bar, sample-data seeding, the CSRF/approval security flow, and both
themes all confirmed working live, not just read. `start.bat` itself, and the
live status-line install flow specifically, are still unverified by an actual
run — those need someone to click through `start.bat` and the terminal
status-line steps on this machine to close this out fully.

**E3. No packaging**
No `pyproject.toml`, no `pipx install`, no single-file exe. Distribution is
"copy a folder."

**E4. Dead code — `meter._fit()`**
The least-squares α fit runs on every status-line ingest and no longer feeds
anything; capacity now comes from direct division. Harmless, misleading to read.

**E5. Greedy scheduler is not optimal**
Fine for "fill the window before it evaporates", suboptimal on wide graphs with
mixed step sizes. OR-Tools CP-SAT would fix it at the cost of the
zero-dependency promise.

---

## What I would do next, in order

1. **P1 — first-run screen.** Now the single biggest gap between "works" and
   "usable by someone who is not you" — everything ranked above it last pass
   (E1 tests, S1 security, most of A1) is done.
2. **P3 — auto-start steps.** Removes the last piece of mandatory manual work.
3. **E2 — close out Windows verification.** Run `start.bat` and the live
   status-line install steps for real, not just `python -m promptmeter`
   directly.
4. **A2 — replace the hand-guessed priors** with real measurements, now that
   the shrinkage/calibration machinery to actually use them exists.

Items **A2, A4, E3, E5** are things to *disclose* rather than fix — they are
inherent to the approach, and the README already names most of them.
