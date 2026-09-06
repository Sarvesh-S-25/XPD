"""Seed realistic sample data so the UI is legible before you've used it for real.

Every row this writes is flagged `is_demo=1` (PLS-DO P2) — nothing here should
ever be mistaken for real usage, count toward calibration (learning.py filters
it out), or survive `POST /api/clear-demo`.
"""
from __future__ import annotations

import random
import time

from . import db, meter, planner, pricing

DEMO_PROMPTS = [
    ("Recipe box web app",
     "Build a small recipe web app.\n"
     "- Create the SQLite schema in db.py with tables for recipes and ingredients\n"
     "- Write a FastAPI backend in api.py exposing list, create and search endpoints\n"
     "- Then build the frontend page index.html that calls the search endpoint\n"
     "- Add pytest tests in test_api.py covering create and search\n"
     "- Finally write a README.md explaining how to run it\n",
     "claude-sonnet-5"),
    ("Invoice CSV cleanup",
     "Clean the messy invoice export in invoices.csv: drop duplicate rows, "
     "normalise the date column to ISO format, and write the result to "
     "invoices_clean.csv with a summary of what changed.",
     "claude-haiku-4-5"),
    ("Auth refactor",
     "Refactor the authentication layer so sessions live in Redis instead of "
     "in-process memory. Update auth.py and middleware.py, keep the existing test "
     "suite green, and migrate any callers that touch the old session dict.",
     "claude-opus-5"),
]

# Deliberately generic so they read sensibly against whichever step they land on.
SUMMARIES = [
    "Wrote the first working version.",
    "Fixed the two problems the last pass left behind.",
    "Filled in the edge cases and re-ran the check.",
    "Tidied it up; behaviour unchanged.",
    "Finished the remaining piece and verified it.",
    "Reworked the approach after the first attempt did not hold.",
]


def seed() -> dict:
    rnd = random.Random(7)
    now = time.time()

    # A day of plausible meter samples so the rings and pace line have shape.
    db.run("DELETE FROM meter WHERE session_id IN ('demo','manual')")
    p5 = p7 = 0.0
    start = now - 26 * 3600
    reset5 = start + 5 * 3600
    for i in range(150):
        ts = start + i * (26 * 3600 / 150)
        if ts > reset5:
            p5 = 0.0
            reset5 += 5 * 3600
        active = 9 <= time.localtime(ts).tm_hour <= 22
        step = rnd.uniform(0.4, 2.1) if active else rnd.uniform(0.0, 0.15)
        p5 = min(99.0, p5 + step)
        p7 = min(99.0, p7 + step * 0.17)
        model = rnd.choice(["claude-sonnet-5", "claude-sonnet-5", "claude-opus-5"])
        it, ot = rnd.randint(9000, 40000), rnd.randint(300, 2200)
        cr, cw = rnd.randint(20000, 120000), rnd.choice([0, 0, 6000])
        db.run("""INSERT INTO meter(ts,pct5,pct7,resets5,resets7,model,session_id,
                       in_tokens,out_tokens,cache_read,cache_write,cost_usd,ctx_pct,is_demo)
                  VALUES(?,?,?,?,?,?,'demo',?,?,?,?,?,?,1)""",
               (ts, round(p5, 2), round(p7, 2), reset5, now + 3.2 * 86400, model,
                it, ot, cr, cw, pricing.cost_usd(model, it, ot, cr, cw),
                rnd.uniform(20, 88)))

    made = []
    for name, prompt, model in DEMO_PROMPTS:
        pid = planner.create_project(name, prompt, model=model, workdir="",
                                     force="auto", goal="")
        made.append(pid)
        db.run("UPDATE projects SET is_demo=1 WHERE id=?", (pid,))
        db.run("UPDATE steps SET is_demo=1 WHERE project_id=?", (pid,))
        steps = db.rows("SELECT * FROM steps WHERE project_id=? ORDER BY stage, idx", (pid,))
        # Advance the first project furthest, leave the last untouched.
        depth = {0: len(steps), 1: max(1, len(steps) // 2), 2: 0}[made.index(pid)]
        for k, s in enumerate(steps):
            if k >= depth:
                break
            n_iters = rnd.randint(1, 3)
            for j in range(n_iters):
                it, ot = rnd.randint(8000, 30000), rnd.randint(400, 2600)
                cr = rnd.randint(10000, 90000)
                cost = pricing.cost_usd(s["model"], it, ot, cr, 0)
                a5, a7 = meter.alphas()
                db.run("""INSERT INTO iterations(step_id,project_id,n,prompt_sent,summary,
                              model,in_tokens,out_tokens,cache_read,cache_write,cost_usd,
                              pp5_delta,pp7_delta,verdict,note,est_cost,created_at,is_demo)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'',?,?,1)""",
                       (s["id"], pid, j + 1, s["prompt"][:400],
                        SUMMARIES[(k + j) % len(SUMMARIES)], s["model"],
                        it, ot, cr, 0, cost, cost * a5, cost * a7,
                        "pass" if j == n_iters - 1 else "unknown",
                        s["est_cost_p50"], now - (depth - k) * 5400 + j * 900))
            last = k == depth - 1
            db.run("""UPDATE steps SET status=?, satisfied=?, satisfied_note=?,
                          summary=?, started_at=?, ended_at=? WHERE id=?""",
                   ("running" if last else "done",
                    0 if last else 1,
                    "" if last else "Checked and satisfied.",
                    SUMMARIES[k % len(SUMMARIES)],
                    now - (depth - k) * 5400,
                    None if last else now - (depth - k) * 5400 + 2400,
                    s["id"]))
            planner.recompute_progress(s["id"])
        db.run("UPDATE projects SET updated_at=? WHERE id=?",
               (now - made.index(pid) * 3600, pid))

    # Fit alpha against the seeded history so the capacity card has a real number.
    prev = db.row("SELECT * FROM meter ORDER BY ts DESC LIMIT 1")
    if prev:
        meter._fit(prev)

    return {"ok": True, "projects": made,
            "message": f"Seeded {len(made)} sample projects and a day of meter history."}
