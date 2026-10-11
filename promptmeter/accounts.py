"""Accounts for the hosted website: sign-up, email verification, sessions,
password reset, login lockout and agent tokens.

Only used when PromptMeter runs as a website (db.hosted()). The local app has no
accounts. Everything here lives in the `pm_shared` Postgres schema and every
query names it explicitly, so it works whatever schema a request is scoped to.
Each account's own data lives in its own schema, `u_<id>` (see db.py).

Secrets are never stored: passwords are scrypt hashes, and session, email and
agent tokens are stored as SHA-256 digests of random values, so a database
leak exposes nothing that can be replayed.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from . import db

SESSION_DAYS = 30
VERIFY_HOURS = 24
RESET_HOURS = 1
LOCK_AFTER = 10                 # failed logins for one email...
LOCK_WINDOW = 15 * 60           # ...within this many seconds lock it...
LOCK_FOR = 15 * 60              # ...for this long
EMAILS_PER_HOUR = 3             # verify/reset mails to one address (protects the Gmail quota)
SIGNUPS_PER_HOUR_PER_IP = 10
PW_MIN, PW_MAX = 10, 128
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

SHARED_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS pm_shared;
CREATE SCHEMA IF NOT EXISTS pm_nobody;
CREATE TABLE IF NOT EXISTS pm_shared.accounts (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    pw_hash     TEXT NOT NULL,
    verified    INTEGER NOT NULL DEFAULT 0,
    created_at  DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS pm_shared.sessions (
    token_hash  TEXT PRIMARY KEY,
    account_id  BIGINT NOT NULL REFERENCES pm_shared.accounts(id) ON DELETE CASCADE,
    csrf        TEXT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL,
    expires_at  DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS pm_shared.email_tokens (
    token_hash  TEXT PRIMARY KEY,
    account_id  BIGINT NOT NULL REFERENCES pm_shared.accounts(id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL,
    expires_at  DOUBLE PRECISION NOT NULL,
    used_at     DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS pm_shared.agent_tokens (
    token_hash  TEXT PRIMARY KEY,
    account_id  BIGINT NOT NULL REFERENCES pm_shared.accounts(id) ON DELETE CASCADE,
    label       TEXT NOT NULL DEFAULT '',
    prefix      TEXT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL,
    last_used   DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS pm_shared.counters (
    key          TEXT PRIMARY KEY,
    n            INTEGER NOT NULL DEFAULT 0,
    window_start DOUBLE PRECISION NOT NULL,
    locked_until DOUBLE PRECISION
)
"""


class AuthError(Exception):
    def __init__(self, status: int, msg: str):
        self.status, self.msg = status, msg
        super().__init__(msg)


# ---------------------------------------------------------------- primitives

def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, maxmem=64 * 1024 * 1024, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                            maxmem=64 * 1024 * 1024, dklen=len(want) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


# Checked against when an email is unknown, so a login for a non-existent
# account takes as long as one for a real account (no timing enumeration).
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def normalise_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL.match(e) or len(e) > 254:
        raise AuthError(400, "Enter a valid email address.")
    return e


def check_new_password(pw: str) -> str:
    if not isinstance(pw, str) or len(pw) < PW_MIN:
        raise AuthError(400, f"Use a password of at least {PW_MIN} characters.")
    if len(pw) > PW_MAX:
        raise AuthError(400, f"Use a password of at most {PW_MAX} characters.")
    return pw


def schema_for(account_id: int) -> str:
    return f"u_{int(account_id)}"


def init_shared() -> None:
    db.executescript(SHARED_SCHEMA)


# ---------------------------------------------------------------- rate limits

def _bump(key: str, limit: int, window: float, now: float) -> bool:
    """Count one event under `key`; False when that goes over `limit` per `window`."""
    r = db.row("SELECT n, window_start FROM pm_shared.counters WHERE key=?", (key,))
    if not r or now - r["window_start"] > window:
        db.run("INSERT INTO pm_shared.counters(key,n,window_start) VALUES(?,1,?) "
               "ON CONFLICT(key) DO UPDATE SET n=1, window_start=excluded.window_start, locked_until=NULL",
               (key, now))
        return True
    if r["n"] >= limit:
        return False
    db.run("UPDATE pm_shared.counters SET n=n+1 WHERE key=?", (key,))
    return True


def _locked(email: str, now: float) -> bool:
    r = db.row("SELECT locked_until FROM pm_shared.counters WHERE key=?", ("login:" + email,))
    return bool(r and r["locked_until"] and r["locked_until"] > now)


