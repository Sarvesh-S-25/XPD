"""Local HTTP server. Python standard library only — no pip install required.

Serves the web UI and a small JSON API on 127.0.0.1. Nothing leaves the machine.
"""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import (db, estimator, installer, learning, meter, oracles, plain,
               planner, pricing, providers, scope, segmenter, watcher)

WEB = Path(__file__).resolve().parent.parent / "web"
ROUTES: list[tuple[str, re.Pattern, callable]] = []

# One random token per process, required on every mutating request. It never
# touches disk — restarting the server issues a new one, and the frontend
# reads it from GET /api/status (a GET is exempt) before it ever needs it. A
# forged cross-origin form POST cannot set a custom header, and this server
# sends no permissive CORS headers, so the browser blocks a script from
# reading the response and setting this header itself (PLS-DO S1, broadened —
# the CSRF gap was never limited to the shell-oracle path; POST /api/reset
# wiping the whole database with zero confirmation is the worse sibling).
CSRF_TOKEN = secrets.token_hex(16)
CSRF_HEADER = "X-PromptMeter-Token"
MUTATING = {"POST", "PATCH", "DELETE"}


def route(method: str, pattern: str):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class Err(Exception):
    def __init__(self, status: int, msg: str):
        self.status, self.msg = status, msg
        super().__init__(msg)


# ================================================================ meter

@route("GET", "/api/status")
def api_status(_m, _q, _b):
    s = meter.status()
    s["leaks"] = meter.leaks()
    s["token_correction"] = estimator.correction()
    s["csrf_token"] = CSRF_TOKEN
    s["settings"] = _user_settings()
    s["plans"] = [{"id": k, **v} for k, v in pricing.plans().items()]
    active = db.rows(
        "SELECT id,name,status,risk_band FROM projects WHERE status='active' ORDER BY updated_at DESC LIMIT 6")
    for p in active:
        p.update(planner.project_progress(p["id"]))
    s["active_projects"] = active
    s["totals"] = {
        "projects": db.scalar("SELECT COUNT(*) FROM projects", (), 0),
        "steps": db.scalar("SELECT COUNT(*) FROM steps", (), 0),
        "iterations": db.scalar("SELECT COUNT(*) FROM iterations", (), 0),
        # excludes sample data (PLS-DO P2) — this is the headline dollar
        # figure, and a loaded demo must never inflate what looks like real spend
        "spent": round(db.scalar(
            "SELECT SUM(cost_usd) FROM iterations WHERE is_demo=0", (), 0.0) or 0.0, 4),
    }
    return s


@route("POST", "/api/ingest")
def api_ingest(_m, _q, body):
    return meter.ingest(body if isinstance(body, dict) else {})


@route("POST", "/api/meter/manual")
def api_meter_manual(_m, _q, body):
    """Record a reading copied from Claude's own usage dialog.

    Mirrors that dialog exactly: current session %, its reset countdown, weekly
    %, its reset countdown. From here the app ticks the clocks down and adds new
    local spend on top, so you do not have to come back.
    """
    now = time.time()
    b = body or {}
    p5 = _num(b.get("pct5"))
    p7 = _num(b.get("pct7"))
    if p5 is None and p7 is None:
        raise Err(400, "Enter at least one percentage.")

    r5 = _num(b.get("reset5_minutes"))
    r7 = _num(b.get("reset7_minutes"))
    db.run("""INSERT INTO meter(ts,pct5,pct7,resets5,resets7,model,session_id,cost_usd)
              VALUES(?,?,?,?,?,'','manual',0)""",
           (now, p5, p7,
            now + (r5 * 60 if r5 is not None else meter.FIVE_HOURS),
            now + (r7 * 60 if r7 is not None else meter.SEVEN_DAYS)))
    cal = meter.calibrate_from_reading(p5, p7)
    out = meter.status()
    out["calibration"] = cal
    return out


def _num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


@route("GET", "/api/meter/history")
def api_meter_history(_m, q, _b):
    hours = float(q.get("hours", ["24"])[0])
    since = time.time() - hours * 3600
    return {"points": db.rows(
        "SELECT ts,pct5,pct7,cost_usd,model FROM meter WHERE ts>=? ORDER BY ts", (since,))}


# ================================================================ planning

