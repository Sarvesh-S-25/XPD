"""SQLite storage for PromptMeter.

Pure stdlib. One file on disk, WAL mode. `SCHEMA` below only ever grows new
tables (`CREATE TABLE IF NOT EXISTS`) — an existing table's shape changes
through a numbered entry in `MIGRATIONS`, never by hand-editing a column here,
or an upgrading user's real rows get silently left behind the code that reads
them (PLS-DO E6).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

_LOCAL = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    goal          TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL DEFAULT '',
    workdir       TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT 'claude-sonnet-5',
    task_class    TEXT NOT NULL DEFAULT 'multi_file_feature',
    status        TEXT NOT NULL DEFAULT 'active',      -- active | done | archived
    split_mode    TEXT NOT NULL DEFAULT 'auto',        -- auto | forced | single
    est_pp5       REAL NOT NULL DEFAULT 0,
    est_pp7       REAL NOT NULL DEFAULT 0,
    est_cost_p50  REAL NOT NULL DEFAULT 0,
    est_cost_p95  REAL NOT NULL DEFAULT 0,
    risk          REAL NOT NULL DEFAULT 0,
    risk_band     TEXT NOT NULL DEFAULT 'green',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    closed_at     REAL
);

CREATE TABLE IF NOT EXISTS steps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    idx            INTEGER NOT NULL DEFAULT 0,
    stage          INTEGER NOT NULL DEFAULT 0,
    title          TEXT NOT NULL DEFAULT '',
    prompt         TEXT NOT NULL DEFAULT '',
    depends_on     TEXT NOT NULL DEFAULT '[]',         -- json list of step ids
    artifacts      TEXT NOT NULL DEFAULT '[]',         -- json list of strings
    model          TEXT NOT NULL DEFAULT 'claude-sonnet-5',
    est_input      INTEGER NOT NULL DEFAULT 0,
    est_out_p50    INTEGER NOT NULL DEFAULT 0,
    est_out_p95    INTEGER NOT NULL DEFAULT 0,
    est_turns_p50  REAL NOT NULL DEFAULT 1,
    est_turns_p95  REAL NOT NULL DEFAULT 1,
    est_cost_p50   REAL NOT NULL DEFAULT 0,
    est_cost_p95   REAL NOT NULL DEFAULT 0,
    est_pp5        REAL NOT NULL DEFAULT 0,
    est_pp7        REAL NOT NULL DEFAULT 0,
    risk           REAL NOT NULL DEFAULT 0,
    risk_band      TEXT NOT NULL DEFAULT 'green',
    risk_drivers   TEXT NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'pending',    -- pending|running|done|failed|skipped
    progress       REAL NOT NULL DEFAULT 0,            -- 0..1
    summary        TEXT NOT NULL DEFAULT '',           -- rolling "what was done"
    oracle_kind    TEXT NOT NULL DEFAULT 'manual',
    oracle_spec    TEXT NOT NULL DEFAULT '',
    satisfied      INTEGER NOT NULL DEFAULT 0,         -- -1 fail | 0 unknown | 1 pass
    satisfied_note TEXT NOT NULL DEFAULT '',
    max_iters      INTEGER NOT NULL DEFAULT 5,
    created_at     REAL NOT NULL,
    started_at     REAL,
    ended_at       REAL
);

CREATE TABLE IF NOT EXISTS iterations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id      INTEGER NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    project_id   INTEGER NOT NULL,
    n            INTEGER NOT NULL DEFAULT 1,
    prompt_sent  TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    in_tokens    INTEGER NOT NULL DEFAULT 0,
    out_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read   INTEGER NOT NULL DEFAULT 0,
    cache_write  INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL NOT NULL DEFAULT 0,
    pp5_delta    REAL NOT NULL DEFAULT 0,
    pp7_delta    REAL NOT NULL DEFAULT 0,
    verdict      TEXT NOT NULL DEFAULT 'unknown',      -- pass|fail|unknown
    note         TEXT NOT NULL DEFAULT '',
    est_cost     REAL NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meter (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    pct5       REAL,
    pct7       REAL,
    resets5    REAL,
    resets7    REAL,
    model      TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    in_tokens  INTEGER NOT NULL DEFAULT 0,
    out_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    cost_usd   REAL NOT NULL DEFAULT 0,
    ctx_pct    REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_steps_project    ON steps(project_id);
CREATE INDEX IF NOT EXISTS ix_iters_step       ON iterations(step_id);
CREATE INDEX IF NOT EXISTS ix_iters_project    ON iterations(project_id);
CREATE INDEX IF NOT EXISTS ix_meter_ts         ON meter(ts);
"""


