"""Learn real usage from past runs, per task class and per model.

Everything here reads rows PromptMeter already recorded. There is no model call
and no network. Priors are blended with observation using shrinkage, so a class
with two samples stays close to the prior and one with fifty is driven by data.
"""
from __future__ import annotations

from . import db, pricing

SHRINK_K = 6.0          # samples at which observation and prior weigh equally
DEFAULT_GROWTH = 2600   # tokens added to context per turn, before observation


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    i = q * (len(s) - 1)
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def observations(task_class: str, model: str | None = None) -> dict:
    """Observed turns and output size per completed step.

    Reads BOTH ledgers: iterations you logged by hand, and turns the watcher
    captured automatically while a step was running. Previously only the manual
    ones counted, so the app could watch you do fifty real runs and still say
    "prior only".
    """
    recs = db.rows(
        """
        SELECT s.id AS sid, s.model AS model, s.started_at, s.ended_at,
               COUNT(i.id) AS turns,
               AVG(i.out_tokens) AS out_avg
        FROM steps s
        JOIN projects p ON p.id = s.project_id
        LEFT JOIN iterations i ON i.step_id = s.id
        WHERE p.task_class = ? AND s.status IN ('done','failed') AND p.is_demo = 0
        GROUP BY s.id
        """,
        (task_class,),
    )
    if model:
        same = [r for r in recs if r["model"] == model]
        if len(same) >= 3:
            recs = same

    turns, outs, n = [], [], 0
    for r in recs:
        t = float(r["turns"] or 0)
        o = float(r["out_avg"] or 0)
        if (not t or not o) and r["started_at"]:
            auto = _auto_for(r["started_at"], r["ended_at"])
            t = t or auto["turns"]
            o = o or auto["out_avg"]
        if t:
            turns.append(t)
            n += 1
        if o:
            outs.append(o)
    return {"n": n, "turns": turns, "outs": outs}


def _auto_for(start, end) -> dict:
    """Watcher turns inside a step's running window."""
    if not start:
        return {"turns": 0.0, "out_avg": 0.0}
    end = float(end or start) + (0 if end else 6 * 3600)
    try:
        r = db.row(
            """SELECT COUNT(*) AS n, AVG(out_tokens) AS o
               FROM turns WHERE ts >= ? AND ts <= ?""", (float(start), float(end)))
    except Exception:                                    # noqa: BLE001 table absent
        return {"turns": 0.0, "out_avg": 0.0}
    return {"turns": float((r or {}).get("n") or 0),
            "out_avg": float((r or {}).get("o") or 0)}


def growth_estimate(task_class: str) -> float:
    """Median context growth per turn.

    Prefers the watcher's turns, which exist without anyone logging anything;
    falls back to logged iterations.
    """
    deltas = []
    try:
        rows = db.rows(
            """SELECT session_id, ts, in_tokens + cache_read + cache_1h + cache_5m AS total
               FROM turns ORDER BY session_id, ts""")
        prev_sess, prev_total = None, None
        for r in rows:
            if r["session_id"] != prev_sess:
                prev_sess, prev_total = r["session_id"], r["total"]
                continue
            if prev_total is not None and r["total"] > prev_total:
                deltas.append(r["total"] - prev_total)
            prev_total = r["total"]
    except Exception:                                    # noqa: BLE001
        pass

    if len(deltas) < 5:
        recs = db.rows(
            """SELECT i.step_id, i.n, i.in_tokens + i.cache_read + i.cache_write AS total
               FROM iterations i JOIN projects p ON p.id = i.project_id
               WHERE p.task_class = ? AND p.is_demo = 0
               ORDER BY i.step_id, i.n""", (task_class,))
        prev_step, prev_total = None, None
        for r in recs:
            if r["step_id"] != prev_step:
                prev_step, prev_total = r["step_id"], r["total"]
                continue
            if prev_total is not None and r["total"] > prev_total:
                deltas.append(r["total"] - prev_total)
            prev_total = r["total"]

    if not deltas:
        return float(DEFAULT_GROWTH)
    return max(400.0, _quantile(deltas, 0.5))


