"""Turn a prompt into a plan: estimate, decide whether to split, build the steps."""
from __future__ import annotations

import json
import time

from . import (db, estimator, meter, oracles, plain, pricing, providers,
               scope, segmenter)

TIER_ORDER = ["haiku", "sonnet", "opus"]

CHEAP = ("readme", "rename", "format", "boilerplate", "scaffold", "comment",
         "docstring", "typo", "lint", "config", "changelog", "list ", "summar")
HARD = ("architect", "design", "algorithm", "optimi", "debug", "root cause",
        "security", "concurren", "migrate", "refactor")


def sibling(model: str, tier: str) -> str:
    """The cheapest model of a given tier from the SAME vendor.

    Routing used to jump to a hardcoded Claude model, so picking Gemini and
    letting the planner downgrade a step silently switched you to Anthropic —
    wrong prices, wrong context window, wrong bill.
    """
    vendor = pricing.spec(model).get("vendor")
    same = [(mid, m) for mid, m in pricing.models().items()
            if m.get("vendor") == vendor and m.get("tier") == tier]
    if not same:
        return model
    return min(same, key=lambda kv: kv[1].get("in", 0))[0]


def route_model(item_prompt: str, default_model: str) -> str:
    """Cheap segments drop a tier, within the vendor you chose."""
    tier = pricing.spec(default_model).get("tier", "sonnet")
    low = item_prompt.lower()
    if any(k in low for k in HARD) or not any(k in low for k in CHEAP):
        return default_model
    idx = TIER_ORDER.index(tier) if tier in TIER_ORDER else 1
    if idx == 0:
        return default_model
    return sibling(default_model, TIER_ORDER[idx - 1])


def preview(prompt: str, *, model: str = "claude-sonnet-5", workdir: str = "",
            turn_cap: int | None = None, force: str = "auto",
            use_planner: bool = False, effort: str = pricing.DEFAULT_EFFORT) -> dict:
    """Full plan preview — what the Composer screen renders before you commit.

    With use_planner, a configured model drafts the work list first and scope is
    derived from that list instead of from keywords. The model never sees a
    price: it enumerates, we cost it.
    """
    rem5, rem7 = meter.remaining()

    drafted, scope_override = None, None
    if use_planner and providers.config()["active"] != "heuristic":
        drafted = providers.plan(prompt)
        if drafted.get("ok"):
            scope_override = scope.from_plan(
                drafted["items"], estimator.classify(prompt))

    est = estimator.estimate(prompt, model=model, turn_cap=turn_cap,
                             remaining_pp5=rem5, remaining_pp7=rem7,
                             scope_override=scope_override, effort=effort)
    want_split, reason = estimator.should_split(est)
    if force == "single":
        want_split, reason = False, "Split disabled — running as one step."
    elif force == "forced":
        want_split, reason = True, "Split forced."

    if want_split and drafted and drafted.get("ok"):
        items = _items_from_plan(drafted["items"])
    else:
        items = segmenter.segment(prompt) if want_split else []
    if want_split and len(items) < 2:
        want_split = False
        if drafted and not drafted.get("ok"):
            reason = ("This needs splitting, but the planner was unavailable and the wording has "
                      "no separable actions. Fix the planner, or list the steps yourself.")
        elif not drafted:
            reason = ("This looks too big for one run, but it is written as a single sentence so "
                      "there is nothing to split on. Tick “Draft the plan first”, or write the "
                      "work out as a list.")
        else:
            reason = "Prompt has no separable actions — running as one step."
        items = []

    steps = []
    naive_costs, routed_costs, final_costs = [], [], []
    if want_split:
        # Each segment is a fraction of the parent's work. Splitting removes
        # coordination overhead, so the shares add up to a little more than one
        # whole — never to n whole prompts.
        scale = 1.25 / max(1, len(items))
        for it in items:
            m = route_model(it["prompt"], model)
            common = dict(turn_cap=turn_cap, turn_scale=scale, effort=effort,
                          remaining_pp5=rem5, remaining_pp7=rem7)
            # three variants, so the savings table reconciles against real numbers
            e_naive = estimator.estimate(it["prompt"], model=model, base_context=12000, **common)
            e_routed = estimator.estimate(it["prompt"], model=m, base_context=12000, **common)
            e = estimator.estimate(it["prompt"], model=m, base_context=6000, **common)
            naive_costs.append(e_naive["cost_p50"])
            routed_costs.append(e_routed["cost_p50"])
            final_costs.append(e["cost_p50"])
            kind, spec = _oracle_for(it, e["task_class"], workdir)
            steps.append({**it, "model": m, "estimate": e,
                          "oracle_kind": kind, "oracle_spec": spec})
    else:
        kind, spec = oracles.default_for(est["task_class"], workdir)
        steps.append({
            "idx": 0, "stage": 0, "title": _headline(prompt), "prompt": prompt,
            "artifacts": segmenter.artifacts_of(prompt), "depends_on": [],
            "model": model, "estimate": est, "oracle_kind": kind, "oracle_spec": spec,
        })

    n_stages = (max((s["stage"] for s in steps), default=0) + 1)
    sav = (segmenter.savings(est["cost_p50"], naive_costs, routed_costs,
                             final_costs, len(steps)) if want_split else None)

    return {
        "estimate": est,
        "plain": plain.explain(est, rem5),
        "planner": drafted,
        "split": want_split,
        "split_reason": reason,
        "steps": steps,
        "stages": n_stages,
        "savings": sav,
        "schedule": schedule(steps, rem5, rem7),
        "windows": {"remaining_pp5": rem5, "remaining_pp7": rem7},
    }


