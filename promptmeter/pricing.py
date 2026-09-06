"""Model catalogue, prices and the cost formula.

Covers Anthropic, OpenAI and Google. Everything lives in editable JSON next to
this module — prices and context windows change, and a stale table is a quietly
wrong app. Each entry carries what the estimator actually needs:

    in / out          list price, USD per million tokens
    cache_read        multiplier on the input price for a cache hit
    cache_write_1h/5m multiplier for writing the cache (1.0 where the vendor
                      caches automatically and charges no premium)
    context           input context window, tokens
    max_output        hard ceiling on one response, tokens
    thinking          whether the model reasons before answering
    thinking_share    fraction of output tokens that are thinking, by effort —
                      a starting assumption only; thinking_share() below
                      switches to your own learned per-model average once
                      there is enough real history to trust it

Anthropic prices and limits verified against the current API documentation in
September 2026 (context/max_output were stale for opus-5, sonnet-5, sonnet-4-6
and fable-5 — fixed then). OpenAI/Gemini figures are not independently
verified past their original estimate. Check before trusting a number to two
decimal places.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import db

_DIR = Path(__file__).resolve().parent / "data"

VENDORS = {
    "anthropic": {"label": "Anthropic — Claude", "order": 1},
    "openai":    {"label": "OpenAI — GPT",      "order": 2},
    "google":    {"label": "Google — Gemini",   "order": 3},
    "local":     {"label": "Local / self-hosted", "order": 4},
}

# thinking_share: how much of the output is internal reasoning at each effort
# level. Thinking tokens are billed as output tokens by all three vendors.
_TH = {"none": 0.0, "low": 0.15, "medium": 0.35, "high": 0.60, "max": 0.78}
_TH_LIGHT = {"none": 0.0, "low": 0.10, "medium": 0.25, "high": 0.45, "max": 0.60}
_TH_OFF = {k: 0.0 for k in _TH}

DEFAULT_MODELS = {
    # ---------------------------------------------------------- Anthropic
    "claude-opus-5": {
        "vendor": "anthropic", "label": "Claude Opus 5", "tier": "opus",
        "in": 5.0, "out": 25.0, "cache_read": 0.1, "cache_write_1h": 2.0,
        "cache_write_5m": 1.25, "context": 1000000, "max_output": 128000,
        "thinking": True, "thinking_share": _TH,
    },
    "claude-sonnet-5": {
        "vendor": "anthropic", "label": "Claude Sonnet 5", "tier": "sonnet",
        "in": 2.0, "out": 10.0, "cache_read": 0.1, "cache_write_1h": 2.0,
        "cache_write_5m": 1.25, "context": 1000000, "max_output": 128000,
        "thinking": True, "thinking_share": _TH,
    },
    "claude-haiku-4-5": {
        "vendor": "anthropic", "label": "Claude Haiku 4.5", "tier": "haiku",
        "in": 1.0, "out": 5.0, "cache_read": 0.1, "cache_write_1h": 2.0,
        "cache_write_5m": 1.25, "context": 200000, "max_output": 32000,
        "thinking": True, "thinking_share": _TH_LIGHT,
    },
    "claude-sonnet-4-6": {
        "vendor": "anthropic", "label": "Claude Sonnet 4.6", "tier": "sonnet",
        "in": 3.0, "out": 15.0, "cache_read": 0.1, "cache_write_1h": 2.0,
        "cache_write_5m": 1.25, "context": 1000000, "max_output": 64000,
        "thinking": True, "thinking_share": _TH,
    },
    "claude-fable-5": {
        "vendor": "anthropic", "label": "Claude Fable 5", "tier": "opus",
        "in": 10.0, "out": 50.0, "cache_read": 0.1, "cache_write_1h": 2.0,
        "cache_write_5m": 1.25, "context": 1000000, "max_output": 128000,
        "thinking": True, "thinking_share": _TH,
    },

    # ------------------------------------------------------------- OpenAI
    "gpt-5.6-sol": {
        "vendor": "openai", "label": "GPT-5.6 Sol", "tier": "opus",
        "in": 2.0, "out": 10.0, "cache_read": 0.1, "cache_write_1h": 1.0,
        "cache_write_5m": 1.0, "context": 1050000, "max_output": 128000,
        "thinking": True, "thinking_share": _TH,
    },
    "gpt-5.6-terra": {
        "vendor": "openai", "label": "GPT-5.6 Terra", "tier": "sonnet",
        "in": 1.0, "out": 6.0, "cache_read": 0.1, "cache_write_1h": 1.0,
        "cache_write_5m": 1.0, "context": 1050000, "max_output": 128000,
        "thinking": True, "thinking_share": _TH,
    },
    "gpt-5.6-luna": {
        "vendor": "openai", "label": "GPT-5.6 Luna", "tier": "haiku",
        "in": 0.10, "out": 0.60, "cache_read": 0.1, "cache_write_1h": 1.0,
        "cache_write_5m": 1.0, "context": 1050000, "max_output": 128000,
        "thinking": True, "thinking_share": _TH_LIGHT,
    },
    "gpt-5.3-codex": {
        "vendor": "openai", "label": "GPT-5.3 Codex", "tier": "sonnet",
        "in": 1.75, "out": 14.0, "cache_read": 0.1, "cache_write_1h": 1.0,
        "cache_write_5m": 1.0, "context": 400000, "max_output": 128000,
        "thinking": True, "thinking_share": _TH,
    },

    # ------------------------------------------------------------- Google
    "gemini-3.1-pro": {
        "vendor": "google", "label": "Gemini 3.1 Pro", "tier": "opus",
        "in": 2.0, "out": 12.0, "cache_read": 0.1, "cache_write_1h": 1.0,
        "cache_write_5m": 1.0, "context": 1000000, "max_output": 64000,
        "thinking": True, "thinking_share": _TH,
        "long_context": {"threshold": 200000, "in": 4.0, "out": 18.0},
    },
    "gemini-3.7-flash": {
        "vendor": "google", "label": "Gemini 3.7 Flash", "tier": "sonnet",
        "in": 0.75, "out": 3.75, "cache_read": 0.1, "cache_write_1h": 1.0,
        "cache_write_5m": 1.0, "context": 1000000, "max_output": 64000,
        "thinking": True, "thinking_share": _TH_LIGHT,
    },
    "gemini-3.5-flash": {
        "vendor": "google", "label": "Gemini 3.5 Flash", "tier": "sonnet",
        "in": 1.5, "out": 9.0, "cache_read": 0.1, "cache_write_1h": 1.0,
        "cache_write_5m": 1.0, "context": 1000000, "max_output": 64000,
        "thinking": True, "thinking_share": _TH_LIGHT,
    },
    "gemini-3.1-flash-lite": {
        "vendor": "google", "label": "Gemini 3.1 Flash-Lite", "tier": "haiku",
        "in": 0.25, "out": 1.5, "cache_read": 0.1, "cache_write_1h": 1.0,
        "cache_write_5m": 1.0, "context": 1000000, "max_output": 64000,
        "thinking": True, "thinking_share": _TH_LIGHT,
    },

    # -------------------------------------------------------------- local
    "local-model": {
        "vendor": "local", "label": "Local model (free)", "tier": "haiku",
        "in": 0.0, "out": 0.0, "cache_read": 0.0, "cache_write_1h": 0.0,
        "cache_write_5m": 0.0, "context": 128000, "max_output": 8000,
        "thinking": False, "thinking_share": _TH_OFF,
    },
}

EFFORTS = ["none", "low", "medium", "high", "max"]
DEFAULT_EFFORT = "medium"

# Fallback multipliers for a model with no explicit cache fields.
CACHE = {"write_5m": 1.25, "write_1h": 2.0, "read": 0.1}

DEFAULT_PLANS = {
    "pro":   {"label": "Pro",     "usd_per_5h": 6.0,   "usd_per_week": 48.0},
    "max5":  {"label": "Max 5x",  "usd_per_5h": 30.0,  "usd_per_week": 240.0},
    "max20": {"label": "Max 20x", "usd_per_5h": 120.0, "usd_per_week": 960.0},
}

DEFAULT_PRIORS = {
    "qa_explain":         {"label": "Q&A / explain",      "turns": [1, 2],    "out": [700, 2500]},
    "single_file_edit":   {"label": "Single-file edit",   "turns": [3, 8],    "out": [900, 3000]},
    "multi_file_feature": {"label": "Multi-file feature", "turns": [12, 40],  "out": [1200, 4000]},
    "build_app":          {"label": "Build an app",       "turns": [35, 120], "out": [1500, 5000]},
    "research_report":    {"label": "Research + report",  "turns": [15, 45],  "out": [2000, 8000]},
    "refactor":           {"label": "Repo-wide refactor", "turns": [25, 90],  "out": [1000, 3500]},
    "data_task":          {"label": "Data / spreadsheet", "turns": [8, 25],   "out": [1100, 3500]},
}


def _load(name: str, fallback: dict) -> dict:
    p = _DIR / name
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data:
                return data
        except Exception:                                   # noqa: BLE001
            pass
    _DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(fallback, indent=2), encoding="utf-8")
    return dict(fallback)


def models() -> dict:
    return _load("models.json", DEFAULT_MODELS)


def prices() -> dict:
    """Backwards-compatible alias — the catalogue is a superset of the old shape."""
    return models()


def priors() -> dict:
    return _load("priors.json", DEFAULT_PRIORS)


def plans() -> dict:
    return _load("plans.json", DEFAULT_PLANS)


def plan(pid: str) -> dict:
    return plans().get(pid) or plans()["pro"]


FALLBACK = {
    "vendor": "anthropic", "label": "unknown", "tier": "sonnet",
    "in": 2.0, "out": 10.0, "cache_read": 0.1, "cache_write_1h": 2.0,
    "cache_write_5m": 1.25, "context": 200000, "max_output": 32000,
    "thinking": False, "thinking_share": _TH_OFF,
}


def price_of(model: str) -> dict:
    m = models()
    return m.get(model) or m.get("claude-sonnet-5") or dict(FALLBACK)


def spec(model: str) -> dict:
    """Full spec with every field guaranteed present."""
    s = dict(FALLBACK)
    s.update(price_of(model))
    s.setdefault("thinking_share", _TH_OFF)
    return s


def by_vendor() -> list[dict]:
    """Catalogue grouped for a picker, cheapest first inside each vendor."""
    groups: dict[str, list] = {}
    for mid, m in models().items():
        groups.setdefault(m.get("vendor", "anthropic"), []).append({"id": mid, **m})
    out = []
    for vid, info in sorted(VENDORS.items(), key=lambda kv: kv[1]["order"]):
        items = sorted(groups.get(vid, []), key=lambda x: x.get("in", 0))
        if items:
            out.append({"id": vid, "label": info["label"], "models": items})
    return out


def rates(model: str, context_tokens: int = 0) -> tuple[float, float]:
    """Input and output $/M, honouring any long-context tier."""
    s = spec(model)
    lc = s.get("long_context")
    if lc and context_tokens > lc.get("threshold", 1e12):
        return float(lc["in"]), float(lc["out"])
    return float(s["in"]), float(s["out"])


# thinking_share, learned: transcripts carry a `usage` block but never the
# effort level that produced a turn (that's a request-time parameter, never
# echoed back), so what we can actually learn from your own history is one
# real average per model — not per (model, effort) the way the static table
# is shaped. Below MIN_THINKING_OBS real thinking-bearing turns for a model,
# stay on the static curve; a single early observation is not worth trusting
# over a deliberately-chosen shape.
MIN_THINKING_OBS = 5


def learned_thinking_share(model: str) -> dict:
    """This model's own EWMA-learned thinking-token share, if trustworthy yet."""
    data = db.get_setting("thinking_share_learned", {}) or {}
    row = data.get(model) or {}
    n = int(row.get("n", 0))
    if n < MIN_THINKING_OBS:
        return {"share": None, "n": n}
    return {"share": float(row["share"]), "n": n}