@route("POST", "/api/preview")
def api_preview(_m, _q, body):
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise Err(400, "Prompt is empty.")
    cap = body.get("turn_cap")
    return planner.preview(
        prompt,
        model=body.get("model") or "claude-sonnet-5",
        workdir=body.get("workdir") or "",
        turn_cap=int(cap) if cap else None,
        force=body.get("force") or "auto",
        use_planner=bool(body.get("use_planner")),
        effort=body.get("effort") or pricing.DEFAULT_EFFORT,
    )


@route("GET", "/api/providers")
def api_providers(_m, _q, _b):
    return providers.public_config()


@route("POST", "/api/providers")
def api_providers_save(_m, _q, body):
    return providers.save_config(body or {})


@route("POST", "/api/providers/test")
def api_providers_test(_m, _q, body):
    return providers.test((body or {}).get("provider"))


def _user_settings() -> dict:
    """The persistent top selection bar's state: model, effort, and surface —
    picked once and reused everywhere, instead of per prompt. `plan` is not
    here; it already has its own persisted setting and route below."""
    return {
        "model": db.get_setting("default_model", "claude-sonnet-5"),
        "effort": db.get_setting("default_effort", pricing.DEFAULT_EFFORT),
        # restricted to what meter.py can actually see (both write the same
        # transcript format) — Cowork/browser usage is real but untrackable,
        # never a selectable "surface" implying it's measured.
        "surface": db.get_setting("default_surface", "terminal"),
    }


@route("GET", "/api/settings")
def api_settings_get(_m, _q, _b):
    return _user_settings()


@route("PATCH", "/api/settings")
def api_settings_patch(_m, _q, body):
    fields = {k: v for k, v in (body or {}).items() if k in ("model", "effort", "surface")}
    if "effort" in fields and fields["effort"] not in pricing.EFFORTS:
        raise Err(400, "Unknown effort.")
    if "surface" in fields and fields["surface"] not in ("terminal", "desktop"):
        raise Err(400, "Unknown surface — tracking can only see terminal and desktop sessions.")
    for k, v in fields.items():
        db.set_setting(f"default_{k}", v)
    return _user_settings()


@route("GET", "/api/models")
def api_models(_m, _q, _b):
    return {
        "models": [{"id": k, **v} for k, v in pricing.models().items()],
        "vendors": pricing.by_vendor(),
        "efforts": pricing.EFFORTS,
        "default_effort": pricing.DEFAULT_EFFORT,
        "task_classes": [{"id": k, **v} for k, v in pricing.priors().items()],
        "oracles": [{"id": k, "label": v} for k, v in oracles.KINDS.items()],
    }


# ================================================================ projects

@route("GET", "/api/projects")
def api_projects(_m, q, _b):
    status = q.get("status", ["active"])[0]
    where = "" if status == "all" else "WHERE status=?"
    args = () if status == "all" else (status,)
    out = db.rows(f"SELECT * FROM projects {where} ORDER BY updated_at DESC", args)
    for p in out:
        p.update(planner.project_progress(p["id"]))
    return {"projects": out}


@route("POST", "/api/projects")
def api_create(_m, _q, body):
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise Err(400, "Prompt is empty.")
    pid = planner.create_project(
        body.get("name") or "",
        prompt,
        model=body.get("model") or "claude-sonnet-5",
        workdir=body.get("workdir") or "",
        force=body.get("force") or "auto",
        goal=body.get("goal") or "",
        turn_cap=int(body["turn_cap"]) if body.get("turn_cap") else None,
    )
    return api_project({"id": str(pid)}, {}, None)