def _login_failed(email: str, now: float) -> None:
    key = "login:" + email
    if not _bump(key, LOCK_AFTER - 1, LOCK_WINDOW, now):
        db.run("UPDATE pm_shared.counters SET locked_until=? WHERE key=?", (now + LOCK_FOR, key))


# ---------------------------------------------------------------- accounts

def _account(email: str) -> dict | None:
    return db.row("SELECT * FROM pm_shared.accounts WHERE email=?", (email,))


def _email_token(account_id: int, purpose: str, hours: float, now: float) -> str:
    token = secrets.token_urlsafe(32)
    # one live link per purpose: a newer email makes the older link useless
    db.run("UPDATE pm_shared.email_tokens SET used_at=? WHERE account_id=? AND purpose=? AND used_at IS NULL",
           (now, account_id, purpose))
    db.run("INSERT INTO pm_shared.email_tokens(token_hash,account_id,purpose,expires_at) VALUES(?,?,?,?)",
           (_sha(token), account_id, purpose, now + hours * 3600))
    return token


def _take_email_token(token: str, purpose: str, now: float) -> int:
    r = db.row("SELECT * FROM pm_shared.email_tokens WHERE token_hash=? AND purpose=?",
               (_sha(token or ""), purpose))
    if not r or r["used_at"] is not None or r["expires_at"] < now:
        raise AuthError(400, "That link has expired or was already used. Ask for a new one.")
    db.run("UPDATE pm_shared.email_tokens SET used_at=? WHERE token_hash=?", (now, r["token_hash"]))
    return int(r["account_id"])


def _mail_allowed(email: str, now: float) -> bool:
    return _bump("mail:" + email, EMAILS_PER_HOUR, 3600, now)


def signup(email: str, password: str, ip: str, send) -> dict:
    """Create an account (unverified) and email a verification link.

    The reply is the same whether or not the address already has an account, so
    the form cannot be used to find out who has signed up. An existing,
    unverified account gets a fresh link instead.
    """
    now = time.time()
    email = normalise_email(email)
    check_new_password(password)
    if not _bump("signup-ip:" + (ip or "?"), SIGNUPS_PER_HOUR_PER_IP, 3600, now):
        raise AuthError(429, "Too many sign-ups from this network. Try again in an hour.")
    acct = _account(email)
    if acct is None:
        acct_id = db.scalar("INSERT INTO pm_shared.accounts(email,pw_hash,created_at) VALUES(?,?,?) "
                            "RETURNING id", (email, hash_password(password), now))
        create_space(int(acct_id))
        acct = {"id": acct_id, "verified": 0}
    if not acct["verified"] and _mail_allowed(email, now):
        send(email, "verify", _email_token(int(acct["id"]), "verify", VERIFY_HOURS, now))
    return {"ok": True, "message": "Check your email for a link to finish signing up."}


def create_space(account_id: int) -> None:
    """The account's own schema, with every PromptMeter table in it."""
    from . import watcher
    schema = schema_for(account_id)
    db.ensure_schema(schema)
    db.set_local_schema(schema)
    db.init()
    watcher.init()


def resend_verification(email: str, send) -> dict:
    now = time.time()
    try:
        email = normalise_email(email)
    except AuthError:
        return {"ok": True}
    acct = _account(email)
    if acct and not acct["verified"] and _mail_allowed(email, now):
        send(email, "verify", _email_token(int(acct["id"]), "verify", VERIFY_HOURS, now))
    return {"ok": True, "message": "If that address has an unverified account, a new link is on its way."}


def verify(token: str) -> dict:
    now = time.time()
    acct_id = _take_email_token(token, "verify", now)
    db.run("UPDATE pm_shared.accounts SET verified=1 WHERE id=?", (acct_id,))
    return _new_session(acct_id, now)


def login(email: str, password: str) -> dict:
    now = time.time()
    try:
        email = normalise_email(email)
    except AuthError:
        raise AuthError(401, "Email or password is incorrect.")
    if _locked(email, now):
        raise AuthError(429, "Too many failed attempts. Try again in 15 minutes, or reset your password.")
    acct = _account(email)
    ok = check_password(password or "", acct["pw_hash"] if acct else _DUMMY_HASH)
    if not acct or not ok:
        _login_failed(email, now)
        raise AuthError(401, "Email or password is incorrect.")
    if not acct["verified"]:
        raise AuthError(403, "Verify your email first — use the link we sent you, or ask for a new one.")
    db.run("DELETE FROM pm_shared.counters WHERE key=?", ("login:" + email,))
    return _new_session(int(acct["id"]), now)


