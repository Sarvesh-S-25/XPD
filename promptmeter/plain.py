"""Plain-language translation of the numbers.

"421% of your 5-hour window" is precise and useless to anyone who has not
internalised how Anthropic meters a subscription. The engine keeps producing
exact figures; this module says what they mean for the person reading them.

Every function here is presentation only. Nothing feeds back into the estimate.
"""
from __future__ import annotations

import math

FIVE_HOURS = 5


def sessions(pp5: float) -> float:
    """Window-percent expressed as 'how many full 5-hour sessions'."""
    return max(0.0, float(pp5 or 0)) / 100.0


def hours_of_waiting(pp5: float) -> float:
    """Wall-clock you would spend waiting for windows to refill."""
    return max(0.0, math.floor(sessions(pp5))) * FIVE_HOURS


def size(pp5: float) -> dict:
    """One sentence for how big a job is, in units a person feels.

    Claude-specific — it speaks in 5-hour sessions, which only means something
    against an Anthropic plan window. Call size_generic() instead for a
    non-Anthropic model; see explain()'s vendor branch.
    """
    s = sessions(pp5)
    if s < 0.02:
        return {"headline": "Tiny", "detail": "Barely touches your limit.",
                "sessions": s, "band": "good"}
    if s < 0.10:
        return {"headline": "Small", "detail": "Under a tenth of one 5-hour session.",
                "sessions": s, "band": "good"}
    if s < 0.35:
        return {"headline": f"About {_fraction(s)} of a session",
                "detail": "Comfortable — you can do several of these before a reset.",
                "sessions": s, "band": "good"}
    if s < 0.75:
        return {"headline": f"About {_fraction(s)} of a session",
                "detail": "Sizeable. Two of these in a row would use up your window.",
                "sessions": s, "band": "warning"}
    if s < 1.0:
        return {"headline": "Most of one session",
                "detail": "This is close to everything a single 5-hour window holds.",
                "sessions": s, "band": "warning"}
    if s < 2.0:
        return {"headline": "More than one full session",
                "detail": (f"You would run out partway through and wait about "
                           f"{FIVE_HOURS} hours to finish."),
                "sessions": s, "band": "serious"}
    n = math.ceil(s)
    return {"headline": f"About {s:.1f} full sessions",
            "detail": (f"You would hit the limit {n - 1} time"
                       f"{'' if n - 1 == 1 else 's'} and wait roughly "
                       f"{hours_of_waiting(pp5):.0f} hours in total. Split it."),
            "sessions": s, "band": "critical"}


def size_generic(turns_p50: float, total_tokens: int | None) -> dict:
    """size(), for a model with no Claude plan window to measure against.

    Banded on expected turns rather than session-percent — turns is computed
    the same way regardless of vendor (learning.profile), so it is the one
    magnitude signal that means the same thing for any model. Token count is
    reported alongside since that is the number this model is actually billed
    on, not implied.
    """
    t = max(0.0, float(turns_p50 or 0))
    tok = f" (~{total_tokens:,} tokens)" if total_tokens else ""
    if t < 2:
        return {"headline": "Tiny", "detail": f"One or two turns{tok}.", "band": "good"}
    if t < 5:
        return {"headline": "Small", "detail": f"A handful of turns{tok}.", "band": "good"}
    if t < 15:
        return {"headline": "Moderate", "detail": f"Comfortable for one sitting{tok}.", "band": "good"}
    if t < 40:
        return {"headline": "Sizeable", "detail": f"Dozens of turns — this will take a while{tok}.",
                "band": "warning"}
    if t < 80:
        return {"headline": "Large", "detail": f"Many dozens of turns — expect this to run long{tok}.",
                "band": "warning"}
    return {"headline": "Very large", "detail": f"Well over 80 turns expected{tok} — split it.",
            "band": "critical"}


def _fraction(s: float) -> str:
    for cut, word in ((0.15, "a tenth"), (0.3, "a quarter"), (0.42, "a third"),
                      (0.58, "half"), (0.72, "two thirds"), (0.85, "three quarters")):
        if s < cut:
            return word
    return "most"