def learn_thinking_share(model: str, rho: float) -> None:
    """Nudge this model's observed thinking-share toward one real transcript
    turn that actually contained a thinking block (character share of
    thinking-block length over total thinking+text length — Anthropic's usage
    block has no separate thinking-token counter to read instead). EWMA blend,
    mirroring estimator.learn_token_ratio()'s existing cur*0.9+observed*0.1
    pattern rather than inventing a new one.
    """
    if not model or not (0.0 <= rho <= 1.0):
        return
    data = db.get_setting("thinking_share_learned", {}) or {}
    row = data.get(model) or {}
    n = int(row.get("n", 0))
    cur = float(row["share"]) if n else rho
    data[model] = {"share": round(cur * 0.9 + rho * 0.1, 4) if n else round(rho, 4),
                   "n": n + 1}
    db.set_setting("thinking_share_learned", data)


def thinking_share(model: str, effort: str = DEFAULT_EFFORT) -> float:
    s = spec(model)
    if not s.get("thinking"):
        return 0.0
    learned = learned_thinking_share(model)
    if learned["share"] is not None:
        return learned["share"]
    return float((s.get("thinking_share") or {}).get(effort, _TH[DEFAULT_EFFORT]))


def effort_output_multiplier(model: str, effort: str = DEFAULT_EFFORT) -> float:
    """How total output scales with effort.

    Thinking is produced *in addition to* the visible answer, not carved out of
    it. Our priors were learned at the default effort, so holding visible output
    fixed and re-deriving the total gives:

        total = visible / (1 - share)      and      visible = prior x (1 - share_default)

    which is why max effort costs roughly three times default, not the same.
    """
    s_def = thinking_share(model, DEFAULT_EFFORT)
    s_now = thinking_share(model, effort)
    return (1.0 - s_def) / max(0.08, 1.0 - s_now)