def forgot(email: str, send) -> dict:
    now = time.time()
    msg = {"ok": True, "message": "If that address has an account, a reset link is on its way."}
    try:
        email = normalise_email(email)
    except AuthError:
        return msg
    acct = _account(email)
    if acct and _mail_allowed(email, now):
        send(email, "reset", _email_token(int(acct["id"]), "reset", RESET_HOURS, now))
    return msg


def reset(token: str, password: str) -> dict:
    """Set a new password from a reset link. Signs out every other session, and
    counts as email verification (the link proves the person reads that inbox)."""
    now = time.time()
    check_new_password(password)
    acct_id = _take_email_token(token, "reset", now)
    db.run("UPDATE pm_shared.accounts SET pw_hash=?, verified=1 WHERE id=?",
           (hash_password(password), acct_id))
    db.run("DELETE FROM pm_shared.sessions WHERE account_id=?", (acct_id,))
    email = db.scalar("SELECT email FROM pm_shared.accounts WHERE id=?", (acct_id,))
    db.run("DELETE FROM pm_shared.counters WHERE key=?", ("login:" + str(email),))
    return _new_session(acct_id, now)


# ---------------------------------------------------------------- sessions

def _new_session(account_id: int, now: float) -> dict:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    db.run("INSERT INTO pm_shared.sessions(token_hash,account_id,csrf,created_at,expires_at) "
           "VALUES(?,?,?,?,?)", (_sha(token), account_id, csrf, now, now + SESSION_DAYS * 86400))
    db.run("DELETE FROM pm_shared.sessions WHERE expires_at < ?", (now,))
    return {"token": token, "csrf": csrf, "account_id": account_id, "max_age": SESSION_DAYS * 86400}


def session(token: str) -> dict | None:
    """{account_id, email, csrf} for a live session of a verified account, else None."""
    if not token:
        return None
    r = db.row("""SELECT s.account_id, s.csrf, s.expires_at, a.email, a.verified
                  FROM pm_shared.sessions s JOIN pm_shared.accounts a ON a.id = s.account_id
                  WHERE s.token_hash=?""", (_sha(token),))
    if not r or r["expires_at"] < time.time() or not r["verified"]:
        return None
    return {"account_id": int(r["account_id"]), "email": r["email"], "csrf": r["csrf"]}


def logout(token: str) -> None:
    if token:
        db.run("DELETE FROM pm_shared.sessions WHERE token_hash=?", (_sha(token),))


# ---------------------------------------------------------------- agent tokens

def create_agent_token(account_id: int, label: str) -> dict:
    token = "pm_" + secrets.token_urlsafe(32)
    label = (label or "").strip()[:60] or "agent"
    if (db.scalar("SELECT COUNT(*) FROM pm_shared.agent_tokens WHERE account_id=?", (account_id,), 0) or 0) >= 25:
        raise AuthError(400, "You already have 25 agent tokens — revoke some first.")
    db.run("INSERT INTO pm_shared.agent_tokens(token_hash,account_id,label,prefix,created_at) VALUES(?,?,?,?,?)",
           (_sha(token), account_id, label, token[:10], time.time()))
    return {"token": token, "label": label, "prefix": token[:10]}


def list_agent_tokens(account_id: int) -> list[dict]:
    return db.rows("SELECT prefix, label, created_at, last_used FROM pm_shared.agent_tokens "
                   "WHERE account_id=? ORDER BY created_at DESC", (account_id,))


def revoke_agent_token(account_id: int, prefix: str) -> bool:
    return db.run_count("DELETE FROM pm_shared.agent_tokens WHERE account_id=? AND prefix=?",
                        (account_id, prefix)) > 0


def account_for_agent_token(token: str) -> int | None:
    if not token or not token.startswith("pm_"):
        return None
    r = db.row("""SELECT t.account_id, a.verified FROM pm_shared.agent_tokens t
                  JOIN pm_shared.accounts a ON a.id = t.account_id WHERE t.token_hash=?""", (_sha(token),))
    if not r or not r["verified"]:
        return None
    db.run("UPDATE pm_shared.agent_tokens SET last_used=? WHERE token_hash=?", (time.time(), _sha(token)))
    return int(r["account_id"])
