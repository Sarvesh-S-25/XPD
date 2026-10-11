"""Storage for PromptMeter: SQLite locally, Postgres when hosted.

Local (the default): pure stdlib, one file on disk, WAL mode.

Hosted (Vercel): there is no persistent disk, so the same tables live in
Postgres. Each account gets its own Postgres *schema* and every request sets
`search_path` to the caller's schema before any query runs, so the engine's
SQL is unchanged and one person's queries cannot reach another's tables — no
`WHERE user_id = ...` to forget. Shared tables (accounts, sessions, agent
tokens) live in the `pm_shared` schema and are always schema-qualified
(accounts.py). The SQL here is written for SQLite; `_pg()` translates the
handful of dialect differences at this one choke point.

`SCHEMA` below only ever grows new tables (`CREATE TABLE IF NOT EXISTS`) — an
existing table's shape changes through a numbered entry in `MIGRATIONS`, never
by hand-editing a column here, or an upgrading user's real rows get silently
left behind the code that reads them (PLS-DO E6).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

_LOCAL = threading.local()


# ---------------------------------------------------------------- backend

def database_url() -> str:
    """The Postgres URL, or '' for local SQLite.

    PROMPTMETER_DATABASE_URL always counts. The generic DATABASE_URL /
    POSTGRES_URL that Vercel's Postgres integration injects only count on
    Vercel — a DATABASE_URL left over from some other project on this machine
    must not silently move a local install onto a remote database.
    """
    url = os.environ.get("PROMPTMETER_DATABASE_URL", "")
    if not url and os.environ.get("VERCEL") == "1":
        url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    return url


def is_pg() -> bool:
    return bool(database_url())


def hosted() -> bool:
    """Hosted = a multi-user web deployment: accounts, no local-machine features.
    Postgres alone is not hosted (the test suite runs the engine on Postgres)."""
    return os.environ.get("PROMPTMETER_HOSTED") == "1" or os.environ.get("VERCEL") == "1"


_ID_TABLES = {"projects", "steps", "iterations", "meter"}
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _pg(sql: str, has_args: bool) -> str:
    """Translate this codebase's SQLite dialect to Postgres."""
    s = sql
    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", s, re.I):
        s = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", s, flags=re.I)
        s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    s = re.sub(r"\bINTEGER PRIMARY KEY AUTOINCREMENT\b", "BIGSERIAL PRIMARY KEY", s)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r"\bINTEGER\b", "BIGINT", s)
    # `scanned.offset` is a column name here but a reserved word in Postgres.
    # SQL keywords in this codebase are upper case, so lower case is the column.
    s = re.sub(r'(?<!")\boffset\b(?!")', '"offset"', s)
    if has_args:
        s = s.replace("%", "%%").replace("?", "%s")
    return s


def _pg_connect():
    import psycopg
    from decimal import Decimal
    from psycopg.adapt import Loader
    from psycopg.rows import dict_row

    class _NumericLoader(Loader):
        # SUM()/AVG() over BIGINT come back as NUMERIC; the engine does float
        # arithmetic on them and the API serialises them, so never hand it Decimal.
        def load(self, data):
            d = Decimal(bytes(data).decode())
            return int(d) if d == d.to_integral_value() else float(d)

    schema = os.environ.get("PROMPTMETER_PG_SCHEMA", "")    # tests / scripts only
    if schema and not _SCHEMA_NAME.match(schema):
        raise ValueError(f"bad PROMPTMETER_PG_SCHEMA: {schema!r}")
    # prepare_threshold=None: Supabase's pooler (Supavisor, transaction mode)
    # hands each transaction to whichever server connection is free, so a
    # statement prepared on one is unknown on the next. Never prepare.
    conn = psycopg.connect(database_url(), autocommit=True, row_factory=dict_row,
                           prepare_threshold=None, connect_timeout=10)
    conn.adapters.register_loader("numeric", _NumericLoader)
    if schema:
        # A session-level SET, never used by the hosted server (which scopes each
        # request with SET LOCAL instead) — only by the test suite and scripts.
        conn.execute(f"SET search_path TO {schema}")
    return conn


def close() -> None:
    """Drop this thread's connection. The hosted server opens one per request
    and closes it after, so nothing outlives the request that used it."""
    conn = getattr(_LOCAL, "conn", None)
    _LOCAL.conn = None
    _LOCAL.schema = None
    if conn is not None and is_pg():
        try:
            conn.close()
        except Exception:                       # noqa: BLE001 already gone
            pass


