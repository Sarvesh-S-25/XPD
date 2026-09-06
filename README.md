# PromptMeter

**Know what a prompt will cost you before you send it — and whether it fits in what's left of your plan.**

A small local app for anyone working on a Claude Pro or Max plan. It estimates
what a prompt will cost, scores how likely it is to hit a wall, splits it into
steps when splitting actually helps, tracks each project's progress, and learns
your real usage as you go.

Everything runs on your own machine. **No API key. No network calls. No account.**
One SQLite file in your home folder is the whole database.

This file is the runbook — start it, connect it, use it. For how it works
internally, the formulas, the data files you can tune, the frontend
conventions, and the project's audit history, see **[`ARCHITECTURE.md`](ARCHITECTURE.md)**.

---

## What is in this folder

```
p-deliverible/
├── start.bat            ← double-click this to run the app        (Windows)
├── start.sh             ← same thing                              (macOS / Linux)
├── connect-meter.bat    ← optional: connect the live usage meter  (Windows)
├── .env.example         ← optional: a plan-drafting provider's API key
│
├── promptmeter/         THE ENGINE — all the logic, pure Python
│   └── data/              prices, plan sizes, task priors — edit these
│
├── web/                 THE INTERFACE — 3 files, no build step
├── shim/                THE LIVE METER — optional, 1 file
├── tests/                stdlib unittest suite
│
├── README.md            you are here — how to run and use it
└── ARCHITECTURE.md      everything else: internals, data, conventions, history
```

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
a few days of plausible history in it. **Clear sample data** on the Setup page
removes just that; **Erase everything** clears real data too.

---

## The whole thing, start to finish

Nine steps. Only the first two are required.

### 1 — Run it
Double-click `start.bat`. The browser opens at `127.0.0.1:7777`. Nothing to
install beyond Python.

### 2 — Tell it your plan
**Setup → How big is your window? → Your plan.** One dropdown. This is what
turns "dollars of work" into "percent of your limit."

### 3 — Sync one reading *(recommended, 10 seconds)*
**Windows → Sync from Claude.** Open Claude's own usage view — the ring beside
the model picker in the desktop app, or `/usage` in the terminal — and copy the
four values across. PromptMeter divides them into the spend it has already
recorded and solves for your window's true size. After this the basis reads
**measured**, and you do not have to do it again.

### 4 — Connect the live meter *(optional, terminal only)*
**Setup → Live status line → Write the setting → Test it**, then restart
terminal Claude Code. Now the percentages update continuously on their own. See
**"Connecting the live meter, step by step"** below for the full walkthrough.
This does nothing in the desktop app, which has no status line.

### 5 — Plan a prompt
**Plan a prompt.** Paste what you were about to send, pick a model and reasoning
effort, press **Estimate**. You get a verdict in plain English, a token count
and a cost, and — if it is too big — a split into steps. Percent-of-window is
one click away, not the headline.

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
| **Plan a prompt** | What will this cost in tokens and dollars, how risky is it, does it need splitting? |
| **Projects** | What am I working on, how far along is each one, what has it cost? |
| **History** | What has PromptMeter learned about my usage, and were its estimates right? |
| **Setup** | What tracking can see, calibration, connecting a provider, and the live status line. |

A persistent bar above every screen — surface, plan, model, reasoning effort —
is set once and applies to every new plan, so you don't re-pick it per prompt.

---

## Connecting the live meter, step by step (Windows)

When this is done, PromptMeter shows the same numbers as Claude's own usage
dialog, updating by itself, covering **all** your usage including Cowork. Total
time: about five minutes. No Node.js, no API key, no payment. This step is
entirely optional — automatic tracking (step 8, above) already works without it.

### 1. Open PowerShell
Press the **Windows key**, type `powershell`, press **Enter**. A blue window
opens with a prompt like `PS C:\Users\you>`. You do **not** need to run as
Administrator.