def profile(task_class: str, model: str = "claude-sonnet-5") -> dict:
    """Blended prediction profile for a (task class, model) pair."""
    pri = pricing.priors().get(task_class) or pricing.priors()["multi_file_feature"]
    pt50, pt95 = float(pri["turns"][0]), float(pri["turns"][1])
    po50, po95 = float(pri["out"][0]), float(pri["out"][1])

    obs = observations(task_class, model)
    n = obs["n"]
    w = n / (n + SHRINK_K) if n else 0.0

    if obs["turns"]:
        ot50, ot95 = _quantile(obs["turns"], 0.5), _quantile(obs["turns"], 0.95)
    else:
        ot50, ot95 = pt50, pt95
    if obs["outs"]:
        oo50, oo95 = _quantile(obs["outs"], 0.5), _quantile(obs["outs"], 0.95)
    else:
        oo50, oo95 = po50, po95

    t50 = pt50 * (1 - w) + ot50 * w
    t95 = max(t50 * 1.15, pt95 * (1 - w) + ot95 * w)
    o50 = po50 * (1 - w) + oo50 * w
    o95 = max(o50 * 1.2, po95 * (1 - w) + oo95 * w)

    conf = "prior only" if n == 0 else "learning" if n < 8 else "measured"
    return {
        "turns": (t50, t95),
        "out": (o50, o95),
        "growth": growth_estimate(task_class),
        "n": n,
        "weight": round(w, 3),
        "confidence": conf,
    }


def model_table() -> list[dict]:
    """Per-model observed reality, for the History screen.

    Reads BOTH ledgers, the same way observations() does: logged iterations
    contribute directly; a step with none logged falls back to the watcher's
    turns captured inside that step's running window (same OR-not-both rule
    as observations()/_auto_for, so nothing is double counted). Previously
    this read `iterations` only, so a model driven entirely through the
    automatic watcher showed $0 spent here even with real turns recorded
    (PLS-DO A5).
    """
    logged = db.rows(
        """
        SELECT model,
               COUNT(*)                AS iters,
               SUM(in_tokens)          AS in_tokens,
               SUM(out_tokens)         AS out_tokens,
               SUM(cache_read)         AS cache_read,
               SUM(cache_write)        AS cache_write,
               SUM(cost_usd)           AS cost,
               SUM(pp5_delta)          AS pp5
        FROM iterations WHERE model <> '' AND is_demo = 0 GROUP BY model
        """
    )
    totals: dict[str, dict] = {}
    for r in logged:
        totals[r["model"]] = {
            "iters": r["iters"] or 0, "cost": r["cost"] or 0.0,
            "in_tokens": r["in_tokens"] or 0, "out_tokens": r["out_tokens"] or 0,
            "cache_read": r["cache_read"] or 0, "cache_write": r["cache_write"] or 0,
            "pp5": r["pp5"] or 0.0,
        }

    unlogged_steps = db.rows(
        """SELECT s.id, s.model, s.started_at, s.ended_at
           FROM steps s
           WHERE s.model <> '' AND s.started_at IS NOT NULL AND s.is_demo = 0
             AND NOT EXISTS (SELECT 1 FROM iterations i WHERE i.step_id = s.id)"""
    )
    for s in unlogged_steps:
        end = float(s["ended_at"] or (float(s["started_at"]) + 6 * 3600))
        try:
            r = db.row(
                """SELECT COUNT(*) AS n, COALESCE(SUM(in_tokens),0) AS i,
                          COALESCE(SUM(out_tokens),0) AS o,
                          COALESCE(SUM(cache_read),0) AS cr,
                          COALESCE(SUM(cache_1h+cache_5m),0) AS cw,
                          COALESCE(SUM(cost_usd),0) AS c
                   FROM turns WHERE ts >= ? AND ts <= ?""",
                (float(s["started_at"]), end)) or {}
        except Exception:                                    # noqa: BLE001 table absent
            continue
        if not r.get("n"):
            continue
        t = totals.setdefault(s["model"], {
            "iters": 0, "cost": 0.0, "in_tokens": 0, "out_tokens": 0,
            "cache_read": 0, "cache_write": 0, "pp5": 0.0,
        })
        t["iters"] += int(r["n"] or 0)
        t["cost"] += float(r["c"] or 0)
        t["in_tokens"] += int(r["i"] or 0)
        t["out_tokens"] += int(r["o"] or 0)
        t["cache_read"] += int(r["cr"] or 0)
        t["cache_write"] += int(r["cw"] or 0)
        # turns carries no pp5_delta equivalent — auto-only steps add 0 there,
        # same honest gap as everywhere else this ledger lacks a field.

    out = []
    for model, t in totals.items():
        label = pricing.price_of(model).get("label", model)
        cost = t["cost"]
        out.append({
            "model": model, "label": label,
            "iters": t["iters"], "cost": round(cost, 4),
            "in_tokens": t["in_tokens"], "out_tokens": t["out_tokens"],
            "cache_read": t["cache_read"], "cache_write": t["cache_write"],
            "pp5": round(t["pp5"], 2),
            "avg_out": int(t["out_tokens"] / t["iters"]) if t["iters"] else 0,
            "pp_per_dollar": round(t["pp5"] / cost, 2) if cost > 0 else None,
        })
    out.sort(key=lambda r: r["cost"], reverse=True)
    return out


