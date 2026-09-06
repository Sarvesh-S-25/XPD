# `promptmeter/data/` — the tables you are meant to edit

Three JSON files. These are **data, not code** — deliberately, because prices
change, models get released, and a hardcoded constant that has gone stale is a
quietly wrong app.

Edit any of them in a text editor and restart. If a file is missing or corrupt
it is regenerated from the defaults in `pricing.py`, so you cannot break the app
by editing it badly — worst case you lose your edits.

| File | Holds |
|---|---|
| `models.json` | Every model: prices, context window, output cap, thinking behaviour |
| `plans.json` | How much work a Pro / Max 5x / Max 20x window holds |
| `priors.json` | How long each kind of task typically runs, before it has seen you work |

---

## `models.json`

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

**Adding a model** — copy the closest entry, change the values, restart. Nothing
else needs touching; the picker, the estimator and the routing ladder all read
from here.

**`thinking_share` is the softest number in the project.** No vendor publishes
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

Prices were verified against vendor pricing pages in **August 2026**. Check them
before trusting a figure to two decimal places.

**Correction, September 2026:** `context`/`max_output` were stale for
`claude-opus-5`, `claude-sonnet-5`, `claude-sonnet-4-6` and `claude-fable-5`
(each had been capped at the prior generation's 200k-context/64k-output
figures). Fixed against Anthropic's current API documentation — prices were
already correct and untouched. OpenAI/Gemini entries were not re-verified in
this pass. There used to be a second, smaller `pricing.json` in this folder;
it was dead — nothing in the code read it — and has been deleted so there is
one source of truth.

---

## `plans.json`

How much work one window of your subscription holds, in list-price dollars.

```json
{ "pro": { "label": "Pro", "usd_per_5h": 6.0, "usd_per_week": 48.0 } }
```

**These are estimates, and they are meant to be replaced.** Anthropic publishes
no absolute limits for any plan — Max 5x is documented as "five times more usage
per session than Pro", with no token figure anywhere. So these are starting
points chosen from the relative multipliers.

The moment you sync one real reading, PromptMeter **solves for your true
capacity** (`spend in window ÷ percentage used`) and stops using this file. The
Windows page then reads *measured* instead of *plan estimate*. If you never
calibrate, this table is what your percentages are based on.

---

## `priors.json`

How long each kind of task runs, before the app has watched you work.

```json
{ "build_app": { "label": "Build an app", "turns": [35, 120], "out": [1500, 5000] } }
```

`turns` is `[typical, worst case]`; `out` is output tokens per turn, same shape.
Seven task classes, matched by the keyword classifier in `estimator.py`.

**These are my judgement, not measurements.** They are the least evidence-backed
numbers in the project and everything downstream inherits them. That matters
most in your first dozen estimates.

They stop mattering as you use it: `learning.py` blends these with your observed
history using shrinkage weighting `n/(n+6)` — two runs sit at 25% weight on your
own data, fifty at 89%. The History screen labels each row *prior only* /
*learning* / *measured* so you always know which you are looking at.

**Tuning them:** if the History screen shows your runs consistently landing above
or below the band for one task type, edit that row. It takes effect on the next
estimate.