# ------------------------------------------------------------- migrations
#
# SCHEMA above only ever adds new tables. A change to an EXISTING table's
# columns goes here instead, as a new numbered entry — never as an edit to a
# CREATE TABLE statement above, which would do nothing for a database that
# already has that table. Each migration must be safe to run against a
# database that has already had it applied (checked via _has_column), since
# a fresh install runs every migration once, in order, right after SCHEMA.

def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def _migration_001_workspace_id(conn: sqlite3.Connection) -> None:
    """Multi-seat readiness, deliberately narrow: one default-valued column on
    the two tables that have zero existing scoping (`projects`, `meter`).
    Nothing reads this column yet — it exists only so a future multi-seat
    feature can start filling in real values instead of restructuring tables.
    """
    for table in ("projects", "meter"):
        if not _has_column(conn, table, "workspace_id"):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN workspace_id "
                "TEXT NOT NULL DEFAULT 'local'")


def _migration_002_oracle_approval(conn: sqlite3.Connection) -> None:
    """PLS-DO S1's own prescribed fix: a shell-executing deliverable check must
    not run until a person has confirmed it once for that step. Defaults to 0
    (not approved) so every existing step needs the same one-time confirmation
    a new one would.
    """
    if not _has_column(conn, "steps", "approved"):
        conn.execute(
            "ALTER TABLE steps ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")


def _migration_003_is_demo(conn: sqlite3.Connection) -> None:
    """Distinguish seeded sample rows (PLS-DO P2) from real ones, on every
    table demo.py actually writes to, so calibration/cost totals can exclude
    them and the UI can label them.
    """
    for table in ("projects", "steps", "iterations", "meter"):
        if not _has_column(conn, table, "is_demo"):
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_workspace_id),
    (2, _migration_002_oracle_approval),
    (3, _migration_003_is_demo),
]


def migrate() -> None:
    conn = connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
    conn.commit()
    current = scalar("SELECT COALESCE(MAX(version), 0) FROM schema_version", (), 0) or 0
    for version, fn in MIGRATIONS:
        if version <= current:
            continue
        fn(conn)
        conn.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES(?,?)",
            (version, now()))
        conn.commit()


def data_dir() -> Path:
    env = os.environ.get("PROMPTMETER_HOME")
    if env:
        p = Path(env)
    else:
        p = Path.home() / ".promptmeter"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "promptmeter.db"


def connect() -> sqlite3.Connection:
    """Thread-local connection. The http server is threaded, sqlite objects are not."""
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        return conn
    conn = sqlite3.connect(str(db_path()), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    _LOCAL.conn = conn
    return conn


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    migrate()


# ---------------------------------------------------------------- helpers

def rows(sql: str, args: tuple = ()) -> list[dict]:
    cur = connect().execute(sql, args)
    return [dict(r) for r in cur.fetchall()]


def row(sql: str, args: tuple = ()) -> dict | None:
    cur = connect().execute(sql, args)
    r = cur.fetchone()
    return dict(r) if r else None


def run(sql: str, args: tuple = ()) -> int:
    conn = connect()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid or 0


def scalar(sql: str, args: tuple = (), default=None):
    cur = connect().execute(sql, args)
    r = cur.fetchone()
    if not r or r[0] is None:
        return default
    return r[0]


def get_setting(key: str, default=None):
    try:
        r = row("SELECT value FROM settings WHERE key=?", (key,))
    except sqlite3.OperationalError:            # init() never ran — table absent
        return default
    if not r:
        return default
    try:
        return json.loads(r["value"])
    except Exception:
        return default


def set_setting(key: str, value) -> None:
    run(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def now() -> float:
    return time.time()