def accuracy() -> list[dict]:
    """Predicted vs actual, one point per completed step."""
    recs = db.rows(
        """
        SELECT s.id, s.title, s.est_cost_p50, s.est_cost_p95, s.model, s.ended_at,
               p.name AS project, p.task_class AS task_class,
               (SELECT COALESCE(SUM(cost_usd),0) FROM iterations WHERE step_id = s.id) AS logged,
               s.started_at, s.ended_at
        FROM steps s JOIN projects p ON p.id = s.project_id
        WHERE s.status = 'done' AND p.is_demo = 0 ORDER BY s.ended_at DESC LIMIT 200
        """
    )
    out = []
    for r in recs:
        actual = r["logged"] or 0.0
        if actual <= 0 and r["started_at"]:
            try:
                actual = float(db.scalar(
                    "SELECT COALESCE(SUM(cost_usd),0) FROM turns WHERE ts>=? AND ts<=?",
                    (float(r["started_at"]), float(r["ended_at"] or r["started_at"])), 0.0) or 0.0)
            except Exception:                            # noqa: BLE001
                actual = 0.0
        if actual <= 0:
            continue
        out.append({
            "id": r["id"], "title": r["title"], "project": r["project"],
            "task_class": r["task_class"],
            "model": r["model"], "ended_at": r["ended_at"],
            "predicted": round(r["est_cost_p50"] or 0, 4),
            "p95": round(r["est_cost_p95"] or 0, 4),
            "actual": round(actual, 4),
            "within_band": bool((r["est_cost_p95"] or 0) >= actual),
        })
    return out


def calibration_factor(task_class: str, prior_ratio: float) -> dict:
    """A P95:P50 cost-ratio, calibrated from this project's own predicted-vs-
    actual history (PLS-DO A1), blended with the model's own uncertainty
    spread (`prior_ratio` = cost_p95/cost_p50 from pricing.loop_budget) using
    the same shrinkage weight everything else here uses, `n/(n+SHRINK_K)`.

    The nonconformity score is one-sided — `actual/predicted_p50` — because
    what matters for a budget warning is the upper tail: how much worse than
    the P50 guess does it actually get. Below 8 real points for this task
    class (the same cutoff `profile()` already uses for its "measured" label)
    this returns `calibrated: False` and the caller keeps today's band as-is.

    This is an honest, labelled approximation, not textbook split-conformal
    prediction — that needs n>=19 sampled points for guaranteed 95% coverage,
    and this app sees dozens of projects per class, not thousands. Report it
    as "calibrated from your own N past runs," never as "95% means 95%."
    """
    ratios = [r["actual"] / r["predicted"] for r in accuracy()
              if r.get("task_class") == task_class and r["predicted"] > 0]
    n = len(ratios)
    if n < 8:
        return {"ratio": prior_ratio, "n": n, "calibrated": False}
    w = n / (n + SHRINK_K)
    observed_p95 = _quantile(ratios, 0.95)
    blended = prior_ratio * (1 - w) + observed_p95 * w
    blended = max(1.0, blended)          # p95 cannot fall below p50 by definition
    return {"ratio": round(blended, 3), "n": n, "calibrated": True,
            "observed_p95": round(observed_p95, 3), "weight": round(w, 3)}
