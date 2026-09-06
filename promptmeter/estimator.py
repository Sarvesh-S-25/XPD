"""Deterministic estimation and risk scoring. No model calls, ever.

Three jobs:
  1. count input tokens (offline heuristic, self-correcting against observed truth)
  2. predict the uncertain parts (turns, output length) from priors + learned history
  3. score risk as P(this run hits a wall), with named drivers
"""
from __future__ import annotations

import math
import re

from . import db, learning, pricing, scope

# ---------------------------------------------------------------- tokenising

# Anthropic publishes no offline tokenizer. chars/CHARS_PER_TOKEN is the
# fallback; the correction factor is learned from observed usage blocks so the
# estimate converges on this machine's real ratio.
CHARS_PER_TOKEN = 3.6


def correction() -> float:
    return float(db.get_setting("token_correction", 1.0) or 1.0)


def count_tokens(text: str) -> int:
    if not text:
        return 0
    base = len(text) / CHARS_PER_TOKEN
    # code and markup tokenise denser than prose
    symbols = sum(text.count(c) for c in "{}[]()<>/\\|=;:#*_`")
    density = 1.0 + min(0.35, symbols / max(1, len(text)) * 4.0)
    return int(round(base * density * correction()))


def learn_token_ratio(text_chars: int, actual_tokens: int) -> None:
    """Nudge the correction factor toward observed truth (EWMA)."""
    if text_chars <= 0 or actual_tokens <= 0:
        return
    naive = text_chars / CHARS_PER_TOKEN
    if naive <= 0:
        return
    observed = actual_tokens / naive
    cur = correction()
    db.set_setting("token_correction", round(cur * 0.9 + observed * 0.1, 4))


# ---------------------------------------------------------------- task class

# Weighted keyword scoring rather than first-match: a prompt usually contains
# signals for several classes and the strongest should win, not the first listed.
CLASS_SIGNALS = {
    "build_app": [
        (3.0, r"\b(build|create|make|develop|scaffold|ship)\b.{0,45}\b(app|application|web ?app|website|site|platform|system|service|dashboard|extension|cli|game)\b"),
        (1.5, r"\bfrom scratch\b"), (1.2, r"\b(full[- ]stack|end[- ]to[- ]end)\b"),
    ],
    "refactor": [
        (3.0, r"\b(refactor|restructure|modernis\w*|moderniz\w*)\b"),
        (2.0, r"\bmigrat\w*\b"), (1.5, r"\b(rename|replace)\b.{0,30}\b(across|everywhere|all)\b"),
    ],
    "research_report": [
        (2.5, r"\b(research|investigate|survey|compare|benchmark)\b"),
        (2.0, r"\b(write|draft|produce|prepare)\b.{0,25}\b(report|doc|document|article|post|essay|memo|summary|brief|plan)\b"),
        (1.5, r"\b(analys\w*|analyz\w*)\b"),
    ],
    "data_task": [
        (2.5, r"\b(spreadsheet|csv|xlsx|dataset|dataframe)\b"),
        (2.0, r"\b(clean|normalis\w*|normaliz\w*|dedup\w*)\b.{0,20}\b(data|rows|columns|file)\b"),
        (1.5, r"\b(pivot|chart|plot|graph|visuali[sz]\w*)\b"),
    ],
    "multi_file_feature": [
        (2.2, r"\b(implement|integrate|wire up|build out)\b"),
        (2.0, r"\b(endpoint|endpoints|api|route|routes|schema|migration|component|module|backend|frontend|server)\b"),
        (1.6, r"\b(add|write|create|build)\b.{0,30}\b(feature|function|class|test|tests|suite|page|form|handler)\b"),
    ],
    "single_file_edit": [
        (2.2, r"\b(fix|patch|debug|bug|typo)\b"),
        (1.4, r"\b(tweak|adjust|rename|update|change)\b"),
    ],
    "qa_explain": [
        (2.5, r"^\s*(what|why|how|when|where|who|which|is|are|can|does|do|should)\b"),
        (2.0, r"\b(explain|describe|tell me about|walk me through)\b"),
        (1.2, r"\b(summari[sz]e|list out|give me a list)\b"),
        (1.2, r"\?\s*$"),
    ],
}


