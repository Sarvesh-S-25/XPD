"""Automatic usage tracking — reads Claude Code's own session transcripts.

Claude Code writes every turn to ~/.claude/projects/<project>/<session>.jsonl,
including an exact `usage` block per assistant message. **Both the terminal and
the desktop app write there**, so this is the one source that covers every way
you use Claude — no status line, no typing, nothing to configure.

Each assistant line looks like:

  {"type": "assistant", "uuid": "...", "timestamp": "2026-08-15T10:59:25.130Z",
   "sessionId": "...", "cwd": "...",
   "message": {"id": "msg_...", "model": "claude-opus-5",
               "usage": {"input_tokens": 2, "output_tokens": 1123,
                         "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 49868,
                         "cache_creation": {"ephemeral_1h_input_tokens": 49868,
                                            "ephemeral_5m_input_tokens": 0}}}}

We tail each file from the last byte offset we read, so a rescan is cheap even
with a 15 MB transcript.

Assistant messages that actually reasoned also carry `content` blocks
(`{"type": "thinking", "thinking": "..."}` alongside `{"type": "text", ...}`).
`usage` alone is enough for cost; `content` is read too, only to learn this
model's real thinking-token share (see `_learn_thinking_share` /
`pricing.learn_thinking_share`) — nothing else here uses it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db, pricing

SCAN_SECONDS = 20
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    uuid        TEXT PRIMARY KEY,
    ts          REAL NOT NULL,
    session_id  TEXT NOT NULL DEFAULT '',
    project     TEXT NOT NULL DEFAULT '',
    cwd         TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    in_tokens   INTEGER NOT NULL DEFAULT 0,
    out_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_read  INTEGER NOT NULL DEFAULT 0,
    cache_1h    INTEGER NOT NULL DEFAULT 0,
    cache_5m    INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'transcript'
);
CREATE INDEX IF NOT EXISTS ix_turns_ts ON turns(ts);

CREATE TABLE IF NOT EXISTS scanned (
    path    TEXT PRIMARY KEY,
    offset  INTEGER NOT NULL DEFAULT 0,
    size    INTEGER NOT NULL DEFAULT 0,
    seen_at REAL NOT NULL DEFAULT 0
);
"""