def verdict(pp5_p50: float, pp5_p95: float, remaining_pp5: float | None,
            risk_band: str = "green") -> dict:
    """The single line that tells you what to do.

    Risk is part of this, not a separate badge. Telling someone to "just send it"
    beside a 48% chance of being cut off is the kind of contradiction that makes
    the whole tool untrustworthy.
    """
    likely, worst = sessions(pp5_p50), sessions(pp5_p95)
    left = None if remaining_pp5 is None else sessions(remaining_pp5)

    if risk_band == "critical":
        return {"do": "Do not send this as one prompt.",
                "why": ("It will almost certainly stop before it is finished. "
                        "Split it, or cap the turns and give it a way to know when it is done."),
                "band": "critical"}
    if worst > 1.5:
        return {"do": "Split this before you start.",
                "why": ("It is bigger than one session can hold, so you would get cut "
                        "off mid-way and lose your place."),
                "band": "critical"}
    if risk_band == "red":
        return {"do": "Tighten it before sending.",
                "why": ("Roughly even odds of being interrupted. A turn cap and a clear "
                        "finish condition are what move this into the safe zone."),
                "band": "serious"}
    if left is not None and worst > left:
        return {"do": "Not enough left in this window.",
                "why": "Wait for the reset, or split it so the first part fits.",
                "band": "serious"}
    if risk_band == "amber" or (left is not None and likely <= left < worst):
        return {"do": "Fine to send, but set a turn cap.",
                "why": "The typical run fits; a bad one would not.",
                "band": "warning"}
    if worst < 0.2:
        return {"do": "Just send it.",
                "why": "Even if it goes long it barely dents your limit.",
                "band": "good"}
    return {"do": "Safe to send now.",
            "why": "Even the worst case fits in what is left of this window.",
            "band": "good"}


def verdict_generic(turns_p95: float, risk_band: str = "green") -> dict:
    """verdict(), for a model with no Claude plan window to compare against.
    Risk band and expected turn count still apply to any agent loop — only
    the "does it fit in what's left of this window" branch doesn't, so it's
    dropped rather than answered with a number that doesn't mean anything here.
    """
    if risk_band == "critical":
        return {"do": "Do not send this as one prompt.",
                "why": ("It will almost certainly stop before it is finished. "
                        "Split it, or cap the turns and give it a way to know when it is done."),
                "band": "critical"}
    if turns_p95 > 60:
        return {"do": "Split this before you start.",
                "why": "This runs long enough that something will likely interrupt it partway.",
                "band": "critical"}
    if risk_band == "red":
        return {"do": "Tighten it before sending.",
                "why": ("Roughly even odds of being interrupted. A turn cap and a clear "
                        "finish condition are what move this into the safe zone."),
                "band": "serious"}
    if risk_band == "amber":
        return {"do": "Fine to send, but set a turn cap.",
                "why": "The typical run fits; a bad one would not.",
                "band": "warning"}
    if turns_p95 < 5:
        return {"do": "Just send it.",
                "why": "Short enough that this is very unlikely to run into trouble.",
                "band": "good"}
    return {"do": "Safe to send now.",
            "why": "Nothing here points to a likely interruption.",
            "band": "good"}


def _risk_context(worst_band: str, risk_band: str) -> str:
    """Bridges a real, recurring point of confusion: the worst-case *size*
    (session-fraction or turn-count) and the *risk* percentage are computed by
    two different mechanisms — size.py/loop_budget measures how much of a
    window a run could use, the estimator's logistic score measures whether
    anything stops it from running indefinitely (mainly: no turn cap, no
    acceptance criteria, open-ended wording — see estimator.py). A prompt can
    legitimately be small AND likely to be cut off at the same time — small
    doesn't buy safety when nothing bounds the loop — but showing "Tiny" next
    to "expect to be cut off" with no bridge reads as a contradiction rather
    than two true, independent facts. Only fires for that specific mismatch;
    silent otherwise so it never adds noise to a run that quietly agrees.
    """
    if worst_band in ("good", "warning") and risk_band in ("red", "critical"):
        return ("Small and risky at the same time — the size is fine, but nothing "
                "here caps how long it can run. A turn cap and a clear finish "
                "condition are what actually lower the risk.")
    return ""


