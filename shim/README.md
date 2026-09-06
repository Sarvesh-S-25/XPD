# `shim/` — the live meter bridge

One file, 78 lines. Optional. This is the only piece of PromptMeter that runs
*inside* Claude Code rather than beside it.

| File | What it is |
|---|---|
| `statusline.py` | A status-line script for Claude Code. Prints a compact usage bar, and posts the same data to PromptMeter. |

---

## Why it exists

Claude Code's status line is the **only supported place** where your real plan
percentages are handed to a local program. Everything else — the desktop app's
usage dialog, `/usage` — renders them and throws them away. There is no cached
copy on disk and no CLI command that prints them.

So if you want live limits rather than estimates, this is the mechanism.

## What Claude Code sends it

On every render, Claude Code pipes a JSON blob to this script on stdin:

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

`rate_limits` appears **only for Claude Pro and Max subscribers**, and only after
the first response in a session. On an API key it is absent.

## What it does

1. Reads stdin.
2. POSTs it verbatim to `http://127.0.0.1:7777/api/ingest`, with a **0.4 second
   timeout**, ignoring every possible failure.
3. Prints a one-line bar: `Sonnet  5h [###.......] 31%  wk [#.........] 12%`

## The rules it obeys

**It must never block your session.** The timeout is short and every network
error is swallowed. If PromptMeter is not running, the bar still prints and
Claude Code carries on. A status line that hangs would make Claude Code feel
broken, which is a far worse failure than a missing meter.

**It must never crash.** Malformed JSON, missing keys, absent `rate_limits` —
all produce a printed line rather than a traceback.

**It runs on every render**, which is often. Nothing expensive belongs here.

## Environment variables

| Variable | Effect |
|---|---|
| `PROMPTMETER_PORT` | Post somewhere other than 7777 |
| `PROMPTMETER_SELFTEST` | Print the bar but post nothing — used by **Test it** on the Setup page so a check never writes fake data |

## Installing it

Do not hand-edit. Press **Write the setting** on the Setup page, or run
`python -m promptmeter --connect`. Both work out the absolute paths for your
machine, back up your existing `settings.json`, and merge in a single key while
leaving everything else alone. `--disconnect` undoes it.

The setting it writes:

```json
{ "statusLine": { "type": "command", "command": "\"<python>\" \"<this file>\"" } }
```

**It does nothing in the Claude desktop app**, which has no status line to
attach to. Terminal Claude Code only.
