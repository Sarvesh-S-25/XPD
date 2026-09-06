"""Wire the status line into Claude Code's settings, safely and reversibly.

Nobody should have to hand-edit JSON to use this. This module works out the real
paths on this machine, merges one key into ~/.claude/settings.json, keeps a
backup, and can undo itself.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MARKER = "promptmeter"


def _norm(s: str) -> str:
    return str(s or "").replace("\\", "/").lower()


def is_ours(command: str | None) -> bool:
    """Is this status line PromptMeter's?

    Match on the script path rather than a magic word — the folder the app lives
    in is whatever the user named it, so a keyword check would miss it. The
    second clause keeps recognising it after the folder is moved or renamed.
    """
    if not command:
        return False
    c = _norm(command)
    return _norm(shim_path()) in c or "shim/statusline.py" in c


# ---------------------------------------------------------------- paths

def claude_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def settings_path() -> Path:
    return claude_dir() / "settings.json"


def shim_path() -> Path:
    return (Path(__file__).resolve().parent.parent / "shim" / "statusline.py")


def python_exe() -> str:
    """The interpreter running this server — the one we know works."""
    exe = sys.executable or "python"
    # pythonw has no console; the shim writes to stdout, so prefer python.exe
    if os.name == "nt" and exe.lower().endswith("pythonw.exe"):
        alt = Path(exe).with_name("python.exe")
        if alt.exists():
            exe = str(alt)
    return exe


def command_string() -> str:
    """The exact command Claude Code should run, quoted for this platform."""
    exe, shim = python_exe(), str(shim_path())
    if os.name == "nt":
        return f'"{exe}" "{shim}"'
    return f'"{exe}" "{shim}"'


def desired_block() -> dict:
    return {"type": "command", "command": command_string()}


# ------------------------------------------------------------- read/write

def _strip_comments(text: str) -> str:
    """Some people keep // comments in settings.json. Tolerate them on read."""
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def read_settings() -> tuple[dict | None, str | None]:
    """Returns (settings, error). settings is {} when the file does not exist."""
    p = settings_path()
    if not p.exists():
        return {}, None
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except OSError as e:
        return None, f"Could not read {p}: {e}"
    if not raw.strip():
        return {}, None
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        try:
            return json.loads(_strip_comments(raw)), None
        except json.JSONDecodeError as e:
            return None, (f"{p} is not valid JSON (line {e.lineno}, column {e.colno}: "
                          f"{e.msg}). Fix or delete that file, then try again — "
                          f"PromptMeter will not overwrite a file it cannot parse.")


def status() -> dict:
    """Everything the Setup page needs to render, with no guessing."""
    s, err = read_settings()
    p = settings_path()
    shim = shim_path()
    current = None
    installed = False
    other = False
    if isinstance(s, dict):
        sl = s.get("statusLine")
        if isinstance(sl, dict):
            current = sl.get("command")
            if is_ours(current):
                installed = True
            elif current:
                other = True
    return {
        "settings_path": str(p),
        "settings_exists": p.exists(),
        "settings_error": err,
        "shim_path": str(shim),
        "shim_exists": shim.exists(),
        "python": python_exe(),
        "command": command_string(),
        "snippet": json.dumps({"statusLine": desired_block()}, indent=2),
        "full_file": json.dumps(_merged(s if isinstance(s, dict) else {}), indent=2),
        "installed": installed,
        "conflict": other,
        "current_command": current,
        "backup": _latest_backup(),
        "platform": "windows" if os.name == "nt" else "unix",
    }


def _merged(s: dict) -> dict:
    out = dict(s)
    out["statusLine"] = desired_block()
    return out


def _latest_backup() -> str | None:
    d = claude_dir()
    if not d.is_dir():
        return None
    backups = sorted(d.glob("settings.promptmeter-backup-*.json"))
    return str(backups[-1]) if backups else None


def install(force: bool = False) -> dict:
    """Merge the statusLine key in, preserving everything else. Backs up first."""
    shim = shim_path()
    if not shim.exists():
        return {"ok": False, "error": f"The shim is missing: {shim}"}

    s, err = read_settings()
    if err:
        return {"ok": False, "error": err}
    s = s or {}

    existing = s.get("statusLine")
    if (isinstance(existing, dict) and existing.get("command")
            and not is_ours(existing.get("command")) and not force):
        return {"ok": False, "conflict": True,
                "error": "You already have a different status line configured.",
                "current_command": existing.get("command")}

    d = claude_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"Could not create {d}: {e}"}

    backup = None
    p = settings_path()
    if p.exists():
        backup = str(d / f"settings.promptmeter-backup-{int(time.time())}.json")
        try:
            shutil.copy2(p, backup)
        except OSError as e:
            return {"ok": False, "error": f"Could not back up {p}: {e}"}

    merged = _merged(s)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        return {"ok": False, "error": f"Could not write {p}: {e}"}

    return {"ok": True, "settings_path": str(p), "backup": backup,
            "command": command_string(), "kept": sorted(k for k in s if k != "statusLine")}


def uninstall() -> dict:
    s, err = read_settings()
    if err:
        return {"ok": False, "error": err}
    if not s or "statusLine" not in s:
        return {"ok": True, "message": "Nothing to remove."}
    if not is_ours((s.get("statusLine") or {}).get("command", "")):
        return {"ok": False, "error": "The configured status line is not PromptMeter's — "
                                      "leaving it alone."}
    s.pop("statusLine", None)
    p = settings_path()
    try:
        p.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"Could not write {p}: {e}"}
    return {"ok": True, "message": "Removed. Restart Claude Code."}


# ---------------------------------------------------------------- test

TEST_PAYLOAD = {
    "session_id": "promptmeter-selftest",
    "model": {"id": "claude-sonnet-5", "display_name": "Sonnet"},
    "rate_limits": {"five_hour": {"used_percentage": 0, "resets_at": 0},
                    "seven_day": {"used_percentage": 0, "resets_at": 0}},
}


def selftest() -> dict:
    """Run the exact configured command with a fake payload.

    The shim honours PROMPTMETER_SELFTEST and does not post, so this proves the
    command works without writing anything to the database.
    """
    shim = shim_path()
    if not shim.exists():
        return {"ok": False, "error": f"The shim is missing: {shim}"}
    env = dict(os.environ, PROMPTMETER_SELFTEST="1")
    try:
        proc = subprocess.run(
            [python_exe(), str(shim)],
            input=json.dumps(TEST_PAYLOAD), text=True,
            capture_output=True, timeout=20, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The shim took too long to respond."}
    except OSError as e:
        return {"ok": False, "error": f"Could not run Python at {python_exe()}: {e}"}

    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "").strip()[-500:] or
                f"Exited with status {proc.returncode}."}
    if not out:
        return {"ok": False, "error": "The shim ran but printed nothing."}
    return {"ok": True, "output": out,
            "message": "The status line command works. Restart Claude Code to start recording."}