def init() -> None:
    conn = db.connect()
    conn.executescript(EXTRA_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------- paths

def claude_root() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def transcript_files() -> list[Path]:
    root = claude_root() / "projects"
    if not root.is_dir():
        return []
    try:
        return sorted(root.rglob("*.jsonl"))
    except OSError:
        return []


# ---------------------------------------------------------------- parsing

def _ts(value) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value) / (1000.0 if value > 1e11 else 1.0)
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() \
            if "+" not in s and "-" not in s[10:] else datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _learn_thinking_share(model: str, content) -> None:
    """If this turn's message carries an actual thinking block, feed its real
    character share of thinking-vs-text length into pricing.learn_thinking_share()
    — the one place PromptMeter can observe this at all, since the transcript
    is the only record of what a turn actually contained. A turn with no
    thinking block (thinking off, or an older transcript format) teaches
    nothing, and the static table stays in charge for that model until enough
    real observations exist.
    """
    if not model or not isinstance(content, list):
        return
    think_chars = sum(len(b.get("thinking") or "") for b in content
                      if isinstance(b, dict) and b.get("type") == "thinking")
    text_chars = sum(len(b.get("text") or "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text")
    total = think_chars + text_chars
    if think_chars > 0 and total > 0:
        pricing.learn_thinking_share(model, think_chars / total)


def parse_line(raw: str, project: str) -> dict | None:
    """Pull one billable turn out of a transcript line, or None."""
    try:
        o = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(o, dict) or o.get("type") != "assistant":
        return None

    msg = o.get("message")
    if not isinstance(msg, dict):
        return None
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None

    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    c1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
    c5m = int(cc.get("ephemeral_5m_input_tokens") or 0)
    total_create = int(u.get("cache_creation_input_tokens") or 0)
    if not (c1h or c5m) and total_create:
        c1h = total_create          # subscriptions default to the 1-hour cache

    in_tok = int(u.get("input_tokens") or 0)
    out_tok = int(u.get("output_tokens") or 0)
    c_read = int(u.get("cache_read_input_tokens") or 0)
    if not any((in_tok, out_tok, c_read, c1h, c5m)):
        return None

    uid = o.get("uuid") or msg.get("id")
    if not uid:
        return None
    ts = _ts(o.get("timestamp")) or time.time()
    model = msg.get("model") or ""

    _learn_thinking_share(model, msg.get("content"))

    p = pricing.price_of(model)
    cost = (in_tok * p["in"]
            + c_read * p["in"] * pricing.CACHE["read"]
            + c1h * p["in"] * pricing.CACHE["write_1h"]
            + c5m * p["in"] * pricing.CACHE["write_5m"]) / 1e6 \
        + out_tok * p["out"] / 1e6

    return {
        "uuid": str(uid), "ts": ts, "session_id": o.get("sessionId") or "",
        "project": project, "cwd": o.get("cwd") or "", "model": model,
        "in_tokens": in_tok, "out_tokens": out_tok, "cache_read": c_read,
        "cache_1h": c1h, "cache_5m": c5m, "cost_usd": cost,
    }


# ---------------------------------------------------------------- scanning

def scan(full: bool = False) -> dict:
    """Read whatever is new in every transcript. Cheap and idempotent."""
    with _LOCK:
        init()
        files = transcript_files()
        added = 0
        errors: list[str] = []
        conn = db.connect()

        for f in files:
            key = str(f)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            row = db.row("SELECT offset,size FROM scanned WHERE path=?", (key,))
            offset = 0 if (full or not row) else int(row["offset"])
            if row and size < int(row["size"]):
                offset = 0                      # file was rotated or truncated
            if row and not full and size == int(row["size"]) and offset >= size:
                continue

            project = f.parent.name if f.parent.name != "projects" else "unknown"
            try:
                with f.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    batch = []
                    for line in fh:
                        rec = parse_line(line, project)
                        if rec:
                            batch.append(rec)
                    new_offset = fh.tell()
            except OSError as e:
                errors.append(f"{f.name}: {e}")
                continue

            if batch:
                before = db.scalar("SELECT COUNT(*) FROM turns", (), 0) or 0
                conn.executemany(
                    """INSERT OR IGNORE INTO turns
                       (uuid,ts,session_id,project,cwd,model,in_tokens,out_tokens,
                        cache_read,cache_1h,cache_5m,cost_usd,source)
                       VALUES(:uuid,:ts,:session_id,:project,:cwd,:model,:in_tokens,
                              :out_tokens,:cache_read,:cache_1h,:cache_5m,:cost_usd,
                              'transcript')""",
                    batch)
                conn.commit()
                added += (db.scalar("SELECT COUNT(*) FROM turns", (), 0) or 0) - before

            conn.execute(
                """INSERT INTO scanned(path,offset,size,seen_at) VALUES(?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET offset=excluded.offset,
                       size=excluded.size, seen_at=excluded.seen_at""",
                (key, new_offset, size, time.time()))
            conn.commit()

        db.set_setting("last_scan", time.time())
        return {
            "files": len(files),
            "turns": db.scalar("SELECT COUNT(*) FROM turns", (), 0) or 0,
            "added": added,
            "errors": errors[:5],
            "root": str(claude_root() / "projects"),
            "root_exists": (claude_root() / "projects").is_dir(),
        }


def info() -> dict:
    init()
    root = claude_root() / "projects"
    n = db.scalar("SELECT COUNT(*) FROM turns", (), 0) or 0
    first = db.scalar("SELECT MIN(ts) FROM turns", (), None)
    last = db.scalar("SELECT MAX(ts) FROM turns", (), None)
    sessions = db.scalar("SELECT COUNT(DISTINCT session_id) FROM turns", (), 0) or 0
    return {
        "root": str(root),
        "root_exists": root.is_dir(),
        "files": len(transcript_files()),
        "turns": n,
        "sessions": sessions,
        "first_turn": first,
        "last_turn": last,
        "last_scan": db.get_setting("last_scan"),
        "running": bool(_THREAD and _THREAD.is_alive()),
        "projects": db.rows(
            """SELECT project, COUNT(*) AS turns, SUM(cost_usd) AS cost, MAX(ts) AS last
               FROM turns GROUP BY project ORDER BY last DESC LIMIT 8"""),
    }


def start() -> None:
    """Background rescan loop. Started with the server."""
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return

    def loop():
        while True:
            try:
                scan()
            except Exception:                      # noqa: BLE001 - never die
                pass
            time.sleep(SCAN_SECONDS)

    _THREAD = threading.Thread(target=loop, daemon=True, name="promptmeter-watcher")
    _THREAD.start()


# ------------------------------------------------------- any agent, any model
#
# The transcript watcher above is one *source* (Claude Code writes files we can
# tail). It is not the only way a turn can get into the ledger: any agent — Codex,
# Gemini CLI, Cursor, Aider, a script, a hook — can report the turns it ran, and
# they land in the same `turns` table, priced through the same catalogue, so
# project spend, learning and History work for it exactly as they do for Claude.
# Rows are tagged source='api' so the Claude plan-window maths (meter.py) can
# tell them apart and leave them out — a Gemini turn is real spend, but it was
# not drawn from a Claude subscription window.

MAX_BATCH = 1000
_USAGE_INT_FIELDS = ("in_tokens", "out_tokens", "cache_read", "cache_write")


def _usage_record(i: int, r) -> tuple[dict, bool]:
    if not isinstance(r, dict):
        raise ValueError(f"record {i}: must be an object.")
    model = str(r.get("model") or "").strip()
    if not model:
        raise ValueError(f"record {i}: model is required.")
    n = {}
    for k in _USAGE_INT_FIELDS:
        try:
            v = int(r.get(k) or 0)
        except (TypeError, ValueError):
            raise ValueError(f"record {i}: {k} must be a whole number.")
        if v < 0:
            raise ValueError(f"record {i}: {k} cannot be negative.")
        n[k] = v
    now = time.time()
    try:
        ts = float(r.get("ts")) if r.get("ts") not in (None, "") else now
    except (TypeError, ValueError):
        raise ValueError(f"record {i}: ts must be a unix timestamp in seconds.")
    if ts > 1e12:
        ts /= 1000.0                                  # milliseconds
    if ts <= 0 or ts > now + 86400:
        raise ValueError(f"record {i}: ts is not a plausible time.")
    if r.get("cost_usd") not in (None, ""):
        try:
            cost = float(r["cost_usd"])
        except (TypeError, ValueError):
            raise ValueError(f"record {i}: cost_usd must be a number.")
        if cost < 0 or cost != cost:
            raise ValueError(f"record {i}: cost_usd cannot be negative.")
    else:
        cost = pricing.cost_usd(model, n["in_tokens"], n["out_tokens"],
                                n["cache_read"], n["cache_write"])
    rid = str(r.get("id") or "").strip()
    import uuid as _uuid
    rec = {
        "uuid": "api:" + (rid or _uuid.uuid4().hex),  # client id makes retries idempotent
        "ts": ts, "session_id": str(r.get("session_id") or "")[:120],
        "project": str(r.get("agent") or "")[:60], "cwd": str(r.get("cwd") or "")[:400],
        "model": model[:120], "in_tokens": n["in_tokens"], "out_tokens": n["out_tokens"],
        "cache_read": n["cache_read"], "cache_1h": n["cache_write"], "cache_5m": 0,
        "cost_usd": round(cost, 6),
    }
    return rec, pricing.is_known(model)


def add_usage(records: list) -> dict:
    """Record turns reported by any agent. All-or-nothing: one bad record rejects
    the batch with a message naming it, rather than half-importing."""
    if not isinstance(records, list) or not records:
        raise ValueError("Send at least one usage record.")
    if len(records) > MAX_BATCH:
        raise ValueError(f"At most {MAX_BATCH} records per request.")
    parsed, unknown = [], set()
    for i, r in enumerate(records):
        rec, known = _usage_record(i, r)
        parsed.append(rec)
        if not known:
            unknown.add(rec["model"])
    init()
    conn = db.connect()
    added = 0
    for rec in parsed:
        cur = conn.execute(
            """INSERT OR IGNORE INTO turns
               (uuid,ts,session_id,project,cwd,model,in_tokens,out_tokens,
                cache_read,cache_1h,cache_5m,cost_usd,source)
               VALUES(:uuid,:ts,:session_id,:project,:cwd,:model,:in_tokens,
                      :out_tokens,:cache_read,:cache_1h,:cache_5m,:cost_usd,'api')""", rec)
        added += cur.rowcount
    conn.commit()
    return {"added": added, "duplicates": len(parsed) - added,
            "unknown_models": sorted(unknown),
            "note": ("Priced with a neutral fallback - add these under Setup > Your own "
                     "models for exact costs, or send cost_usd yourself.") if unknown else ""}


def usage_summary() -> dict:
    """Everything the ledger has seen, by model — whichever agent produced it."""
    init()
    rows = db.rows(
        """SELECT model, source, COUNT(*) AS turns,
                  COALESCE(SUM(in_tokens),0) AS in_tokens,
                  COALESCE(SUM(out_tokens),0) AS out_tokens,
                  COALESCE(SUM(cache_read),0) AS cache_read,
                  COALESCE(SUM(cost_usd),0) AS cost, MAX(ts) AS last
           FROM turns GROUP BY model, source ORDER BY cost DESC""")
    by_model: dict[str, dict] = {}
    for r in rows:
        m = by_model.setdefault(r["model"] or "(unknown)", {
            "model": r["model"] or "(unknown)", "turns": 0, "in_tokens": 0, "out_tokens": 0,
            "cache_read": 0, "cost": 0.0, "last": 0.0, "sources": []})
        m["turns"] += r["turns"]; m["in_tokens"] += r["in_tokens"]
        m["out_tokens"] += r["out_tokens"]; m["cache_read"] += r["cache_read"]
        m["cost"] += r["cost"]; m["last"] = max(m["last"], r["last"] or 0)
        m["sources"].append(r["source"])
    out = []
    for m in by_model.values():
        spec = pricing.spec(m["model"])
        out.append({**m, "cost": round(m["cost"], 4), "label": spec.get("label") if pricing.is_known(m["model"]) else m["model"],
                    "vendor": spec.get("vendor"), "known": pricing.is_known(m["model"]),
                    "sources": sorted(set(m["sources"]))})
    out.sort(key=lambda x: -x["cost"])
    return {"models": out,
            "turns": sum(m["turns"] for m in out),
            "cost": round(sum(m["cost"] for m in out), 4)}


def reprice_model(model: str, save) -> int:
    """Run `save()` (which changes the catalogue's price for `model`), then
    recompute the cost of turns that were *computed* from the old price.

    A turn whose stored cost equals what the old catalogue would have produced
    was priced by us and should follow the new price; one that differs was
    given an explicit cost_usd by the agent and is left alone — an agent's own
    number always beats our arithmetic.
    """
    init()
    rows = db.rows(
        """SELECT uuid,in_tokens,out_tokens,cache_read,cache_1h,cost_usd
           FROM turns WHERE source='api' AND model=?""", (model,))

    def cost(r):
        return pricing.cost_usd(model, r["in_tokens"], r["out_tokens"], r["cache_read"], r["cache_1h"])

    old = {r["uuid"]: cost(r) for r in rows}
    save()
    n = 0
    for r in rows:
        if abs(r["cost_usd"] - old[r["uuid"]]) < 1e-6:
            new = round(cost(r), 6)
            if abs(new - r["cost_usd"]) > 1e-9:
                db.run("UPDATE turns SET cost_usd=? WHERE uuid=?", (new, r["uuid"]))
                n += 1
    return n