SCOPE_WIDE = re.compile(
    r"\b(codebase|repo|repository|whole (project|app|thing)|entire (project|app|codebase)|"
    r"all (the )?(files|tests|modules|code)|across the (project|repo|codebase))\b", re.I)

# Classes ordered cheapest to most expensive, for scope escalation.
CLASS_RANK = ["qa_explain", "single_file_edit", "data_task", "research_report",
              "multi_file_feature", "refactor", "build_app"]


def classify(prompt: str) -> str:
    p = prompt.strip()
    best, best_score = "multi_file_feature", 0.0
    for name, sigs in CLASS_SIGNALS.items():
        score = 0.0
        for w, pat in sigs:
            if re.search(pat, p, re.I | re.M):
                score += w
        if score > best_score:
            best, best_score = name, score
    cls = best if best_score >= 1.5 else "multi_file_feature"

    # Scope escalation: "fix everything in the codebase and keep going" reads as a
    # single-file edit by keyword, but it is a repo-wide job. Unbounded language
    # plus a repo-wide noun promotes the class rather than trusting the verb.
    if SCOPE_WIDE.search(p) and len(OPEN_ENDED.findall(p)) >= 1:
        if CLASS_RANK.index(cls) < CLASS_RANK.index("refactor"):
            cls = "refactor"
    return cls


# ---------------------------------------------------------------- risk

OPEN_ENDED = re.compile(
    r"\b(etc\.?|and so on|and more|whatever|anything else|as needed|"
    r"until it works|until done|keep going|iterate|refine|dynamically|"
    r"make it (good|nice|better|fast|robust|clean|production)|"
    r"all of (them|it)|every|everything|any and all)\b", re.I)

ACCEPTANCE = re.compile(
    r"\b(test|tests pass|passes|acceptance|criteria|should (return|output|equal|match)|"
    r"exit code|schema|must (be|have|return)|definition of done|expected output|"
    r"verify|assert)\b", re.I)

FORMAT_NAMED = re.compile(
    r"\b(json|csv|markdown|md|html|pdf|docx|xlsx|pptx|yaml|table|list|diagram|"
    r"\.py|\.js|\.ts|\.tsx|\.java|\.go|\.rs|\.sql)\b", re.I)

PATHS = re.compile(r"[\w./\\-]+\.(?:py|js|jsx|ts|tsx|java|go|rs|rb|php|c|cpp|h|css|html|json|ya?ml|toml|md|sql|sh|bat|txt|csv|xlsx|docx|pdf)\b")

IMPERATIVES = re.compile(
    r"\b(build|create|make|write|add|implement|fix|refactor|delete|remove|update|"
    r"generate|design|test|deploy|install|configure|connect|integrate|migrate|"
    r"analy[sz]e|research|summari[sz]e|convert|export|import|render|draw|plot)\b", re.I)

MULTI_SYSTEM = re.compile(
    r"\b(database|api|frontend|backend|auth|payment|docker|ci/cd|kubernetes|"
    r"aws|gcp|azure|redis|postgres|mysql|mongodb|s3|webhook|oauth)\b", re.I)


def risk_features(prompt: str, *, tools_write: bool = True, tools_bash: bool = True,
                  turn_cap: int | None = None, model: str = "claude-sonnet-5",
                  files_in_scope: int = 0) -> dict:
    words = max(1, len(prompt.split()))
    return {
        "tokens":        count_tokens(prompt),
        "words":         words,
        "verbs":         len(set(m.group(0).lower() for m in IMPERATIVES.finditer(prompt))),
        "paths":         len(set(PATHS.findall(prompt))),
        "systems":       len(set(m.group(0).lower() for m in MULTI_SYSTEM.finditer(prompt))),
        "open_ended":    len(OPEN_ENDED.findall(prompt)),
        "has_criteria":  1 if ACCEPTANCE.search(prompt) else 0,
        "has_format":    1 if FORMAT_NAMED.search(prompt) else 0,
        "no_turn_cap":   0 if turn_cap else 1,
        "write":         1 if tools_write else 0,
        "bash":          1 if tools_bash else 0,
        "opus":          1 if pricing.price_of(model).get("tier") == "opus" else 0,
        "files":         files_in_scope,
    }


