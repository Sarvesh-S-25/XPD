"""Plan-window meter: the 5-hour and 7-day ledgers.

Ground truth arrives from the Claude Code status line, which hands a subscriber's
real limit percentages to any local script:

    rate_limits.five_hour.used_percentage / .resets_at   (unix epoch seconds)
    rate_limits.seven_day.used_percentage / .resets_at

We never need Anthropic's absolute limits — the percentage IS the enforced
quantity. To convert an estimated dollar cost into "percent of window", we fit
one scalar per window (alpha = percent consumed per list-price dollar) from
observed pairs. That derives the plan capacity Anthropic does not publish.
"""
from __future__ import annotations

import time

from . import db, pricing

FIVE_HOURS = 5 * 3600
SEVEN_DAYS = 7 * 86400
MIN_SAMPLES = 4
# Percent of a window consumed per list-price dollar, before any fitting.
# 5.0 implies ~$20 of list-price work per 5-hour window and ~$110 per week,
# which is a middle-of-the-road subscription. It is only a starting point:
# the first few dozen status-line samples replace it with the real figure.
DEFAULT_ALPHA5 = 5.0
DEFAULT_ALPHA7 = 0.9


# ---------------------------------------------------------------- ingest

def ingest(payload: dict) -> dict:
    """Accept one status-line JSON blob. Every field is optional and defensive."""
    rl = payload.get("rate_limits") or {}
    five = rl.get("five_hour") or {}
    seven = rl.get("seven_day") or {}
    cw = payload.get("context_window") or {}
    cu = cw.get("current_usage") or {}
    model = (payload.get("model") or {}).get("id", "") or ""

    prev = db.row("SELECT * FROM meter ORDER BY ts DESC LIMIT 1")

    in_tok = int(cu.get("input_tokens") or 0)
    out_tok = int(cu.get("output_tokens") or 0)
    c_read = int(cu.get("cache_read_input_tokens") or 0)
    c_write = int(cu.get("cache_creation_input_tokens") or 0)
    cost = pricing.cost_usd(model or "claude-sonnet-5", in_tok, out_tok, c_read, c_write)

    db.run(
        """INSERT INTO meter(ts,pct5,pct7,resets5,resets7,model,session_id,
                             in_tokens,out_tokens,cache_read,cache_write,cost_usd,ctx_pct)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (time.time(),
         _f(five.get("used_percentage")), _f(seven.get("used_percentage")),
         _f(five.get("resets_at")), _f(seven.get("resets_at")),
         model, payload.get("session_id", "") or "",
         in_tok, out_tok, c_read, c_write, cost,
         _f(cw.get("used_percentage"))),
    )

    if prev:
        _fit(prev)
    p5, p7 = _f(five.get("used_percentage")), _f(seven.get("used_percentage"))
    if p5 is not None or p7 is not None:
        calibrate_from_reading(p5, p7)
    return status()


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- calibration

def _fit(prev: dict) -> None:
    """Refit alpha from all observed (delta-percent, delta-dollar) pairs.

    Least squares through the origin: alpha = sum(pct*cost) / sum(cost^2).
    Pairs that straddle a window reset (percentage went down) are discarded.
    """
    recs = db.rows("SELECT ts,pct5,pct7,cost_usd,resets5,resets7 FROM meter ORDER BY ts")
    if len(recs) < MIN_SAMPLES + 1:
        return
    pairs5, pairs7 = [], []
    for a, b in zip(recs, recs[1:]):
        dc = (b["cost_usd"] or 0) - 0.0
        if dc <= 0:
            continue
        if a["pct5"] is not None and b["pct5"] is not None and b["pct5"] >= a["pct5"]:
            pairs5.append((b["pct5"] - a["pct5"], dc))
        if a["pct7"] is not None and b["pct7"] is not None and b["pct7"] >= a["pct7"]:
            pairs7.append((b["pct7"] - a["pct7"], dc))

    for pairs, key, floor in ((pairs5, "alpha5", 0.05), (pairs7, "alpha7", 0.005)):
        useful = [(p, c) for p, c in pairs if p > 0]
        if len(useful) < MIN_SAMPLES:
            continue
        num = sum(p * c for p, c in useful)
        den = sum(c * c for _, c in useful)
        if den <= 0:
            continue
        a = num / den
        if a < floor:
            continue
        db.set_setting(key, round(a, 5))
        db.set_setting(key + "_n", len(useful))


def alphas() -> tuple[float, float]:
    a5 = float(db.get_setting("alpha5", DEFAULT_ALPHA5) or DEFAULT_ALPHA5)
    a7 = float(db.get_setting("alpha7", DEFAULT_ALPHA7) or DEFAULT_ALPHA7)
    return a5, a7


def capacity() -> dict:
    """Plan capacity in list-price dollars per window, and where it came from."""
    cap5, cap7, basis = capacity_usd()
    return {
        "usd_per_5h": round(cap5, 2),
        "usd_per_week": round(cap7, 2),
        "confidence": basis,
        "fitted_at": db.get_setting("cap_fitted_at"),
        "plan": plan_id(),
        "plan_label": pricing.plan(plan_id()).get("label", plan_id()),
        "samples5": int(db.get_setting("alpha5_n", 0) or 0),
    }


# ---------------------------------------------------------------- status

def latest() -> dict | None:
    return db.row(
        "SELECT * FROM meter WHERE pct5 IS NOT NULL OR pct7 IS NOT NULL ORDER BY ts DESC LIMIT 1")


# -------------------------------------------------- derived from transcripts

def plan_id() -> str:
    return str(db.get_setting("plan", "max5") or "max5")


def capacity_usd() -> tuple[float, float, str]:
    """(per 5h, per week, basis).

    A capacity solved from a real percentage reading beats everything: one
    reading of "you are 37% through the 5-hour window" plus the exact dollars
    the ledger says you spent in that window gives the window's true size.
    """
    c5 = db.get_setting("cap5_fitted")
    c7 = db.get_setting("cap7_fitted")
    if c5 and c7 and float(c5) > 0 and float(c7) > 0:
        return float(c5), float(c7), "measured"
    p = pricing.plan(plan_id())
    return float(p["usd_per_5h"]), float(p["usd_per_week"]), "plan estimate"


def raw_window_spend() -> tuple[float, float, int]:
    """Spend inside the current 5-hour window and the trailing 7 days, from
    transcripts alone. Used for calibration, where an anchored view would be
    circular."""
    from . import watcher
    watcher.init()
    now = time.time()
    rows = db.rows("SELECT ts, cost_usd FROM turns WHERE ts >= ? ORDER BY ts",
                   (now - SEVEN_DAYS,))
    if not rows:
        return 0.0, 0.0, 0
    start, spent5 = None, 0.0
    for r in rows:
        t = r["ts"]
        if start is None or t >= start + FIVE_HOURS:
            start, spent5 = t, 0.0
        spent5 += r["cost_usd"] or 0.0
    if start is not None and now >= start + FIVE_HOURS:
        spent5 = 0.0
    return spent5, sum(r["cost_usd"] or 0.0 for r in rows), len(rows)


def calibrate_from_reading(pct5: float | None, pct7: float | None) -> dict:
    """Solve window capacity from one true percentage reading.

    Only meaningful when the transcripts actually account for the usage behind
    that percentage. When most of your work happens somewhere transcripts cannot
    see — Cowork runs in the cloud, claude.ai runs in a browser — the reading is
    still stored and used as the anchor, but it tells us nothing about capacity,
    so we leave capacity alone rather than fitting it to a fiction.
    """
    spent5, spent7, n = raw_window_spend()
    out, skipped = {}, []
    for pct, spent, key, label in ((pct5, spent5, "cap5_fitted", "5-hour"),
                                   (pct7, spent7, "cap7_fitted", "weekly")):
        if pct is None or pct < 3:
            continue
        if spent <= 0.05:
            skipped.append(label)
            continue
        implied = spent / (pct / 100.0)
        prev = db.get_setting(key)
        value = implied if not prev else float(prev) * 0.5 + implied * 0.5
        db.set_setting(key, round(value, 4))
        out[label] = round(value, 2)
    if out:
        db.set_setting("cap_fitted_at", time.time())
        return {"ok": True, "capacity": out, "anchored": True}
    return {"ok": True, "capacity": {}, "anchored": True, "skipped": skipped,
            "reason": ("Reading saved and now anchoring your windows. Window size was left "
                       "alone because local sessions account for almost none of it — that "
                       "usage happened in Cowork or the browser, which write nothing here.")}


def derived() -> dict | None:
    """Current window state, anchored on the last real reading.

    A reading (from the status line, or typed off the app's usage dialog) gives
    the true percentage at a known moment and the true reset times. From there we
    tick the clock down and add whatever new spend the transcripts have recorded
    since. Between readings the numbers move on their own; at the reset they go
    back to zero by themselves.

    With no reading at all we fall back to spend alone, converted with the plan
    estimate.
    """
    from . import watcher
    watcher.init()

    now = time.time()
    cap5, cap7, basis = capacity_usd()
    anchor = db.row(
        "SELECT * FROM meter WHERE pct5 IS NOT NULL ORDER BY ts DESC LIMIT 1")

    def spend_since(t0: float) -> float:
        return float(db.scalar(
            "SELECT SUM(cost_usd) FROM turns WHERE ts > ?", (t0,), 0.0) or 0.0)

    if anchor:
        r5 = anchor["resets5"]
        r7 = anchor["resets7"]
        base5 = float(anchor["pct5"] or 0.0)
        base7 = float(anchor["pct7"] or 0.0)

        # A window that has rolled over since the reading starts again at zero.
        if r5 and now >= r5:
            base5 = 0.0
            while r5 and now >= r5:
                r5 += FIVE_HOURS
            since5 = r5 - FIVE_HOURS
        else:
            since5 = anchor["ts"]
        if r7 and now >= r7:
            base7 = 0.0
            r7 = now + SEVEN_DAYS
            since7 = now
        else:
            since7 = anchor["ts"]

        add5 = spend_since(since5)
        add7 = spend_since(since7)
        pct5 = min(100.0, base5 + (100.0 * add5 / cap5 if cap5 > 0 else 0.0))
        pct7 = min(100.0, base7 + (100.0 * add7 / cap7 if cap7 > 0 else 0.0))
        return {
            "pct5": pct5, "pct7": pct7,
            "resets5": r5 or (now + FIVE_HOURS),
            "resets7": r7 or (now + SEVEN_DAYS),
            "spent5": round(add5, 4), "spent7": round(add7, 4),
            "cap5": cap5, "cap7": cap7, "basis": basis,
            "anchored_at": anchor["ts"],
            "anchor_pct5": base5, "anchor_pct7": base7,
            "turns": int(db.scalar("SELECT COUNT(*) FROM turns WHERE ts > ?",
                                   (since5,), 0) or 0),
            "last_turn": db.scalar("SELECT MAX(ts) FROM turns", (), None),
            "stale_hours": round((now - anchor["ts"]) / 3600.0, 1),
        }

    # No reading ever taken — spend only.
    rows = db.rows("SELECT ts, cost_usd FROM turns WHERE ts >= ? ORDER BY ts",
                   (now - SEVEN_DAYS,))
    if not rows:
        return None
    start, spent5 = None, 0.0
    for r in rows:
        t = r["ts"]
        if start is None or t >= start + FIVE_HOURS:
            start, spent5 = t, 0.0
        spent5 += r["cost_usd"] or 0.0
    if start is not None and now >= start + FIVE_HOURS:
        start, spent5 = None, 0.0
    spent7 = sum(r["cost_usd"] or 0.0 for r in rows)
    return {
        "pct5": min(100.0, 100.0 * spent5 / cap5) if cap5 > 0 else None,
        "pct7": min(100.0, 100.0 * spent7 / cap7) if cap7 > 0 else None,
        "resets5": (start + FIVE_HOURS) if start else None,
        "resets7": rows[0]["ts"] + SEVEN_DAYS,
        "spent5": round(spent5, 4), "spent7": round(spent7, 4),
        "cap5": cap5, "cap7": cap7, "basis": basis,
        "anchored_at": None, "turns": len(rows),
        "last_turn": rows[-1]["ts"], "stale_hours": None,
    }


def status() -> dict:
    """Current window state plus the number that matters: when you run out.

    Two sources, in order of trust:
      1. a fresh status-line reading — the exact percentages Anthropic enforces
      2. the transcript ledger — every turn Claude Code wrote to disk, from the
         terminal *and* the desktop app, converted with the fitted capacity

    Source 2 needs no setup at all, which is why it is the default path.
    """
    m = latest()
    now = time.time()
    live = bool(m and (now - m["ts"]) < 3600)

    pct5 = (m or {}).get("pct5") if live else None
    pct7 = (m or {}).get("pct7") if live else None
    r5 = (m or {}).get("resets5") if live else None
    r7 = (m or {}).get("resets7") if live else None
    source = ("your reading" if (m or {}).get("session_id") == "manual" else "status line") \
        if live else "none"

    d = derived()
    if not live and d:
        pct5, pct7 = d["pct5"], d["pct7"]
        r5, r7 = d["resets5"], d["resets7"]
        source = "transcripts"
    elif live and d:
        # keep the derived figures visible for comparison, but trust the reading
        pass

    if r5 is None and pct5 is not None:
        r5 = now + FIVE_HOURS
    if r7 is None and pct7 is not None:
        r7 = now + SEVEN_DAYS

    rem5 = None if pct5 is None else max(0.0, 100.0 - pct5)
    rem7 = None if pct7 is None else max(0.0, 100.0 - pct7)

    burn = burn_rate()
    exhaust = None
    if rem7 is not None and burn["pp7_per_hour"] > 0.01:
        hours = rem7 / burn["pp7_per_hour"]
        exhaust = now + hours * 3600

    sustainable = None
    if rem7 is not None and r7:
        hrs_left = max(0.25, (r7 - now) / 3600.0)
        sustainable = rem7 / hrs_left

    return {
        "live": live or bool(d),
        "source": source,
        "derived": d,
        "plan": plan_id(),
        "observed_at": (m or {}).get("ts") if live else (d or {}).get("last_turn"),
        "five_hour": {"used": pct5, "remaining": rem5, "resets_at": r5,
                      "seconds_to_reset": None if not r5 else max(0, r5 - now)},
        "seven_day": {"used": pct7, "remaining": rem7, "resets_at": r7,
                      "seconds_to_reset": None if not r7 else max(0, r7 - now)},
        "burn": burn,
        "sustainable_pp7_per_hour": None if sustainable is None else round(sustainable, 3),
        "exhausts_at": exhaust,
        "pace_ok": None if (sustainable is None) else burn["pp7_per_hour"] <= sustainable,
        "capacity": capacity(),
        "regime": regime(),
    }


def burn_rate(hours: float = 6.0) -> dict:
    """Observed percent-per-hour over a recent trailing window.

    Prefers the transcript ledger, which exists without any setup; falls back to
    status-line samples when transcripts are unavailable.
    """
    since = time.time() - hours * 3600
    tr = db.rows("SELECT ts, cost_usd FROM turns WHERE ts >= ? ORDER BY ts", (since,)) \
        if _has_turns() else []
    if tr:
        cap5, cap7, _ = capacity_usd()
        cost = sum(r["cost_usd"] or 0.0 for r in tr)
        span = max(0.25, (tr[-1]["ts"] - tr[0]["ts"]) / 3600.0) if len(tr) > 1 else hours
        return {
            "window_hours": round(span, 2),
            "pp5_per_hour": round(100.0 * cost / cap5 / span, 3) if cap5 > 0 else 0.0,
            "pp7_per_hour": round(100.0 * cost / cap7 / span, 3) if cap7 > 0 else 0.0,
            "usd_per_hour": round(cost / span, 4),
            "samples": len(tr),
        }

    recs = db.rows(
        "SELECT ts,pct5,pct7,cost_usd FROM meter WHERE ts >= ? ORDER BY ts", (since,))
    d5 = d7 = 0.0
    cost = 0.0
    for a, b in zip(recs, recs[1:]):
        if a["pct5"] is not None and b["pct5"] is not None and b["pct5"] >= a["pct5"]:
            d5 += b["pct5"] - a["pct5"]
        if a["pct7"] is not None and b["pct7"] is not None and b["pct7"] >= a["pct7"]:
            d7 += b["pct7"] - a["pct7"]
        cost += b["cost_usd"] or 0
    span = hours if len(recs) < 2 else max(0.25, (recs[-1]["ts"] - recs[0]["ts"]) / 3600.0)
    return {
        "window_hours": round(span, 2),
        "pp5_per_hour": round(d5 / span, 3),
        "pp7_per_hour": round(d7 / span, 3),
        "usd_per_hour": round(cost / span, 4),
        "samples": len(recs),
    }


def _has_turns() -> bool:
    try:
        return bool(db.scalar("SELECT 1 FROM turns LIMIT 1", (), 0))
    except Exception:                                   # noqa: BLE001 table absent
        return False


def regime() -> str:
    """Which limit actually binds this user: burst (5h) or sustained (weekly)."""
    b = burn_rate(24.0)
    if b["samples"] < 4:
        return "unknown"
    # can they drain the 5h window inside 5 hours of work?
    if b["pp5_per_hour"] * 5 >= 100:
        return "burst"
    if b["pp7_per_hour"] * 24 * 7 >= 100:
        return "sustained"
    return "comfortable"


def remaining() -> tuple[float, float]:
    """Remaining PP in each window, with safe fallbacks when unmetered."""
    s = status()
    r5 = s["five_hour"]["remaining"]
    r7 = s["seven_day"]["remaining"]
    return (100.0 if r5 is None else r5, 100.0 if r7 is None else r7)


# ---------------------------------------------------------------- leaks

def leaks() -> list[dict]:
    """Deterministic detectors for the documented ways usage climbs unnoticed."""
    out = []
    recs = db.rows("SELECT * FROM meter ORDER BY ts DESC LIMIT 400")
    if not recs:
        return out
    total_cost = sum(r["cost_usd"] or 0 for r in recs) or 1e-9

    # 1. cache misses: large fresh input with no cache read
    miss = [r for r in recs if (r["cache_write"] or 0) > 3000 and (r["cache_read"] or 0) < 500]
    miss_cost = sum(r["cost_usd"] or 0 for r in miss)
    if miss_cost / total_cost >= 0.10:
        out.append(_leak("Cache misses", miss_cost, total_cost,
                         "Long gaps reprocess your whole context at full rate.",
                         "Set ENABLE_PROMPT_CACHING_1H=1, and resume large sessions from a summary."))

    # 2. long context
    longc = [r for r in recs if (r["ctx_pct"] or 0) >= 70]
    lc_cost = sum(r["cost_usd"] or 0 for r in longc)
    if lc_cost / total_cost >= 0.10:
        out.append(_leak("Long context", lc_cost, total_cost,
                         "Every turn re-sends the whole conversation.",
                         "Run /clear between unrelated tasks (/rename first so you can /resume)."))

    # 3. opus on everything
    opus = [r for r in recs if pricing.price_of(r["model"] or "").get("tier") == "opus"]
    op_cost = sum(r["cost_usd"] or 0 for r in opus)
    if op_cost / total_cost >= 0.50 and len(recs) > 10:
        out.append(_leak("Opus by default", op_cost, total_cost,
                         "Opus drains the shared window several times faster than Sonnet.",
                         "Use /model to drop to Sonnet for routine work; Haiku for subagents."))

    # 4. usage that belongs to no tracked step
    iters = db.scalar("SELECT COUNT(*) FROM iterations", (), 0) or 0
    tracked = db.scalar("SELECT SUM(cost_usd) FROM iterations", (), 0.0) or 0.0
    if len(recs) > 25 and iters * 3 < len(recs):
        untracked = max(0.0, total_cost - tracked)
        out.append(_leak("Usage outside your projects", untracked, total_cost,
                         "Most metered turns are not attached to any tracked step — that is "
                         "chat, Cowork, background jobs, or work you have not logged. It counts "
                         "against the same windows.",
                         "Log iterations against a step, and check whether a scheduled task is "
                         "firing while the session sits idle."))
    return out


def _leak(name, cost, total, why, fix):
    return {
        "name": name,
        "share": round(100.0 * cost / total, 1) if total else 0.0,
        "cost": round(cost, 4),
        "why": why, "fix": fix,
    }