class request_scope:
    """One transaction for one web request, pinned to one account's schema.

    `SET LOCAL` lasts only until the transaction ends, so a schema can never
    carry over to the next request on a reused connection — and under a
    transaction-mode pooler the whole request runs on one server connection,
    which a session-level `SET` would not guarantee.
    """

    def __init__(self, schema: str | None):
        if schema is not None and not _SCHEMA_NAME.match(schema):
            raise ValueError(f"bad schema name: {schema!r}")
        self.schema = schema
        self._tx = None

    def __enter__(self):
        conn = connect()
        self._tx = conn.transaction()
        self._tx.__enter__()
        # A schema with no tables: a request that is not signed in can reach
        # nothing at all, rather than whatever search_path happened to be.
        conn.execute(f"SET LOCAL search_path TO {self.schema or 'pm_nobody'}")
        _LOCAL.schema = self.schema
        return self

    def use(self, schema: str) -> None:
        """Re-point the rest of this request at one account's schema."""
        set_local_schema(schema)
        self.schema = schema

    def __exit__(self, *exc):
        try:
            return self._tx.__exit__(*exc)
        finally:
            _LOCAL.schema = None


def set_local_schema(schema: str) -> None:
    """SET LOCAL search_path for the rest of the current transaction, and
    remember it (watcher.init() keys its once-per-database memo on it)."""
    if not _SCHEMA_NAME.match(schema or ""):
        raise ValueError(f"bad schema name: {schema!r}")
    connect().execute(f"SET LOCAL search_path TO {schema}")
    _LOCAL.schema = schema


class _PgConn:
    """The sliver of sqlite3.Connection the migrations use, on Postgres."""

    def __init__(self, conn):
        self._c = conn

    def execute(self, sql: str, args: tuple = ()):
        return self._c.execute(_pg(sql, bool(args)), args or None)

    def executescript(self, script: str) -> None:
        for stmt in (x.strip() for x in script.split(";")):
            if stmt:
                self._c.execute(_pg(stmt, False))

    def commit(self) -> None:
        pass                                    # autocommit


def use_schema(name: str) -> None:
    """Point this thread's connection at one account's schema (Postgres only).
    The name is validated, never interpolated from user text."""
    if not _SCHEMA_NAME.match(name or ""):
        raise ValueError(f"bad schema name: {name!r}")
    connect().execute(f"SET search_path TO {name}")
    _LOCAL.schema = name


def ensure_schema(name: str) -> None:
    if not _SCHEMA_NAME.match(name or ""):
        raise ValueError(f"bad schema name: {name!r}")
    connect().execute(f"CREATE SCHEMA IF NOT EXISTS {name}")


def drop_schema(name: str) -> None:
    if not _SCHEMA_NAME.match(name or "") or name in ("public", "pm_shared"):
        raise ValueError(f"refusing to drop schema {name!r}")
    connect().execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")

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

def _has_column(conn, table: str, col: str) -> bool:
    if isinstance(conn, _PgConn):
        return scalar(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ? AND column_name = ?",
            (table, col), None) is not None
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
    conn = _PgConn(connect()) if is_pg() else connect()
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


def connect():
    """Thread-local connection. The http server is threaded, sqlite objects are not."""
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        return conn
    if is_pg():
        conn = _pg_connect()
        _LOCAL.conn = conn
        return conn
    conn = sqlite3.connect(str(db_path()), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    _LOCAL.conn = conn
    return conn


def init() -> None:
    executescript(SCHEMA)
    migrate()


# ---------------------------------------------------------------- helpers

def _exec(sql: str, args: tuple = ()):
    if is_pg():
        return connect().execute(_pg(sql, bool(args)), args or None)
    return connect().execute(sql, args)


def executescript(script: str) -> None:
    if is_pg():
        _PgConn(connect()).executescript(script)
        return
    conn = connect()
    conn.executescript(script)
    conn.commit()


def rows(sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in _exec(sql, args).fetchall()]


def row(sql: str, args: tuple = ()) -> dict | None:
    r = _exec(sql, args).fetchone()
    return dict(r) if r else None


def run(sql: str, args: tuple = ()) -> int:
    """Execute a write; returns the new row's id for an INSERT into an id table."""
    if is_pg():
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
        if m and m.group(1).lower() in _ID_TABLES and "ON CONFLICT" not in sql.upper():
            r = connect().execute(_pg(sql, bool(args)).rstrip().rstrip(";") + " RETURNING id",
                                  args or None).fetchone()
            return int(r["id"]) if r else 0
        _exec(sql, args)
        return 0
    conn = connect()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid or 0


def run_count(sql: str, args: tuple = ()) -> int:
    """Execute a write; returns the number of rows it changed."""
    cur = _exec(sql, args)
    if not is_pg():
        connect().commit()
    return max(0, cur.rowcount or 0)


def scalar(sql: str, args: tuple = (), default=None):
    r = _exec(sql, args).fetchone()
    if not r:
        return default
    v = next(iter(r.values())) if isinstance(r, dict) else r[0]
    return default if v is None else v


def _missing_table_errors() -> tuple:
    errs = [sqlite3.OperationalError]
    if is_pg():
        import psycopg
        errs.append(psycopg.errors.UndefinedTable)
    return tuple(errs)


def get_setting(key: str, default=None):
    try:
        r = row("SELECT value FROM settings WHERE key=?", (key,))
    except _missing_table_errors():             # init() never ran — table absent
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
