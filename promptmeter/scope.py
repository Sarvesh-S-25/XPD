"""Scope extraction — how big is this actually, from the words alone.

The task class says *what kind* of work it is. Scope says *how much*. Without it
"build a todo app" and "build a food delivery app with payments, driver tracking,
a restaurant dashboard and a React Native client" get identical estimates, which
is plainly wrong.

Everything here is regex and counting. No model call.
"""
from __future__ import annotations

import math
import re

SUBSYSTEMS = {
    "auth":          r"\b(auth|authentication|login|sign[- ]?up|oauth|sso|jwt|permission|role)\b",
    "payments":      r"\b(payment|checkout|billing|stripe|razorpay|paypal|invoice|subscription|wallet)\b",
    "search":        r"\b(search|filter|autocomplete|elasticsearch|full[- ]text)\b",
    "realtime":      r"\b(real[- ]?time|live|tracking|websocket|socket\.io|push|streaming)\b",
    "maps":          r"\b(map|maps|geo|location|gps|route|distance|delivery address)\b",
    "notifications": r"\b(notification|email|sms|twilio|sendgrid|push notification|alert)\b",
    "admin":         r"\b(admin|dashboard|back[- ]?office|analytics|report(ing)?|metrics)\b",
    "chat":          r"\b(chat|messaging|comment|thread|inbox)\b",
    "media":         r"\b(upload|image|photo|video|file storage|s3|cdn|thumbnail)\b",
    "cart":          r"\b(cart|basket|order|checkout flow|inventory|catalog|menu)\b",
    "reviews":       r"\b(review|rating|star|feedback)\b",
    "scheduling":    r"\b(schedul|booking|reservation|calendar|slot|availability)\w*",
    "recommend":     r"\b(recommend|personali[sz]|ranking|feed algorithm|ml model)\w*",
    "infra":         r"\b(docker|kubernetes|ci/cd|deploy|terraform|microservice|queue|kafka|redis)\b",
}

PLATFORMS = {
    "web":     r"\b(web|website|browser|react|vue|angular|next\.?js|frontend)\b",
    "mobile":  r"\b(mobile|android|ios|react[- ]native|flutter|swift|kotlin|app store)\b",
    "desktop": r"\b(desktop|electron|tauri|windows app|mac app)\b",
    "api":     r"\b(api|rest|graphql|endpoint|backend|server)\b",
    "cli":     r"\b(cli|command[- ]line|terminal tool)\b",
}

INTEGRATIONS = re.compile(
    r"\b(stripe|razorpay|paypal|twilio|sendgrid|firebase|supabase|auth0|okta|aws|gcp|azure|"
    r"s3|mapbox|google maps|openai|anthropic|slack|github|jira|shopify|plaid)\b", re.I)

# Domain nouns that imply a distinct data model / table / screen.
ENTITIES = re.compile(
    r"\b(user|customer|driver|rider|restaurant|vendor|merchant|seller|buyer|order|item|"
    r"product|menu|dish|cart|payment|invoice|address|review|rating|coupon|promo|"
    r"delivery|shipment|booking|reservation|ticket|message|notification|category|tag|"
    r"account|profile|session|role|team|project|task|document|report)s?\b", re.I)

BULLET = re.compile(r"^\s*(?:[-*•]|\(?\d{1,2}[.)])\s+", re.M)

CLONE_OF = re.compile(
    r"\b(like|similar to|clone of|version of|competitor to)\s+"
    r"(zomato|swiggy|uber|ubereats|doordash|airbnb|instagram|twitter|x|facebook|amazon|"
    r"netflix|spotify|slack|notion|trello|shopify|stripe|linkedin|tiktok|youtube|whatsapp)\b", re.I)

# A named clone implies scope the prompt never spells out.
CLONE_SUBSYSTEMS = 5

# Reference scope for each class — the "typical" project its prior was written for.
REFERENCE = {
    "build_app":          2.6,
    "refactor":           2.0,
    "multi_file_feature": 1.8,
    "research_report":    1.4,
    "data_task":          1.3,
    "single_file_edit":   1.1,
    "qa_explain":         1.0,
}
DEFAULT_REFERENCE = 1.8
MIN_MULT, MAX_MULT = 0.30, 3.20