def _items_from_plan(items: list[dict]) -> list[dict]:
    """Turn a drafted plan into segments, chained in the order given."""
    out = []
    for i, it in enumerate(items):
        text = (it["title"] + (". " + it["detail"] if it.get("detail") else "")).strip()
        out.append({
            "idx": i,
            "title": it["title"][:42],
            "prompt": text,
            "artifacts": segmenter.artifacts_of(text),
            "depends_on": [i - 1] if i else [],
            "stage": i,
        })
    return out


def _headline(prompt: str) -> str:
    first = prompt.strip().splitlines()[0] if prompt.strip() else "Untitled"
    return (" ".join(first.split()[:10]))[:64] or "Untitled"


def _oracle_for(item: dict, task_class: str, workdir: str) -> tuple[str, str]:
    files = [a for a in item["artifacts"] if "." in a and "/" not in a[:1]]
    if files:
        return ("file_exists", ", ".join(files[:4]))
    return oracles.default_for(task_class, workdir)


def schedule(steps: list[dict], rem5: float, rem7: float) -> dict:
    """Greedy window packing: fill the current 5-hour window to a safety margin,
    defer the rest to the next reset. Unused plan percent evaporates, so we pack
    each window as full as the margin allows rather than spreading evenly."""
    margin = 8.0
    cap = max(10.0, rem5 - margin)
    windows: list[dict] = [{"index": 0, "budget": cap, "used": 0.0, "steps": []}]
    weekly_used = 0.0
    blocked = []

    for s in sorted(steps, key=lambda x: (x["stage"], x["idx"])):
        need5 = s["estimate"]["pp5_p50"]
        need7 = s["estimate"]["pp7_p50"]
        if weekly_used + need7 > rem7:
            blocked.append({"idx": s["idx"], "title": s["title"],
                            "why": "Would exceed the weekly window."})
            continue
        w = windows[-1]
        if w["used"] + need5 > w["budget"] and w["steps"]:
            windows.append({"index": len(windows), "budget": 100.0 - margin,
                            "used": 0.0, "steps": []})
            w = windows[-1]
        w["used"] += need5
        weekly_used += need7
        w["steps"].append({"idx": s["idx"], "title": s["title"],
                           "pp5": round(need5, 2), "model": s["model"],
                           "stage": s["stage"]})

    return {
        "windows": windows,
        "weekly_used": round(weekly_used, 2),
        "weekly_remaining_after": round(max(0.0, rem7 - weekly_used), 2),
        "blocked": blocked,
        "spans_windows": len(windows) > 1,
        "hours_to_finish": round((len(windows) - 1) * 5.0, 1),
    }


# ---------------------------------------------------------------- commit

