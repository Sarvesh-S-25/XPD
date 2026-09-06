# PromptMeter

**Know what a prompt will cost you before you send it — and whether it fits in what's left of your plan.**

A small local app for anyone working on a Claude Pro or Max plan. It estimates
what a prompt will cost, scores how likely it is to hit a wall, splits it into
steps when splitting actually helps, tracks each project's progress, and learns
your real usage as you go.

Everything runs on your own machine. **No API key. No network calls. No account.**
One SQLite file in your home folder is the whole database.

---

## What is in this folder

```
p-deliverible/
├── start.bat            ← double-click this to run the app        (Windows)
├── start.sh             ← same thing                              (macOS / Linux)
├── connect-meter.bat    ← optional: connect the live usage meter  (Windows)
│
├── promptmeter/         THE ENGINE — all the logic, pure Python
│   ├── README.md          what every file in here does
│   └── data/              prices, plan sizes, task priors — edit these
│       └── README.md      what each table means and how to tune it
│
├── web/                 THE INTERFACE — 3 files, no build step
│   └── README.md          how the screens are built
│
├── shim/                THE LIVE METER — optional, 1 file
│   └── README.md          how it hooks into Claude Code
│
├── README.md            you are here — what it does and how to use it
├── ARCHITECTURE.md      how it works internally: algorithms and formulas
├── TERMINAL-SETUP.md    step-by-step guide to the live meter
└── PLS-DO.md            known faults, what is fixed, what still needs doing
```

Each folder has its own `README.md` explaining what it contains. Start with this
file, then `promptmeter/README.md` if you want to read the code.

