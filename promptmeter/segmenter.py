"""Deterministic prompt segmentation into a dependency graph. No model calls.

Four passes:
  1. cut the prompt into work items using structure, then imperative sentences
  2. extract the artifacts each item produces or touches
  3. infer edges (artifact reuse, ordering cues, pronoun back-reference)
  4. topologically sort into stages; independent items in a stage can run together
"""
from __future__ import annotations

import re

from .estimator import IMPERATIVES, PATHS

ORDER_CUES = re.compile(
    r"^\s*(then|after (that|this)|next|once .* (is )?(done|ready|built)|finally|"
    r"afterwards|subsequently|using (the|that)|based on (the|that|it))\b", re.I)

BACKREF = re.compile(r"\b(it|its|that|this|them|these|those|the above|the same)\b", re.I)

BULLET = re.compile(r"^\s*(?:[-*•]|\(?\d{1,2}[.)])\s+")

QUOTED = re.compile(r"[\"'`]([A-Za-z][\w .\-/]{2,40})[\"'`]")
IDENT = re.compile(r"\b([A-Z][a-zA-Z0-9]{2,}(?:Service|Controller|Model|Component|View|Manager|Handler|Store|Client|Page|Form|Table|Chart))\b")

DELIVERABLE_NOUN = re.compile(
    r"\b(script|module|component|endpoint|api|page|dashboard|report|document|doc|"
    r"spreadsheet|deck|slide|schema|migration|test|suite|readme|config|"
    r"function|class|cli|server|database|table|chart|diagram|plan)\b", re.I)

MAX_ITEMS = 24


def _structural_items(prompt: str) -> list[str]:
    lines = prompt.splitlines()
    bulleted = [l for l in lines if BULLET.match(l)]
    if len(bulleted) >= 2:
        items, cur = [], None
        for l in lines:
            if BULLET.match(l):
                if cur:
                    items.append(cur.strip())
                cur = BULLET.sub("", l)
            elif cur is not None and l.strip():
                cur += " " + l.strip()
        if cur:
            items.append(cur.strip())
        return [i for i in items if i]

    paras = [p.strip() for p in re.split(r"\n\s*\n", prompt) if p.strip()]
    if len(paras) >= 2:
        return paras
    return []


def _sentence_items(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text)
    items, buf = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if IMPERATIVES.search(p) and len(p.split()) >= 4:
            if buf:
                items.append(buf.strip())
            buf = p
        else:
            buf = (buf + " " + p).strip() if buf else p
    if buf:
        items.append(buf.strip())
    return [i for i in items if len(i.split()) >= 3]


def split_items(prompt: str) -> list[str]:
    items = _structural_items(prompt)
    if not items:
        items = _sentence_items(prompt)
    if not items:
        items = [prompt.strip()]

    # merge fragments that carry no action of their own into the previous item
    merged: list[str] = []
    for it in items:
        if merged and not IMPERATIVES.search(it) and len(it.split()) < 12:
            merged[-1] = merged[-1] + " " + it
        else:
            merged.append(it)

    if len(merged) > MAX_ITEMS:
        head, tail = merged[:MAX_ITEMS - 1], merged[MAX_ITEMS - 1:]
        head.append(" ".join(tail))
        merged = head
    return merged


def artifacts_of(text: str) -> list[str]:
    found: list[str] = []
    found += PATHS.findall(text)
    found += [m.group(1) for m in QUOTED.finditer(text)]
    found += [m.group(1) for m in IDENT.finditer(text)]
    seen, out = set(), []
    for a in found:
        k = a.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(a.strip())
    return out[:8]


def title_of(text: str, n: int) -> str:
    """A short human label. Prefer verb + the artifact it acts on."""
    words = text.split()
    verb = IMPERATIVES.search(text)
    arts = artifacts_of(text)
    noun = DELIVERABLE_NOUN.search(text)

    if verb and arts:
        t = f"{verb.group(0).capitalize()} {arts[0]}"
    elif verb and noun:
        t = f"{verb.group(0).capitalize()} the {noun.group(0).lower()}"
    else:
        t = " ".join(words[:7])
    t = t.strip(" .,:;-") or f"Step {n}"
    return t if len(t) <= 42 else t[:39].rstrip() + "…"


def infer_edges(items: list[str], arts: list[list[str]]) -> list[list[int]]:
    """depends_on[i] = indices this item needs first."""
    deps: list[list[int]] = [[] for _ in items]
    produced: dict[str, int] = {}
    for i, alist in enumerate(arts):
        for a in alist:
            produced.setdefault(a.lower(), i)

    for i, text in enumerate(items):
        low = text.lower()
        d = set()
        # 1. references an artifact an earlier item introduced
        for a, owner in produced.items():
            if owner < i and a in low:
                d.add(owner)
        # 2. explicit ordering cue -> depends on immediately previous item
        if i > 0 and ORDER_CUES.match(text):
            d.add(i - 1)
        # 3. back-reference with no other anchor
        if i > 0 and not d and BACKREF.search(" ".join(text.split()[:8])):
            d.add(i - 1)
        deps[i] = sorted(d)
    return deps


def stages(deps: list[list[int]]) -> list[int]:
    """Longest-path depth for each node = its stage. Cycles are broken by index."""
    n = len(deps)
    stage = [0] * n
    for i in range(n):
        best = 0
        for d in deps[i]:
            if 0 <= d < i:
                best = max(best, stage[d] + 1)
        stage[i] = best
    return stage


def segment(prompt: str) -> list[dict]:
    items = split_items(prompt)
    arts = [artifacts_of(t) for t in items]
    deps = infer_edges(items, arts)
    stg = stages(deps)
    return [
        {
            "idx": i,
            "title": title_of(t, i + 1),
            "prompt": t,
            "artifacts": arts[i],
            "depends_on": deps[i],
            "stage": stg[i],
        }
        for i, t in enumerate(items)
    ]


# ---------------------------------------------------------------- savings

def savings(single_cost: float, naive_costs: list[float], routed_costs: list[float],
            final_costs: list[float], n_steps: int) -> dict:
    """Decomposition computed from three real estimates, not invented percentages.

      naive   — same model, full context carried into every step
      routed  — after dropping cheap steps a model tier
      final   — after also pruning each step's context to what it touches

    The difference between the single run and `naive` is the quadratic context
    growth a long agent loop pays and a split avoids. Everything is a real
    estimate, so the numbers reconcile.
    """
    naive = sum(naive_costs)
    routed = sum(routed_costs)
    final = sum(final_costs)
    return {
        "single_run": round(single_cost, 4),
        "naive_split": round(naive, 4),
        "avoided_growth": round(single_cost - naive, 4),
        "routing": round(max(0.0, naive - routed), 4),
        "pruning": round(max(0.0, routed - final), 4),
        "optimised_split": round(final, 4),
        "net_saving": round(single_cost - final, 4),
        "blast_radius_before": round(single_cost, 4),
        "blast_radius_after": round(max(final_costs) if final_costs else single_cost, 4),
        "steps": n_steps,
    }