# weight, human-readable driver name. Tuned so the four highest-signal features
# (no turn cap, bash+write, no acceptance criteria, open-ended language) dominate.
RISK_WEIGHTS = [
    ("no_turn_cap",  0.85, "No turn cap set"),
    ("bash",         0.40, "Shell access enabled"),
    ("write",        0.25, "File writes enabled"),
    ("open_ended",   0.45, "Open-ended wording"),
    ("systems",      0.22, "Multiple external systems"),
    ("verbs",        0.13, "Many distinct actions"),
    ("paths",        0.06, "Many files referenced"),
    ("opus",         0.30, "Opus (spends window fastest)"),
]


def risk_score(feats: dict, headroom_ratio: float = 1.0,
               turns_p95: float = 12.0) -> tuple[float, str, list]:
    """Return (probability 0..1, band, drivers).

    headroom_ratio = predicted P95 cost / remaining budget. Above 1.0 the P95
    case does not fit, and risk climbs — this is what makes the number mean
    *probability of hitting a wall* rather than a vibe.

    Runaway features (no turn cap, shell access, vague wording) are scaled by how
    much room the task has to run away in. A two-turn question cannot overrun no
    matter how loosely it is worded, so those features are damped to near zero
    there and reach full weight on genuinely long-running work.
    """
    scale = min(1.0, max(0.12, math.log1p(max(1.0, turns_p95)) / math.log1p(40.0)))
    z = -2.2
    contrib = []
    for key, w, label in RISK_WEIGHTS:
        v = float(feats.get(key, 0) or 0)
        if key in ("verbs", "paths", "systems", "open_ended"):
            v = math.log1p(v) * 1.6
        c = w * v * scale
        if c > 0.01:
            contrib.append((label, c))
        z += c
    if not feats.get("has_criteria"):
        z += 0.55 * scale
        contrib.append(("No acceptance criteria", 0.55 * scale))
    if not feats.get("has_format"):
        z += 0.22 * scale
        contrib.append(("No output format named", 0.22 * scale))

    # Budget pressure. Capped so a wildly oversized job does not saturate every
    # score at 99% — past "definitely does not fit" the extra information is in
    # the cost band, not the probability.
    if headroom_ratio > 1.0:
        bump = min(2.2, 1.5 * math.log(headroom_ratio))
        z += bump
        contrib.append(("Worst case exceeds what is left in the window", bump))
    elif headroom_ratio < 0.5:
        z -= min(1.0, 0.8 * math.log(0.5 / max(headroom_ratio, 0.02)))

    p = 1.0 / (1.0 + math.exp(-z))
    band = "green" if p < 0.10 else "amber" if p < 0.30 else "red" if p < 0.60 else "critical"
    contrib.sort(key=lambda t: -t[1])
    drivers = [{"label": l, "weight": round(c, 3)} for l, c in contrib[:4]]
    return round(p, 4), band, drivers


# ---------------------------------------------------------------- estimate