def risk_sentence(p: float, band: str) -> str:
    """What the risk percentage actually predicts."""
    pct = int(round((p or 0) * 100))
    if band == "green":
        return f"Unlikely to be interrupted ({pct}%)."
    if band == "amber":
        return f"Might get interrupted before it finishes ({pct}% chance)."
    if band == "red":
        return f"Good chance you get cut off part-way ({pct}%)."
    return f"Expect to be cut off before this finishes ({pct}%)."


def money(usd: float, on_plan: bool = True) -> str:
    """Dollars are a size, not a bill, when you are on a subscription."""
    if usd is None:
        return "—"
    amount = "<$0.01" if 0 < usd < 0.01 else f"${usd:,.2f}"
    return f"{amount} of work" if on_plan else amount


def window_left(remaining_pp5: float | None, seconds_to_reset: float | None) -> str:
    if remaining_pp5 is None:
        return "No reading yet — sync from Claude to see what is left."
    s = sessions(remaining_pp5)
    if s <= 0.02:
        return "This window is spent. Wait for the reset."
    hrs = "" if not seconds_to_reset else f", back to full in {seconds_to_reset / 3600:.0f}h"
    return f"About {_fraction(s)} of this session left{hrs}."


def explain(est: dict, remaining_pp5: float | None = None) -> dict:
    """Everything the Plan page needs to speak like a person.

    Branches on vendor: a Claude plan window only exists for Anthropic
    models, so a GPT/Gemini/local estimate speaks in turns and tokens instead
    of "sessions" — showing a session-percent figure for a model that isn't
    billed against that window would just be confusing, not merely secondary.
    """
    vendor = ((est.get("spec") or {}).get("vendor") or "anthropic")
    p50, p95 = est.get("pp5_p50", 0), est.get("pp5_p95", 0)
    b50 = est.get("budget_p50") or {}
    turns_p50, turns_p95 = est.get("turns_p50", 0), est.get("turns_p95", 0)

    if vendor == "anthropic":
        sz, worst = size(p50), size(p95)
        vd = verdict(p50, p95, remaining_pp5, est.get("risk_band", "green"))
        left = window_left(remaining_pp5, None)
    else:
        total_tokens = b50.get("total_tokens")
        sz = size_generic(turns_p50, total_tokens)
        worst = size_generic(turns_p95, (est.get("budget_p95") or {}).get("total_tokens"))
        vd = verdict_generic(turns_p95, est.get("risk_band", "green"))
        left = ""

    return {
        "size": sz,
        "worst": worst,
        "verdict": vd,
        "risk": risk_sentence(est.get("risk", 0), est.get("risk_band", "green")),
        "risk_context": _risk_context(worst["band"], est.get("risk_band", "green")),
        "cost": money(est.get("cost_p50")),
        "cost_worst": money(est.get("cost_p95")),
        "turns": (f"About {turns_p50:.0f} back-and-forth steps, "
                  f"up to {turns_p95:.0f} if it goes badly."),
        "context": _context_line(b50),
        "left": left,
    }


def _context_line(b: dict) -> str:
    if not b:
        return ""
    pct = b.get("context_pct", 0)
    if b.get("compactions"):
        return (f"The conversation outgrows this model's memory — it would need "
                f"to summarise and restart {b['compactions']} time"
                f"{'' if b['compactions'] == 1 else 's'}, which costs extra.")
    if pct > 75:
        return f"Fills about {pct:.0f}% of this model's memory — close to the edge."
    return f"Uses about {pct:.0f}% of this model's memory. Plenty of room."
