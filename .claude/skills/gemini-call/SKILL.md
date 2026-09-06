---
name: gemini
description: Delegate a single question — reading, research, planning or code review — to Gemini via .claude/scripts/gemini.mjs instead of doing it yourself. Use this whenever answering would need more than about three files read, tracing a call path across promptmeter/web/shim, finding every usage of a symbol, or reviewing a set of files. Reach for it BEFORE broad exploration, not after — even when Gemini is not mentioned. For a whole task (not a single question), use the gemini-planner subagent instead.
when_to_use: Triggers include "how does X work", "where is X used", "what would break if", "review these files", or any moment you are about to read a fourth file to answer one question.
argument-hint: "[question]"
arguments: [question]
allowed-tools: Bash(node .claude/scripts/gemini.mjs *)
---

# Gemini delegation

Gemini reads; you edit. Its context window is larger than yours and reading the
codebase is its job. Do not spend your own context on exploration you can
delegate.

This project's only Gemini entry point is `.claude/scripts/gemini.mjs`, run
through the Antigravity CLI (`agy`) — see `.claude/README.md` for the full
mechanism, and `AGENTS`-style design rules for this repo live in
`ARCHITECTURE.md` §8 and `PLS-DO.md`, not in any `.agent/` directory (there
isn't one).

## Dispatch

```bash
node .claude/scripts/gemini.mjs --dirs <dirs> "$question"
```

`<dirs>` should be the real directories the question is about —
`promptmeter` (the engine), `web` (the interface), `shim` (the live
status-line meter) — comma-separated if more than one. The wrapper reads those
files itself and embeds them in the prompt; it does not let Gemini go exploring
on its own, so naming the right directory is the whole game.

Other flags, from `node .claude/scripts/gemini.mjs --help`:

| Flag | |
|---|---|
| `--dry-context` | print exactly what would be sent, then stop — free, no API call. Use this first if unsure the directory choice is right. |
| `--effort low\|medium\|high` | quicker/cheaper vs. more thorough. Default `high`. |
| `--max-kb <n>` | raise the 250KB payload cap for a larger `--dirs` sweep |
| `--timeout <seconds>` | default 600 |
| `--raw` | full JSON response instead of just the answer |

## Writing the question

- Name exact paths: `promptmeter/planner.py and promptmeter/segmenter.py`, not
  "the planning code."
- One question per call. Two unrelated asks gets a worse answer to both.
- State the shape you want back if you need something other than prose — e.g.
  "numbered plan with files touched," or "file:line findings only."
- Phrase it as a question, not an instruction to act — Gemini here only reads
  and answers; it has no write access and nothing it says changes any file
  unless you act on it afterward.

Good: `--dirs promptmeter "read planner.py and segmenter.py. every place a
step's window can overlap another step's — file:line, and whether the overlap
is resolved by 'most recently started wins' as ARCHITECTURE.md claims"`

Bad: `--dirs promptmeter "fix the scheduler"`

## Acting on the output

Treat it as a claim, not truth. It is a different model reading the same repo
and it can be confidently wrong about specifics, and it does not know this
repo's own stated design rules unless you quoted them in the question.

- Before editing a file it named, open the cited lines and confirm they say
  what it claimed.
- If a claim contradicts the file, the file wins. Say the answer was wrong.
- If it proposes a dependency, a build step, or a framework: this repo is
  Python-stdlib-only with a no-build vanilla-JS frontend (`ARCHITECTURE.md`
  §8) — that proposal is very likely wrong here, not a valid option to weigh.
- Do not relay its findings as established fact without checking the part you
  are about to act on.

## Failure handling

Exit codes from `.claude/scripts/gemini.mjs` itself (see its header comment):

- **2 — setup problem.** Nothing installed, or authentication failed with
  `agy`. Stop and report it; retrying will not fix a 2.
- **1 — transient.** The engine returned an error, hit a quota limit, or timed
  out. Say so in one line, then either retry once with a narrower `--dirs`, or
  fall back to reading the files yourself and say that's what you're doing.

Always name which one happened. Silently switching to reading files yourself
without saying the Gemini call failed hides a broken setup.

For a whole task rather than one question — a feature, a refactor, a formula
or schema change — hand it to the `gemini-planner` subagent instead of using
this skill directly; it does the same dispatch but also checks the answer
against this repo before returning a plan.