def estimate(prompt: str, *, model: str = "claude-sonnet-5", task_class: str | None = None,
             turn_cap: int | None = None, tools_write: bool = True, tools_bash: bool = True,
             base_context: int = 12000, remaining_pp5: float = 100.0,
             remaining_pp7: float = 100.0, turn_scale: float = 1.0,
             scope_override: dict | None = None,
             effort: str = pricing.DEFAULT_EFFORT) -> dict:
    """Full pre-flight estimate for one prompt.

    turn_scale < 1 marks this as one segment of a larger prompt: the task class is
    the same but the work is a fraction of it, so the turn prediction is scaled
    down (with a floor — every step still pays a fixed start-up cost).
    """
    from . import meter

    tc = task_class or classify(prompt)
    prof = learning.profile(tc, model)          # priors blended with observed history
    prompt_tokens = count_tokens(prompt)
    base = base_context + prompt_tokens

    t50, t95 = prof["turns"]

    # Scope: how big is *this* ask versus the typical member of its class.
    sc = scope_override or scope.multiplier(prompt, tc)
    t50 *= sc["multiplier"]
    t95 *= sc["multiplier"]

    if turn_scale < 1.0:
        t50 = max(1.5, t50 * turn_scale)
        t95 = max(3.0, t95 * turn_scale)
    if turn_cap:
        t50, t95 = min(t50, turn_cap), min(t95, turn_cap)
    o50, o95 = prof["out"]
    growth = prof["growth"]

    b50 = pricing.loop_budget(model, base, t50, growth, o50, effort=effort)
    b95 = pricing.loop_budget(model, base, t95, growth, o95, effort=effort)
    c50, c95 = b50["cost"], b95["cost"]

    # Calibrate the P95 cost against this task class's own predicted-vs-actual
    # history (PLS-DO A1) once there is enough of it; below that the model's
    # own uncertainty spread (c95/c50) is unchanged.
    prior_ratio = (c95 / c50) if c50 > 0 else 1.0
    cal = learning.calibration_factor(tc, prior_ratio)
    if cal["calibrated"]:
        c95 = round(c50 * cal["ratio"], 4)

    alpha5, alpha7 = meter.alphas()
    pp5_50, pp5_95 = c50 * alpha5, c95 * alpha5
    pp7_50, pp7_95 = c50 * alpha7, c95 * alpha7

    head5 = pp5_95 / max(remaining_pp5, 0.5)
    head7 = pp7_95 / max(remaining_pp7, 0.5)
    headroom = max(head5, head7)

    feats = risk_features(prompt, tools_write=tools_write, tools_bash=tools_bash,
                          turn_cap=turn_cap, model=model)
    p, band, drivers = risk_score(feats, headroom, t95)

    return {
        "task_class": tc,
        "task_label": pricing.priors().get(tc, {}).get("label", tc),
        "model": model,
        "prompt_tokens": prompt_tokens,
        "base_input": base,
        "turns_p50": round(t50, 1), "turns_p95": round(t95, 1),
        "out_p50": int(o50), "out_p95": int(o95),
        "cost_p50": round(c50, 4), "cost_p95": round(c95, 4),
        "pp5_p50": round(pp5_50, 2), "pp5_p95": round(pp5_95, 2),
        "pp7_p50": round(pp7_50, 2), "pp7_p95": round(pp7_95, 2),
        "risk": p, "risk_band": band, "risk_drivers": drivers,
        "scope": sc,
        "budget_p50": b50, "budget_p95": b95,
        "effort": effort,
        "spec": {k: pricing.spec(model)[k] for k in
                 ("vendor", "label", "context", "max_output", "thinking")},
        "headroom_ratio": round(headroom, 3),
        "remaining_pp5": round(remaining_pp5, 1),
        "remaining_pp7": round(remaining_pp7, 1),
        "samples": prof["n"],
        "confidence": prof["confidence"],
        "calibration": cal,
        "features": feats,
    }


def should_split(est: dict) -> tuple[bool, str]:
    """The 'split only if it helps' rule. Returns (split, human reason)."""
    if est["risk_band"] in ("red", "critical"):
        return True, f"Risk is {est['risk_band']} ({est['risk']*100:.0f}% chance of hitting a wall)."
    if est["pp5_p95"] > est["remaining_pp5"]:
        return True, (f"Worst case needs {est['pp5_p95']:.0f}% of the 5-hour window "
                      f"but only {est['remaining_pp5']:.0f}% is left.")
    if est["turns_p95"] > 45:
        return True, f"Worst case runs ~{est['turns_p95']:.0f} turns — long enough to cross a window."
    if est["features"]["verbs"] >= 6 and est["features"]["paths"] >= 3:
        return True, "The prompt contains many separate actions across several files."
    return False, "Fits comfortably in one run — no split needed."