@route("GET", "/api/projects/{id}")
def api_project(m, _q, _b):
    pid = int(m["id"])
    p = db.row("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise Err(404, "No such project.")
    steps = db.rows("SELECT * FROM steps WHERE project_id=? ORDER BY stage, idx", (pid,))
    for s in steps:
        s["depends_on"] = json.loads(s["depends_on"] or "[]")
        s["artifacts"] = json.loads(s["artifacts"] or "[]")
        s["risk_drivers"] = json.loads(s["risk_drivers"] or "[]")
        s["iterations"] = db.rows(
            "SELECT * FROM iterations WHERE step_id=? ORDER BY n", (s["id"],))
        s["spent"] = round(sum(i["cost_usd"] for i in s["iterations"]), 4)
        s["next_prompt"] = planner.next_prompt(s)
    p["steps"] = steps
    p.update(planner.project_progress(pid))
    p["oracles"] = [{"id": k, "label": v} for k, v in oracles.KINDS.items()]
    return p


@route("PATCH", "/api/projects/{id}")
def api_project_patch(m, _q, body):
    pid = int(m["id"])
    fields = {k: v for k, v in (body or {}).items()
              if k in ("name", "goal", "status", "workdir", "model")}
    if not fields:
        raise Err(400, "Nothing to update.")
    if fields.get("status") == "done":
        fields["closed_at"] = db.now()
    sets = ", ".join(f"{k}=?" for k in fields)
    db.run(f"UPDATE projects SET {sets}, updated_at=? WHERE id=?",
           (*fields.values(), db.now(), pid))
    return api_project(m, {}, None)


@route("DELETE", "/api/projects/{id}")
def api_project_delete(m, q, _b):
    pid = int(m["id"])
    if q.get("hard", ["0"])[0] in ("1", "true"):
        db.run("DELETE FROM iterations WHERE project_id=?", (pid,))
        db.run("DELETE FROM steps WHERE project_id=?", (pid,))
        db.run("DELETE FROM projects WHERE id=?", (pid,))
        return {"deleted": pid, "hard": True}
    db.run("UPDATE projects SET status='archived', closed_at=?, updated_at=? WHERE id=?",
           (db.now(), db.now(), pid))
    return {"archived": pid}


# ================================================================ steps

@route("PATCH", "/api/steps/{id}")
def api_step_patch(m, _q, body):
    sid = int(m["id"])
    s = db.row("SELECT * FROM steps WHERE id=?", (sid,))
    if not s:
        raise Err(404, "No such step.")
    fields = {k: v for k, v in (body or {}).items()
              if k in ("title", "prompt", "model", "status", "oracle_kind",
                       "oracle_spec", "max_iters", "summary", "approved")}
    if "oracle_kind" in fields or "oracle_spec" in fields:
        fields["approved"] = 0     # changing the check re-arms the confirmation
    if fields.get("status") == "running" and not s["started_at"]:
        fields["started_at"] = db.now()
    if fields.get("status") in ("done", "failed", "skipped"):
        fields["ended_at"] = db.now()
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        db.run(f"UPDATE steps SET {sets} WHERE id=?", (*fields.values(), sid))
    planner.recompute_progress(sid)
    db.run("UPDATE projects SET updated_at=? WHERE id=?", (db.now(), s["project_id"]))
    return db.row("SELECT * FROM steps WHERE id=?", (sid,))


@route("POST", "/api/steps/{id}/iterate")
def api_iterate(m, _q, body):
    """Record one iteration: what was sent, what came back, what it cost.

    The summary is the important field. On a red/critical step it is folded into
    the next prompt so an interrupted run resumes instead of restarting.
    """
    sid = int(m["id"])
    s = db.row("SELECT * FROM steps WHERE id=?", (sid,))
    if not s:
        raise Err(404, "No such step.")
    n = (db.scalar("SELECT MAX(n) FROM iterations WHERE step_id=?", (sid,), 0) or 0) + 1

    model = body.get("model") or s["model"]
    in_tok = int(body.get("in_tokens") or 0)
    out_tok = int(body.get("out_tokens") or 0)
    c_read = int(body.get("cache_read") or 0)
    c_write = int(body.get("cache_write") or 0)
    cost = float(body.get("cost_usd") or 0) or pricing.cost_usd(model, in_tok, out_tok, c_read, c_write)
    a5, a7 = meter.alphas()

    db.run(
        """INSERT INTO iterations(step_id,project_id,n,prompt_sent,summary,model,
               in_tokens,out_tokens,cache_read,cache_write,cost_usd,pp5_delta,pp7_delta,
               verdict,note,est_cost,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, s["project_id"], n,
         body.get("prompt_sent") or planner.next_prompt(s),
         (body.get("summary") or "").strip(), model,
         in_tok, out_tok, c_read, c_write, cost, cost * a5, cost * a7,
         body.get("verdict") or "unknown", body.get("note") or "",
         s["est_cost_p50"], db.now()),
    )

    if body.get("summary"):
        rolled = "; ".join(
            r["summary"] for r in db.rows(
                "SELECT summary FROM iterations WHERE step_id=? AND summary<>'' ORDER BY n",
                (sid,)))[:1200]
        db.run("UPDATE steps SET summary=? WHERE id=?", (rolled, sid))
    if s["status"] == "pending":
        db.run("UPDATE steps SET status='running', started_at=COALESCE(started_at,?) WHERE id=?",
               (db.now(), sid))

    if in_tok > 0 and s["prompt"]:
        estimator.learn_token_ratio(len(s["prompt"]), max(1, in_tok // 4))

    res = check_step(sid) if s["oracle_kind"] != "manual" else None
    planner.recompute_progress(sid)
    db.run("UPDATE projects SET updated_at=? WHERE id=?", (db.now(), s["project_id"]))

    out = db.row("SELECT * FROM steps WHERE id=?", (sid,))
    out["oracle_result"] = res
    out["iterations"] = db.rows("SELECT * FROM iterations WHERE step_id=? ORDER BY n", (sid,))
    out["next_prompt"] = planner.next_prompt(out)
    out["stop"] = _stop_reason(out)
    return out


def _stop_reason(s: dict) -> str | None:
    n = db.scalar("SELECT COUNT(*) FROM iterations WHERE step_id=?", (s["id"],), 0) or 0
    if s["satisfied"] == 1:
        return "Deliverable satisfied — stop."
    if n >= s["max_iters"]:
        return f"Hit the iteration cap ({s['max_iters']}) — stop and re-plan."
    spent = db.scalar("SELECT SUM(cost_usd) FROM iterations WHERE step_id=?", (s["id"],), 0.0) or 0.0
    if spent > s["est_cost_p95"] > 0:
        return "Spent past the P95 estimate — stop and re-plan."
    rows = db.rows("SELECT summary FROM iterations WHERE step_id=? ORDER BY n DESC LIMIT 2", (s["id"],))
    if len(rows) == 2 and rows[0]["summary"] and rows[0]["summary"] == rows[1]["summary"]:
        return "No progress since the last iteration — stop."
    return None


@route("POST", "/api/steps/{id}/check")
def api_check(m, _q, _b):
    return {"result": check_step(int(m["id"]))}


def check_step(sid: int) -> dict:
    s = db.row("SELECT * FROM steps WHERE id=?", (sid,))
    if not s:
        raise Err(404, "No such step.")
    p = db.row("SELECT workdir FROM projects WHERE id=?", (s["project_id"],))
    last = db.rows("SELECT summary FROM iterations WHERE step_id=? ORDER BY n DESC LIMIT 2", (sid,))
    res = oracles.check(s["oracle_kind"], s["oracle_spec"], (p or {}).get("workdir", ""),
                        last[0]["summary"] if last else "",
                        last[1]["summary"] if len(last) > 1 else "",
                        approved=bool(s["approved"]))
    db.run("UPDATE steps SET satisfied=?, satisfied_note=? WHERE id=?",
           (res["satisfied"], res["note"], sid))
    if res["satisfied"] == 1 and s["status"] != "done":
        db.run("UPDATE steps SET status='done', ended_at=? WHERE id=?", (db.now(), sid))
    planner.recompute_progress(sid)
    return res


@route("POST", "/api/steps/{id}/satisfy")
def api_satisfy(m, _q, body):
    sid = int(m["id"])
    val = int(body.get("satisfied", 1))
    db.run("UPDATE steps SET satisfied=?, satisfied_note=?, status=?, ended_at=? WHERE id=?",
           (val, body.get("note") or "Marked by hand",
            "done" if val == 1 else "failed", db.now(), sid))
    planner.recompute_progress(sid)
    return db.row("SELECT * FROM steps WHERE id=?", (sid,))


@route("POST", "/api/steps/{id}/resplit")
def api_resplit(m, _q, _b):
    """Split one oversized step into children in place."""
    sid = int(m["id"])
    s = db.row("SELECT * FROM steps WHERE id=?", (sid,))
    if not s:
        raise Err(404, "No such step.")
    items = segmenter.segment(s["prompt"])
    if len(items) < 2:
        raise Err(400, "This step has no separable actions.")
    base = db.scalar("SELECT MAX(idx) FROM steps WHERE project_id=?", (s["project_id"],), 0) or 0
    rem5, rem7 = meter.remaining()
    for k, it in enumerate(items):
        mdl = planner.route_model(it["prompt"], s["model"])
        e = estimator.estimate(it["prompt"], model=mdl, base_context=6000,
                               remaining_pp5=rem5, remaining_pp7=rem7)
        db.run(
            """INSERT INTO steps(project_id,idx,stage,title,prompt,depends_on,artifacts,model,
                   est_input,est_out_p50,est_out_p95,est_turns_p50,est_turns_p95,
                   est_cost_p50,est_cost_p95,est_pp5,est_pp7,risk,risk_band,risk_drivers,
                   status,oracle_kind,oracle_spec,max_iters,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)""",
            (s["project_id"], base + k + 1, s["stage"] + it["stage"],
             it["title"], it["prompt"], json.dumps([sid] if it["stage"] else []),
             json.dumps(it["artifacts"]), mdl,
             e["base_input"], e["out_p50"], e["out_p95"], e["turns_p50"], e["turns_p95"],
             e["cost_p50"], e["cost_p95"], e["pp5_p50"], e["pp7_p50"],
             e["risk"], e["risk_band"], json.dumps(e["risk_drivers"]),
             s["oracle_kind"], s["oracle_spec"], 4, db.now()),
        )
    db.run("UPDATE steps SET status='skipped', summary=?, ended_at=? WHERE id=?",
           (f"Split into {len(items)} steps.", db.now(), sid))
    return api_project({"id": str(s["project_id"])}, {}, None)


# ================================================================ history

@route("GET", "/api/history")
def api_history(_m, _q, _b):
    return {
        "projects": [
            {**p, **planner.project_progress(p["id"])}
            for p in db.rows("SELECT * FROM projects ORDER BY updated_at DESC LIMIT 200")
        ],
        "models": learning.model_table(),
        "accuracy": learning.accuracy(),
        "capacity": meter.capacity(),
        "classes": [
            {"id": cid, "label": info.get("label", cid), **_class_profile(cid)}
            for cid, info in pricing.priors().items()
        ],
    }


def _class_profile(cid: str) -> dict:
    pr = learning.profile(cid)
    return {
        "turns_p50": round(pr["turns"][0], 1), "turns_p95": round(pr["turns"][1], 1),
        "out_p50": int(pr["out"][0]), "out_p95": int(pr["out"][1]),
        "growth": int(pr["growth"]), "n": pr["n"], "confidence": pr["confidence"],
    }


@route("GET", "/api/graph/{id}")
def api_graph(m, _q, _b):
    """Step graph: what prompt produced what, and whether it was satisfied."""
    pid = int(m["id"])
    steps = db.rows("SELECT * FROM steps WHERE project_id=? ORDER BY stage, idx", (pid,))
    nodes, edges = [], []
    for s in steps:
        deps = json.loads(s["depends_on"] or "[]")
        iters = db.rows(
            "SELECT n,summary,cost_usd,out_tokens,verdict FROM iterations WHERE step_id=? ORDER BY n",
            (s["id"],))
        nodes.append({
            "id": s["id"], "title": s["title"], "stage": s["stage"], "idx": s["idx"],
            "status": s["status"], "satisfied": s["satisfied"], "progress": s["progress"],
            "risk_band": s["risk_band"], "model": s["model"],
            "artifacts": json.loads(s["artifacts"] or "[]"),
            "iterations": len(iters), "summary": s["summary"],
            "spent": round(sum(i["cost_usd"] for i in iters), 4),
            "est": round(s["est_cost_p50"], 4),
            "prompt": s["prompt"],
            "outputs": [{"n": i["n"], "summary": i["summary"],
                         "out_tokens": i["out_tokens"], "cost": round(i["cost_usd"], 4)}
                        for i in iters],
        })
        for d in deps:
            edges.append({"from": d, "to": s["id"]})
    # implicit stage ordering where nothing explicit exists
    by_stage: dict[int, list[int]] = {}
    for n_ in nodes:
        by_stage.setdefault(n_["stage"], []).append(n_["id"])
    explicit = {e["to"] for e in edges}
    for st in sorted(by_stage):
        if st == 0:
            continue
        for nid in by_stage[st]:
            if nid not in explicit:
                for prev in by_stage.get(st - 1, []):
                    edges.append({"from": prev, "to": nid, "implicit": True})
    return {"nodes": nodes, "edges": edges,
            "stages": (max(by_stage) + 1) if by_stage else 0}


# ================================================================ setup

@route("GET", "/api/setup")
def api_setup(_m, _q, _b):
    s = installer.status()
    st = meter.status()
    s["live"] = st["live"]
    s["source"] = st["source"]
    s["watcher"] = watcher.info()
    s["plan"] = meter.plan_id()
    s["plans"] = [{"id": k, **v} for k, v in pricing.plans().items()]
    return s


@route("POST", "/api/setup/scan")
def api_setup_scan(_m, _q, body):
    return watcher.scan(full=bool((body or {}).get("full")))


@route("POST", "/api/setup/plan")
def api_setup_plan(_m, _q, body):
    pid = (body or {}).get("plan") or "max5"
    if pid not in pricing.plans():
        raise Err(400, "Unknown plan.")
    db.set_setting("plan", pid)
    return meter.status()


@route("POST", "/api/setup/install")
def api_setup_install(_m, _q, body):
    return installer.install(force=bool((body or {}).get("force")))


@route("POST", "/api/setup/uninstall")
def api_setup_uninstall(_m, _q, _b):
    return installer.uninstall()


@route("POST", "/api/setup/test")
def api_setup_test(_m, _q, _b):
    return installer.selftest()


@route("POST", "/api/demo")
def api_demo(_m, _q, _b):
    from .demo import seed
    return seed()


@route("POST", "/api/reset")
def api_reset(_m, _q, body):
    """Wipe everything. The CSRF token already stops a forged request from
    reaching this at all — this second check stops a genuine but accidental
    click, since there is no undo.
    """
    if (body or {}).get("confirm") != "RESET_ALL_DATA":
        raise Err(400, 'Send {"confirm": "RESET_ALL_DATA"} to actually do this.')
    for t in ("iterations", "steps", "projects", "meter"):
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM settings")
    return {"ok": True}


@route("POST", "/api/clear-demo")
def api_clear_demo(_m, _q, _b):
    """Delete only sample rows seeded by 'Load sample data' (PLS-DO P2) —
    real projects and history are untouched."""
    n = db.scalar("SELECT COUNT(*) FROM projects WHERE is_demo=1", (), 0) or 0
    db.run("DELETE FROM iterations WHERE project_id IN "
           "(SELECT id FROM projects WHERE is_demo=1)")
    db.run("DELETE FROM steps WHERE project_id IN "
           "(SELECT id FROM projects WHERE is_demo=1)")
    db.run("DELETE FROM projects WHERE is_demo=1")
    db.run("DELETE FROM meter WHERE is_demo=1")
    return {"cleared_projects": n}


# ================================================================ plumbing

class Handler(BaseHTTPRequestHandler):
    server_version = "PromptMeter"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # quiet by default
        pass

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        """Reject any request whose Host header isn't this server's own
        address — closes DNS rebinding, the same class of bug that has hit
        other localhost dev servers (Ollama, Jupyter). A browser sets this
        header itself from the URL bar; a page can name any hostname there
        without controlling what it resolves to, so an attacker-owned domain
        that resolves to 127.0.0.1 would otherwise sail through same-origin
        checks that only look at scheme+host+port as reported by the browser.
        """
        host_header = (self.headers.get("Host") or "").strip()
        if not host_header:
            return False
        hostname = host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
        hostname = hostname.strip("[]")             # IPv6 literal, e.g. [::1]:7777
        allowed = {"127.0.0.1", "localhost", "::1"}
        bind_host = self.server.server_address[0]
        if bind_host not in ("0.0.0.0", "::"):
            allowed.add(bind_host)
        return hostname in allowed

    def _dispatch(self, method: str):
        if not self._host_ok():
            return self._json(403, {"error": "Host header not allowed."})

        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if not path.startswith("/api/"):
            return self._static(path)

        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = {}

        if method in MUTATING and self.headers.get(CSRF_HEADER) != CSRF_TOKEN:
            return self._json(403, {"error": "Missing or wrong CSRF token."})

        for m, rx, fn in ROUTES:
            if m != method:
                continue
            match = rx.match(path)
            if match:
                try:
                    return self._json(200, fn(match.groupdict(), query, body))
                except Err as e:
                    return self._json(e.status, {"error": e.msg})
                except Exception as e:                        # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        self._json(404, {"error": f"No route for {method} {path}"})

    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / rel).resolve()
        try:
            target.relative_to(WEB.resolve())
        except ValueError:
            return self._json(403, {"error": "forbidden"})
        if not target.is_file():
            target = WEB / "index.html"
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def do_GET(self):     self._dispatch("GET")       # noqa: E704
    def do_POST(self):    self._dispatch("POST")      # noqa: E704
    def do_PATCH(self):   self._dispatch("PATCH")     # noqa: E704
    def do_DELETE(self):  self._dispatch("DELETE")    # noqa: E704


def serve(host: str = "127.0.0.1", port: int = 7777) -> None:
    providers.load_dotenv()
    db.init()
    watcher.init()
    watcher.start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    w = watcher.info()
    print(f"\n  PromptMeter running at  http://{host}:{port}")
    print(f"  Data:                   {db.db_path()}")
    print(f"  Watching:               {w['root']}"
          + ("" if w["root_exists"] else "   (no sessions found yet)"))
    print("  Press Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        httpd.server_close()