> If your prompt says `C:\Users\you>` **without** the `PS`, you're in CMD, not
> PowerShell. Close it and search for "PowerShell" instead.

### 2. Install Claude Code
```powershell
irm https://claude.ai/install.ps1 | iex
```
Wait for it to finish. It downloads a single program — no Node.js needed.

### 3. Close PowerShell and open it again
Not optional — Windows only picks up the new `claude` command in a fresh window.
Then check it worked: `claude --version` should print something like
`2.1.233 (Claude Code)`.

### 4. Log in
Type `claude`. First run asks for a colour theme (pick any), then a login
method — choose **"Claude account with subscription"**. Your browser opens;
sign in and approve. Go back to PowerShell — it will say you're logged in.

### 5. Prove the numbers are there
Still inside Claude Code, type `/usage`. You should see **Current session** and
**Weekly** bars — the same ones as the app's usage dialog. This costs nothing.
Seeing them here means PromptMeter can get them too. Leave with `/exit`.

### 6. Connect PromptMeter
Make sure PromptMeter is running (`start.bat` if not). Go to **Setup → Live
status line**, click **Write the setting**, then **Test it** — you want the
green "It works" box. If Test it fails saying Python wasn't found, install
Python from [python.org/downloads](https://www.python.org/downloads/) and tick
**"Add python.exe to PATH"** on the installer's first screen.

### 7. Start Claude Code again and send one message
```powershell
claude
```
Type anything — `hi` will do. A usage bar appears at the bottom of the Claude
Code window, and PromptMeter's Windows page turns green with your real
percentages. The one message is needed because Claude Code only receives the
limit numbers after its first reply in a session.

### What you have now
Live limits matching Claude's own dialog, updating continuously, covering
Cowork/browser/desktop/terminal (one shared pool), at no cost — the status line
is a local script making no API calls.

**Day to day:** whenever a terminal session is open, PromptMeter updates on its
own. With no session running, the numbers keep ticking from the last reading —
countdowns run down, bars reset themselves at a window rollover. To re-sync
immediately after heavy Cowork/browser use, open a terminal session for a
moment, or use **Sync from Claude** on the Windows page.

**Undo:** the **Undo** button on the Setup page, or `python -m promptmeter
--disconnect`. Your other settings are untouched.

### If something goes wrong

| What you see | What to do |
|---|---|
| `irm ... is not recognized` | You're in CMD, not PowerShell. Open PowerShell. |
| `claude is not recognized` | Close and reopen PowerShell. If it persists, restart the PC. |
| Test it says Python not found | Reinstall Python, ticking **Add python.exe to PATH**. |
| No bar at the bottom of Claude Code | You started `claude` before writing the setting. `/exit` and start it again. |
| App still yellow after a message | Press **Check again** on Setup. Confirm `/usage` shows bars inside Claude Code. |

Run `claude doctor` in PowerShell for a health check of the Claude Code install
itself.

---

## Command reference

```
python -m promptmeter                    # start on the default port (7777)
python -m promptmeter --port 7788        # start on a different port
python -m promptmeter --no-browser       # start without opening a browser tab
python -m promptmeter --connect          # write the status-line setting (add --force to replace an existing one)
python -m promptmeter --disconnect       # undo the status-line setting
```

Windows users can double-click `connect-meter.bat` instead of `--connect`.

Set `PROMPTMETER_HOME` to move the database somewhere other than
`~/.promptmeter`.

**Optional: a plan-drafting provider's key.** Copy `.env.example` to `.env`
and fill in whichever key you want (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`GEMINI_API_KEY`) — this is only used if you also pick that provider on
**Setup → Connect a provider**, and only for having it draft your prompt's
step list; every model's cost/token estimate works with no key at all. A key
pasted into the Setup page itself always takes priority over `.env`. `.env`
is git-ignored; never commit it.

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

For everything else — how the estimator actually works, the data files you can
tune, the frontend's design system, and the project's known issues — read
**[`ARCHITECTURE.md`](ARCHITECTURE.md)**.