def create_project(name: str, prompt: str, *, model: str, workdir: str,
                   force: str = "auto", goal: str = "",
                   turn_cap: int | None = None) -> int:
    p = preview(prompt, model=model, workdir=workdir, turn_cap=turn_cap, force=force)
    est = p["estimate"]
    now = db.now()
    pid = db.run(
        """INSERT INTO projects(name,goal,prompt,workdir,model,task_class,status,split_mode,
                                est_pp5,est_pp7,est_cost_p50,est_cost_p95,risk,risk_band,
                                created_at,updated_at)
           VALUES(?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?)""",
        (name or _headline(prompt), goal, prompt, workdir, model, est["task_class"],
         "forced" if p["split"] else "single",
         est["pp5_p50"], est["pp7_p50"], est["cost_p50"], est["cost_p95"],
         est["risk"], est["risk_band"], now, now),
    )

    id_map: dict[int, int] = {}
    for s in p["steps"]:
        e = s["estimate"]
        sid = db.run(
            """INSERT INTO steps(project_id,idx,stage,title,prompt,depends_on,artifacts,model,
                    est_input,est_out_p50,est_out_p95,est_turns_p50,est_turns_p95,
                    est_cost_p50,est_cost_p95,est_pp5,est_pp7,risk,risk_band,risk_drivers,
                    status,oracle_kind,oracle_spec,max_iters,created_at)
               VALUES(?,?,?,?,?,'[]',?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)""",
            (pid, s["idx"], s["stage"], s["title"], s["prompt"],
             json.dumps(s["artifacts"]), s["model"],
             e["base_input"], e["out_p50"], e["out_p95"], e["turns_p50"], e["turns_p95"],
             e["cost_p50"], e["cost_p95"], e["pp5_p50"], e["pp7_p50"],
             e["risk"], e["risk_band"], json.dumps(e["risk_drivers"]),
             s["oracle_kind"], s["oracle_spec"],
             max(3, int(e["turns_p95"] // 6) + 3), now),
        )
        id_map[s["idx"]] = sid

    for s in p["steps"]:
        deps = [id_map[d] for d in s.get("depends_on", []) if d in id_map]
        if deps:
            db.run("UPDATE steps SET depends_on=? WHERE id=?",
                   (json.dumps(deps), id_map[s["idx"]]))
    return pid


# ---------------------------------------------------------------- iteration

def next_prompt(step: dict) -> str:
    """The prompt to actually send for the next iteration.

    When risk is red or critical, the accumulated summary of what has already
    been done is folded into the prompt so a fresh run does not redo finished
    work — this is what makes an interrupted step resumable instead of wasted.
    """
    base = step["prompt"]
    iters = db.rows(
        "SELECT n, summary FROM iterations WHERE step_id=? AND summary<>'' ORDER BY n",
        (step["id"],))
    parts = [base]

    if iters:
        done = "\n".join(f"{r['n']}. {r['summary']}" for r in iters)
        parts.append(
            "\n---\n## Work already completed in earlier iterations\n"
            f"{done}\n\nContinue from here. Do not redo the items above.")

    if step["risk_band"] in ("red", "critical"):
        parts.append(
            "\n## Budget guard (this step is high risk)\n"
            f"- Stop after at most {step['max_iters']} tool-heavy passes.\n"
            "- Before you stop, print a 1-2 sentence summary of exactly what you "
            "changed, so the next run can resume without repeating it.\n"
            f"- Done when: {_done_when(step)}\n")
    elif step["oracle_spec"]:
        parts.append("\n## Done when\n" + _done_when(step) + "\n")

    return "\n".join(parts)


def _done_when(step: dict) -> str:
    kind, spec = step["oracle_kind"], step["oracle_spec"]
    if kind == "file_exists":
        return f"`{spec}` exists and is not empty."
    if kind == "tests":
        return f"`{spec}` passes."
    if kind == "command":
        return f"`{spec}` exits with status 0."
    if kind == "json_valid":
        return f"`{spec}` parses as valid JSON."
    if kind == "contains":
        return f"`{spec}` matches."
    return oracles.KINDS.get(kind, kind)


def attributed(step: dict) -> dict:
    """Work the watcher recorded while this step was running.

    The app used to keep two ledgers that never met: turns captured
    automatically from Claude Code's transcripts, and iterations you typed in by
    hand. Project spend only counted the typed ones, so a project you actually
    did showed $0.00. A turn belongs to whichever step was running when it was
    recorded; where windows overlap it goes to the one that started most
    recently, so nothing is double counted.
    """
    if not step.get("started_at"):
        return {"cost": 0.0, "turns": 0, "out_tokens": 0, "in_tokens": 0}
    start = float(step["started_at"])
    end = float(step["ended_at"] or time.time())

    # a later-started sibling takes ownership from its own start time
    nxt = db.scalar(
        """SELECT MIN(started_at) FROM steps
           WHERE project_id=? AND id<>? AND started_at > ?""",
        (step["project_id"], step["id"], start), None)
    if nxt:
        end = min(end, float(nxt))

    r = db.row(
        """SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS c,
                  COALESCE(SUM(out_tokens),0) AS o,
                  COALESCE(SUM(in_tokens + cache_read + cache_1h + cache_5m),0) AS i
           FROM turns WHERE ts >= ? AND ts <= ?""", (start, end)) or {}
    return {"cost": round(float(r.get("c") or 0), 6), "turns": int(r.get("n") or 0),
            "out_tokens": int(r.get("o") or 0), "in_tokens": int(r.get("i") or 0),
            "from": start, "to": end}


def step_spend(step: dict) -> dict:
    """Combined view: whichever ledger saw more is the truth for this step."""
    logged = float(db.scalar(
        "SELECT COALESCE(SUM(cost_usd),0) FROM iterations WHERE step_id=?",
        (step["id"],), 0.0) or 0.0)
    logged_n = int(db.scalar(
        "SELECT COUNT(*) FROM iterations WHERE step_id=?", (step["id"],), 0) or 0)
    auto = attributed(step)
    return {
        "logged": round(logged, 6), "logged_turns": logged_n,
        "auto": auto["cost"], "auto_turns": auto["turns"],
        "spent": round(max(logged, auto["cost"]), 6),
        "turns": max(logged_n, auto["turns"]),
        "source": "tracked" if auto["cost"] >= logged and auto["turns"] else "logged",
    }


def recompute_progress(step_id: int) -> float:
    """Progress from three signals: iterations spent, oracle verdict, budget burn."""
    s = db.row("SELECT * FROM steps WHERE id=?", (step_id,))
    if not s:
        return 0.0
    if s["status"] == "done":
        p = 1.0
    elif s["status"] in ("failed", "skipped"):
        p = s["progress"]
    else:
        sp = step_spend(s)
        by_iter = min(0.85, sp["turns"] / max(1.0, s["est_turns_p50"]) * 0.6)
        by_cost = min(0.85, sp["spent"] / max(1e-6, s["est_cost_p50"]) * 0.6)
        p = max(by_iter, by_cost)
        if s["satisfied"] == 1:
            p = 1.0
        elif s["status"] == "running":
            p = max(p, 0.08)
    db.run("UPDATE steps SET progress=? WHERE id=?", (round(p, 4), step_id))
    return p


def project_progress(pid: int) -> dict:
    steps = db.rows("SELECT * FROM steps WHERE project_id=? ORDER BY stage, idx", (pid,))
    if not steps:
        return {"progress": 0.0, "done": 0, "total": 0, "spent": 0.0, "est": 0.0,
                "satisfied": 0, "pp5_spent": 0.0, "est_p95": 0.0,
                "logged": 0.0, "auto": 0.0, "turns": 0}
    weights = [max(0.01, s["est_cost_p50"]) for s in steps]
    total_w = sum(weights)
    prog = sum(w * s["progress"] for w, s in zip(weights, steps)) / total_w

    spends = [step_spend(s) for s in steps]
    spent = sum(x["spent"] for x in spends)
    a5, _ = meter.alphas()
    return {
        "progress": round(prog, 4),
        "done": sum(1 for s in steps if s["status"] == "done"),
        "total": len(steps),
        "satisfied": sum(1 for s in steps if s["satisfied"] == 1),
        "spent": round(spent, 4),
        "logged": round(sum(x["logged"] for x in spends), 4),
        "auto": round(sum(x["auto"] for x in spends), 4),
        "turns": sum(x["turns"] for x in spends),
        "pp5_spent": round(spent * a5, 2),
        "est": round(sum(s["est_cost_p50"] for s in steps), 4),
        "est_p95": round(sum(s["est_cost_p95"] for s in steps), 4),
    }
