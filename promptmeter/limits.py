"""Subscription limits for providers other than Claude.

Claude's 5-hour and weekly windows are read from Claude Code (meter.py). Other
coding assistants sold on a subscription have limits too — Codex under a ChatGPT
plan is metered in rolling 5-hour and weekly windows, Gemini plans in daily
quotas — but those limits are not published as stable numbers and differ by plan
and by month, so this module never assumes one. You enter what *your* plan
allows; PromptMeter measures the usage reported for that provider's models
against it, over the plan's window.

Honest limits of the measurement, surfaced in the UI rather than hidden:
  - windows are rolling ("the last N hours"), not aligned to the provider's own
    reset clock, which is not observable from here;
  - only usage that reached the ledger counts (Claude Code sessions are read
    automatically; other agents report through `--log-usage` / POST /api/usage).

Presentation-and-storage only: nothing here feeds an estimate or a verdict.
"""
from __future__ import annotations

import math
import time

from . import db, pricing, watcher

# vendor id (as in pricing.py) -> how that provider meters a subscription.
PROVIDERS = {
    "openai": {
        "label": "Codex (ChatGPT plan)",
        "windows": [{"id": "5h", "name": "5-hour", "hours": 5},
                    {"id": "week", "name": "Weekly", "hours": 168}],
    },
    "google": {
        "label": "Gemini (Google plan)",
        "windows": [{"id": "day", "name": "Daily", "hours": 24}],
    },
}
UNITS = ("turns", "usd")
_KEY = "plan_limits"


def _saved() -> dict:
    v = db.get_setting(_KEY) or {}
    return v if isinstance(v, dict) else {}


def _usage(vendor: str, since: float) -> dict:
    """Turns and list-price dollars this vendor's models used since `since`."""
    watcher.init()
    rows = db.rows("SELECT ts, model, cost_usd FROM turns WHERE ts >= ?", (since,))
    vendors: dict[str, str] = {}
    turns, usd, oldest = 0, 0.0, None
    for r in rows:
        m = r["model"] or ""
        if m not in vendors:
            vendors[m] = pricing.spec(m).get("vendor")
        if vendors[m] != vendor:
            continue
        turns += 1
        usd += float(r["cost_usd"] or 0)
        oldest = r["ts"] if oldest is None else min(oldest, r["ts"])
    return {"turns": turns, "usd": round(usd, 4), "oldest": oldest}


def get(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    saved = _saved()
    out = []
    for vendor, info in PROVIDERS.items():
        wins = []
        for w in info["windows"]:
            cfg = (saved.get(vendor) or {}).get(w["id"]) or {}
            unit = cfg.get("unit") if cfg.get("unit") in UNITS else "turns"
            limit = cfg.get("limit")
            since = now - w["hours"] * 3600
            u = _usage(vendor, since)
            used = u[unit]
            has = isinstance(limit, (int, float)) and limit > 0
            wins.append({
                "id": w["id"], "name": w["name"], "hours": w["hours"], "unit": unit,
                "limit": limit if has else None,
                "used": used, "turns": u["turns"], "usd": u["usd"],
                "pct": round(used / limit * 100, 1) if has else None,
                # when the oldest counted turn drops out of the window and frees room
                "frees_at": (u["oldest"] + w["hours"] * 3600) if u["oldest"] is not None else None,
            })
        out.append({"provider": vendor, "label": info["label"], "windows": wins,
                    "configured": any(x["limit"] for x in wins)})
    return {"providers": out}


def save(provider: str, windows: dict) -> dict:
    """windows: {window_id: {"limit": number|""|None, "unit": "turns"|"usd"}}.
    A blank/None limit clears that window. Rejects anything else outright."""
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider.")
    if not isinstance(windows, dict):
        raise ValueError("windows must be an object.")
    valid = {w["id"] for w in PROVIDERS[provider]["windows"]}
    current = _saved()
    mine = dict(current.get(provider) or {})
    for wid, cfg in windows.items():
        if wid not in valid:
            raise ValueError(f"Unknown window '{wid}'.")
        cfg = cfg if isinstance(cfg, dict) else {}
        raw = cfg.get("limit")
        if raw in (None, ""):
            mine.pop(wid, None)
            continue
        try:
            limit = float(raw)
        except (TypeError, ValueError):
            raise ValueError("Limit must be a number.")
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("Limit must be greater than zero.")
        unit = cfg.get("unit") or "turns"
        if unit not in UNITS:
            raise ValueError("Unit must be 'turns' or 'usd'.")
        mine[wid] = {"limit": limit, "unit": unit}
    current[provider] = mine
    db.set_setting(_KEY, current)
    return get()
