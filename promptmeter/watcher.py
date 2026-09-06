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