# ---------------------------------------------------------------- costing

def cost_usd(model: str, in_tokens: int, out_tokens: int,
             cache_read: int = 0, cache_write: int = 0, one_hour: bool = True) -> float:
    """Cost of one call. Unchanged signature — used by the transcript watcher."""
    s = spec(model)
    p_in, p_out = rates(model, in_tokens + cache_read + cache_write)
    w = s.get("cache_write_1h" if one_hour else "cache_write_5m",
              CACHE["write_1h" if one_hour else "write_5m"])
    r = s.get("cache_read", CACHE["read"])
    return (in_tokens * p_in
            + cache_write * p_in * w
            + cache_read * p_in * r
            + out_tokens * p_out) / 1e6


def loop_budget(model: str, base_input: int, turns: float, growth: int,
                out_per_turn: int, *, effort: str = DEFAULT_EFFORT,
                cached_prefix: bool = True) -> dict:
    """Full token and cost accounting for an agent run.

    An agent re-sends the whole conversation each turn, so input grows as an
    arithmetic series. Once the conversation outgrows the context window the
    agent must compact, which is itself a large read plus a summary write — so
    the window is a cost driver, not just a limit.
    """
    T = max(1.0, float(turns))
    s = spec(model)
    window = int(s["context"])
    max_out = int(s["max_output"])

    per_turn_out = min(float(out_per_turn) * effort_output_multiplier(model, effort),
                       float(max_out))
    grown = growth * T * (T - 1) / 2.0

    # peak conversation size, before any compaction
    peak_ctx = base_input + growth * (T - 1) + per_turn_out
    compactions = 0
    compact_in = compact_out = 0.0
    if peak_ctx > window:
        # each compaction reads ~70% of the window and writes a summary,
        # then buys back roughly half the window of headroom
        headroom = max(window * 0.5, 1.0)
        compactions = int(math.ceil((peak_ctx - window) / headroom))
        compact_in = compactions * window * 0.70
        compact_out = compactions * min(4000.0, max_out)
        peak_ctx = window

    if cached_prefix and T > 1:
        w = s.get("cache_write_1h", CACHE["write_1h"])
        r = s.get("cache_read", CACHE["read"])
        base_billable = base_input * w + base_input * (T - 1) * r
    else:
        base_billable = base_input * T

    total_in_raw = base_input * T + grown + compact_in
    billable_in = base_billable + grown + compact_in
    total_out = per_turn_out * T + compact_out
    think = total_out * thinking_share(model, effort)

    p_in, p_out = rates(model, int(peak_ctx))
    in_usd = billable_in * p_in / 1e6
    out_usd = total_out * p_out / 1e6

    return {
        "turns": round(T, 1),
        "input_tokens": int(total_in_raw),
        "billable_input_tokens": int(billable_in),
        "output_tokens": int(total_out),
        "thinking_tokens": int(think),
        "visible_output_tokens": int(total_out - think),
        "total_tokens": int(total_in_raw + total_out),
        "growth_tokens": int(grown),
        "peak_context": int(peak_ctx),
        "context_window": window,
        "context_pct": round(100.0 * min(peak_ctx, window) / window, 1),
        "compactions": compactions,
        "compaction_tokens": int(compact_in + compact_out),
        "output_capped": per_turn_out < out_per_turn,
        "max_output": max_out,
        "cost_in": round(in_usd, 6),
        "cost_out": round(out_usd, 6),
        "cost": round(in_usd + out_usd, 6),
        "rate_in": p_in, "rate_out": p_out,
        "effort": effort,
    }


def loop_cost(model: str, base_input: int, turns: float, growth: int,
              out_per_turn: int, cached_prefix: bool = True) -> float:
    """Cost only — kept so existing callers keep working."""
    return loop_budget(model, base_input, turns, growth, out_per_turn,
                       cached_prefix=cached_prefix)["cost"]