**Your data is not in this folder.** It lives in `~/.promptmeter/` (on Windows,
`C:\Users\you\.promptmeter\`) as a single SQLite file. Delete that folder to
reset everything; this one only holds the program.

---

## Start it

You need **Python 3.9 or newer** and nothing else — no `pip install`, no Node,
no build step. The whole app is standard library.

**Windows** — double-click `start.bat`, or:

```
python -m promptmeter
```

**macOS / Linux**

```
./start.sh
```

Your browser opens at <http://127.0.0.1:7777>. To stop it, press Ctrl+C.

First time in, click **Load sample data** on the Windows page to see the app with
a few days of plausible history in it. **Erase everything** on the Setup page
clears it again.

---

## The whole thing, start to finish

Nine steps. Only the first two are required.

### 1 — Run it
Double-click `start.bat`. The browser opens at `127.0.0.1:7777`. Nothing to
install beyond Python.

### 2 — Tell it your plan
**Setup → How big is your window? → Your plan.** One dropdown. This is what
turns "dollars of work" into "percent of your limit."

### 3 — Sync one reading  *(recommended, 10 seconds)*
**Windows → Sync from Claude.** Open Claude's own usage view — the ring beside
the model picker in the desktop app, or `/usage` in the terminal — and copy the
four values across. PromptMeter divides them into the spend it has already
recorded and solves for your window's true size. After this the basis reads
**measured**, and you do not have to do it again.

### 4 — Connect the live meter  *(optional, terminal only)*
**Setup → Live status line → Write the setting → Test it**, then restart
terminal Claude Code. Now the percentages update continuously on their own. See
`TERMINAL-SETUP.md` for the full walkthrough. This does nothing in the desktop
app, which has no status line.

### 5 — Plan a prompt
**Plan a prompt.** Paste what you were about to send, pick a model and reasoning
effort, press **Estimate**. You get a verdict in plain English, a size in
sessions, a token budget, and — if it is too big — a split into steps.

### 6 — Create the project
**Create project** turns the plan into steps you can track, each with its own
budget, model, iteration cap and a check for when it is done.

### 7 — Do the work
Open a step, press **Copy prompt**, paste it into Claude. On a high-risk step the
prompt already contains a summary of what earlier passes finished, so an
interrupted run resumes instead of starting over.

### 8 — Watch it fill in
Usage tracks itself from Claude Code's session files — terminal and desktop Code
tab both. Press **Start** on a step and the turns you spend get attributed to it
automatically. Logging iterations by hand is optional; it only adds the summary
text.

### 9 — Check it against reality
**History** shows what each model actually cost you, what the app has learned
about each kind of task, and whether its estimates landed inside their band. That
last table is the honest scoreboard — if the estimates are wrong, this is where
you find out.

---

## The five screens

| Screen | What it answers |
|---|---|
| **Windows** | How much of my 5-hour and weekly limits is left, and when do I run out at this rate? |
| **Plan a prompt** | What will this cost, how risky is it, does it need splitting? |
| **Projects** | What am I working on, how far along is each one, what has it cost? |
| **History** | What has PromptMeter learned about my usage, and were its estimates right? |
| **Setup** | What tracking can see, calibration, and things worth knowing about plan limits. |

---

## How it works

### The windows

A Pro or Max plan has two limits: a **rolling 5-hour window** and a **7-day weekly
window**. Both are shared across Claude chat, Claude Code and Cowork — same pool,
three surfaces. Opus has a separate limit on top of those.

Anthropic does not publish the numbers behind them. Max 5x is documented as "five
times more usage per session than Pro" — there is no token figure anywhere. So
PromptMeter does not try to guess them. It reads the **percentages**, which is the
quantity actually enforced, and fits everything else from those.

### Tracking your usage — automatic, both surfaces

**There is nothing to set up.** Claude Code writes every turn it runs to a session
file under `~/.claude/projects/`, including the exact token counts per message.
PromptMeter tails those files. That covers the **terminal** and the **desktop
app** equally, needs no settings change, and refreshes every 20 seconds while the
app is open.

Open the app, use Claude however you normally do, and the Windows page fills in.
The Setup page shows how many session files and turns it can see, and has a
**Check now** button if you don't want to wait for the next sweep.

### One calibration, once

Tracking gives exact dollars of work. Converting that into "percent of your
window" needs your window's size — and Anthropic publishes no such number for any
plan. Two ways to supply it:

1. **Pick your plan** (Pro / Max 5x / Max 20x) on the Setup page for a starting
   estimate. One click.
2. **Calibrate exactly, once.** Read the two percentages from wherever you use
   Claude — the usage ring next to the model picker in the desktop app, or
   `/usage` in the terminal — and press **Calibrate**. PromptMeter divides them
   into the spend it has already recorded and solves for your true window size.
   You never do it again.

After calibration the basis on the Windows page reads **measured** instead of
**plan estimate**, and every later percentage is computed automatically.

### Live status line (optional, terminal only)

The terminal version of Claude Code can hand PromptMeter the exact percentages
Anthropic enforces on, continuously — and each one re-calibrates the window size
for free. This is a precision upgrade on top of automatic tracking, not a
requirement.

**It does nothing in the desktop app**, which has no status line to attach to.

On the Setup page press **Write the setting**, then **Test it**, then restart the
terminal Claude Code. It works out the right paths for your machine, backs up your
existing `settings.json`, and merges in one key while preserving everything else.
There is an **Undo** button. From a console instead:

```
python -m promptmeter --connect          # add --force to replace an existing one
python -m promptmeter --disconnect       # undo
```

Windows users can double-click `connect-meter.bat`.

If you edit the file by hand, use **Show me the file instead** — it prints the
complete file with your real paths already filled in. Copy that; never paste
anything containing a placeholder like `<this folder>`.

### Measured plan capacity

Once samples come in, PromptMeter fits one number — percent of window consumed per
dollar of list-price work — and inverts it. That gives you your plan's real
capacity in dollars-equivalent per window: the limit that isn't published,
derived from your own usage. It's labelled *default* / *learning* / *measured* so
you always know how much to trust it.

### Models — Claude, GPT and Gemini

The catalogue in `promptmeter/data/models.json` covers Anthropic, OpenAI and
Google, plus a free "local model" entry. Each model carries what the estimator
actually needs, not just a price:

| Field | Why it matters |
|---|---|
| `in` / `out` | list price per million tokens |
| `cache_read`, `cache_write_*` | Anthropic charges a premium to write cache; OpenAI and Google cache automatically |
| `context` | 200k to 1.05M depending on model — drives the compaction estimate |
| `max_output` | ceiling on one reply; long answers get truncated or continued |
| `thinking_share` | how much of the output is reasoning, per effort level |
| `long_context` | Gemini Pro switches to a higher rate above 200k tokens |

Prices were verified against vendor pricing pages in **August 2026**. They
change — the file is plain JSON, edit it.

### Token budget — where the tokens actually go

Every estimate now shows the full accounting, typical and worst case: input sent
across all turns, how much of that is conversation re-sent, output generated,
how much of the output is **thinking**, peak conversation size against the
model's context window, and whether the run would need **compaction**.

Compaction is a real cost, not just a limit. When the conversation outgrows the
window the agent must summarise and continue — a large read plus a summary
write. The estimator counts it:

```
peak = base + growth × (turns − 1) + output_per_turn
if peak > window:
    compactions = ceil((peak − window) / (window × 0.5))
    extra = compactions × (window × 0.70  +  summary)
```

### Reasoning effort

Thinking tokens are billed as output tokens by all three vendors. The effort
selector (`none` → `max`) scales the output prediction, because thinking is
produced *in addition to* the visible answer rather than carved out of it:

```
total = visible / (1 − thinking_share)
```

Priors were learned at the default effort, so `medium` is 1.0× and `max` works
out around 3× the output tokens — and the cost moves with it.

### Estimating a prompt

Input size is measured directly. Output length and turn count are genuinely
uncertain, so PromptMeter predicts a **band** (typical and worst case), never a
single number.

The thing that actually blows budgets is neither: an agent re-sends the whole
conversation on every turn, so input cost grows **quadratically in turn count**. A
"20k prompt" that runs 30 turns is a $10 request, not a $0.13 one. Turn count is
the variable the estimator predicts hardest.

### Risk

Risk here has a real definition: **the probability this run hits a wall** — the
tail of the predicted cost distribution above what's left in your window. It's a
number you can check, and the History screen checks it.

The score names its drivers rather than just showing a colour. In practice the
big four are: no turn cap, shell access, no acceptance criteria, and open-ended
wording ("keep going until it works"). Runaway features are scaled by how much
room the task has to run away in — a two-turn question can't overrun no matter
how loosely it's worded.

### Scope — why a todo app and a Zomato clone differ

The task class says *what kind* of work it is; scope says *how much*. Scope is
counted from the prompt: subsystems named (payments, auth, tracking, admin),
platforms, data entities, listed requirements, third-party integrations, and
whether it names a product to clone. That becomes a multiplier on the turn
prediction, so `build a todo app` lands around 0.5× and a food-delivery app with
payments, tracking, a dashboard and a mobile client lands above 1.2×.

**Optionally, a model can draft the plan instead.** On the Setup page pick
Ollama (local, free, no key), or paste a Claude / OpenAI / Gemini key. Scope is
then derived from the drafted step list and its complexity ratings, and those
steps become the split. The important rule: **the model never sets a price.** It
enumerates the work; PromptMeter costs it with its own numbers, which stay
checkable against your history. Keys are stored in your local database, sent
only to that provider, and never shown back to the page in full.

Leave the method on **Built-in** and nothing changes — no key, no network, no cost.

### Splitting — and when not to

**Splitting a prompt does not save money by itself.** Each step re-sends its own
context. PromptMeter splits only when it helps, and tells you which of three
things is paying for it:

- **less context re-sent per turn** — short steps avoid the quadratic growth (usually the largest)
- **cheaper models on the simple steps** — boilerplate goes to Haiku
- **each step carries only what it touches**

Every line in that table is a separate real estimate, so the numbers reconcile.
If the split comes out *more* expensive, the app says so and tells you the only
two reasons left to do it anyway: it fits inside your window, and a step that
goes wrong costs one step's budget instead of the whole run's.

Set **Splitting → Never split** to run the whole thing as one step.

### Steps, iterations and the step graph

A project is a graph of steps. Each step has a prompt, a model, a budget, an
iteration cap, and a **deliverable check**. Log what each pass did and PromptMeter
tracks progress against the budget and stops you when a guard trips.

The **step graph** shows which prompt produced what, laid out in dependency
order. The dot on each card is the deliverable verdict; the bar is progress.

### Deliverable checks — the loop without a model

The iteration loop stops on a machine-checkable condition, not on a model's
opinion:

| Check | Passes when |
|---|---|
| File exists | the file is there and not empty |
| Command exits 0 | your command succeeds |
| Test suite passes | `pytest` / `npm test` / `go test` returns 0 |
| Valid JSON | the file parses |
| Contains | `path/to/file.txt::pattern` matches |
| No progress | output stopped changing — the loop has converged |
| Manual | you decide |

When you don't name a deliverable, one is derived from the task type — not
invented by a model. Three universal stops are always on: iteration cap, budget
cap, and no-progress.

### High-risk steps resume instead of restarting

On a red or critical step, every summary you log is folded into the next prompt
under *"work already completed — do not redo the items above"*, along with the
iteration cap and the acceptance check. An interrupted run picks up where it
stopped instead of starting over. That's what the **Copy prompt** button gives
you.

### Learning

Every logged iteration feeds back. Task-class predictions start from built-in
priors and shift toward your own numbers as runs accumulate (shrinkage weighting,
so two samples stay near the prior and fifty drive it). The History screen shows
what it has learned per model and per task type, and how often the estimates
landed inside the band.

---

## Worth knowing

- **Unused plan percent evaporates** at every reset; money spent on usage credits
  doesn't. So the scheduler packs each window as full as the safety margin allows
  rather than spreading work evenly.
- **The credits trap.** Prompt cache lifetime is 1 hour on a subscription and
  drops to **5 minutes** once you draw on usage credits — so crossing over quietly
  makes every turn more expensive through cache misses. Set
  `ENABLE_PROMPT_CACHING_1H=1` to keep the hour.
- **Agent teams** use roughly **7× the tokens** in plan mode; each teammate carries
  its own context window.
- **`/clear` costs nothing. `/compact` doesn't** — compacting a large context is
  itself a large request.

---

## Reading the code

Each folder documents itself:

| Folder | Read | For |
|---|---|---|
| `promptmeter/` | [`promptmeter/README.md`](promptmeter/README.md) | Every module, in dependency order |
| `promptmeter/data/` | [`data/README.md`](promptmeter/data/README.md) | The JSON tables you can tune |
| `web/` | [`web/README.md`](web/README.md) | How the screens are built |
| `shim/` | [`shim/README.md`](shim/README.md) | The live-meter bridge |

For the algorithms and formulas behind it all, read
[`ARCHITECTURE.md`](ARCHITECTURE.md). For the known faults and what is still
outstanding, read [`PLS-DO.md`](PLS-DO.md).

**Your data:** `~/.promptmeter/promptmeter.db` (Windows:
`C:\Users\you\.promptmeter\`). One SQLite file. Delete the folder to reset.
Set `PROMPTMETER_HOME` to put it elsewhere.

Run on another port with `python -m promptmeter --port 7788`, and `--no-browser`
to skip opening one.

---

## Accuracy, honestly

- Chat and Cowork usage counts against the same windows and PromptMeter can't
  attribute it — but the percentages it reads already include it, so the ledger
  self-corrects at every reading. The gap shows up as *"usage outside your
  projects"*.
- `/usage` is computed from local history on one machine; other devices and
  claude.ai aren't in it. Same correction applies.
- There's no official offline tokenizer, so input counts are a heuristic that
  calibrates itself against observed usage.
- Capacity fitting assumes metering is roughly proportional to list price. If
  that stops holding, the confidence label and the residuals will show it.