def extract(prompt: str) -> dict:
    """Count what the prompt actually asks for."""
    subs = sorted(k for k, pat in SUBSYSTEMS.items() if re.search(pat, prompt, re.I))
    plats = sorted(k for k, pat in PLATFORMS.items() if re.search(pat, prompt, re.I))
    integrations = sorted({m.group(0).lower() for m in INTEGRATIONS.finditer(prompt)})
    entities = sorted({m.group(0).lower().rstrip("s") for m in ENTITIES.finditer(prompt)})
    bullets = len(BULLET.findall(prompt))
    clone = CLONE_OF.search(prompt)

    n_subs = len(subs)
    implied = 0
    if clone and n_subs < CLONE_SUBSYSTEMS:
        implied = CLONE_SUBSYSTEMS - n_subs      # "like zomato" carries unstated scope

    return {
        "subsystems": subs,
        "implied_subsystems": implied,
        "clone_of": clone.group(2).lower() if clone else None,
        "platforms": plats,
        "integrations": integrations,
        "entities": entities[:12],
        "bullets": bullets,
        "words": len(prompt.split()),
    }


def score(f: dict) -> float:
    """Raw scope score. 1.0 is a bare request with no stated scope."""
    return (
        1.0
        + 0.30 * (len(f["subsystems"]) + f["implied_subsystems"])
        + 0.25 * max(0, len(f["platforms"]) - 1)
        + 0.08 * len(f["entities"])
        + 0.12 * min(f["bullets"], 12)
        + 0.15 * len(f["integrations"])
    )


def multiplier(prompt: str, task_class: str) -> dict:
    """How much bigger or smaller than the class prior is this specific ask?"""
    f = extract(prompt)
    s = score(f)
    ref = REFERENCE.get(task_class, DEFAULT_REFERENCE)
    raw = s / ref
    # Compress the extremes — scope evidence is suggestive, not proof.
    m = max(MIN_MULT, min(MAX_MULT, raw ** 0.75))
    return {
        "multiplier": round(m, 3),
        "score": round(s, 2),
        "reference": ref,
        "features": f,
        "drivers": _drivers(f),
    }


def _drivers(f: dict) -> list[dict]:
    out = []
    if f["clone_of"]:
        out.append({"label": f'“like {f["clone_of"]}” implies a full product',
                    "weight": 0.30 * (len(f["subsystems"]) + f["implied_subsystems"])})
    if f["subsystems"]:
        out.append({"label": f'{len(f["subsystems"])} subsystems: ' + ", ".join(f["subsystems"][:5]),
                    "weight": 0.30 * len(f["subsystems"])})
    if len(f["platforms"]) > 1:
        out.append({"label": f'{len(f["platforms"])} platforms: ' + ", ".join(f["platforms"]),
                    "weight": 0.25 * (len(f["platforms"]) - 1)})
    if f["entities"]:
        out.append({"label": f'{len(f["entities"])} data entities', "weight": 0.08 * len(f["entities"])})
    if f["bullets"]:
        out.append({"label": f'{f["bullets"]} listed requirements', "weight": 0.12 * f["bullets"]})
    if f["integrations"]:
        out.append({"label": "integrations: " + ", ".join(f["integrations"][:4]),
                    "weight": 0.15 * len(f["integrations"])})
    out.sort(key=lambda d: -d["weight"])
    return [{"label": d["label"], "weight": round(d["weight"], 2)} for d in out[:5]]


def from_plan(items: list[dict], task_class: str) -> dict:
    """Scope derived from a drafted plan instead of from keywords.

    A planner gives us the one thing regex cannot: the work it did not say out
    loud. We use the item count and the planner's own complexity rating, and
    still convert to turns with our own numbers.
    """
    n = max(1, len(items))
    complexity = [float(i.get("complexity") or 3) for i in items]
    avg_c = sum(complexity) / len(complexity)
    # An item of average complexity (3) is worth one reference unit of scope.
    s = 1.0 + 0.30 * n * (avg_c / 3.0)
    ref = REFERENCE.get(task_class, DEFAULT_REFERENCE)
    m = max(MIN_MULT, min(MAX_MULT, (s / ref) ** 0.75))
    return {
        "multiplier": round(m, 3),
        "score": round(s, 2),
        "reference": ref,
        "items": n,
        "avg_complexity": round(avg_c, 2),
        "drivers": [{"label": f"{n} planned steps, average complexity {avg_c:.1f}/5",
                     "weight": round(0.30 * n * (avg_c / 3.0), 2)}],
    }
