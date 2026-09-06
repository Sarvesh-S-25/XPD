"""Deliverable oracles — deterministic "is it done?" checks.

This is the answer to "can the iteration loop run without an AI model?". The
control plane is code: a step stops when a machine-checkable condition holds, not
when a model says it looks good. Where no deliverable was specified, a default
oracle is derived from the task class rather than invented by a model.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

TIMEOUT = 120

KINDS = {
    "manual":        "Manual — you mark it satisfied",
    "file_exists":   "File exists and is non-empty",
    "command":       "Command exits 0",
    "tests":         "Test suite passes",
    "contains":      "File contains text / matches regex",
    "json_valid":    "File is valid JSON",
    "no_progress":   "Stop when output stops changing",
}

# Derived default when the user names no deliverable.
CLASS_DEFAULT = {
    "qa_explain":         ("manual", ""),
    "single_file_edit":   ("tests", ""),
    "multi_file_feature": ("tests", ""),
    "build_app":          ("command", ""),
    "research_report":    ("file_exists", ""),
    "refactor":           ("tests", ""),
    "data_task":          ("file_exists", ""),
}

TEST_CMDS = [
    ("pytest.ini", "pytest -q"), ("pyproject.toml", "pytest -q"),
    ("package.json", "npm test --silent"), ("go.mod", "go test ./..."),
    ("Cargo.toml", "cargo test -q"),
]


def default_for(task_class: str, workdir: str = "") -> tuple[str, str]:
    kind, spec = CLASS_DEFAULT.get(task_class, ("manual", ""))
    if kind == "tests":
        cmd = detect_tests(workdir)
        if not cmd:
            return ("manual", "")
        return ("tests", cmd)
    return (kind, spec)


def detect_tests(workdir: str) -> str:
    if not workdir:
        return ""
    root = Path(workdir)
    if not root.is_dir():
        return ""
    for marker, cmd in TEST_CMDS:
        if (root / marker).exists():
            return cmd
    return ""


def _safe_dir(workdir: str) -> str:
    p = Path(workdir).expanduser() if workdir else Path.cwd()
    return str(p) if p.is_dir() else str(Path.cwd())


def check(kind: str, spec: str, workdir: str = "", last_output: str = "",
          prev_output: str = "", *, approved: bool = False) -> dict:
    """Run one oracle. Returns {satisfied: -1|0|1, note, detail}.

    `command`/`tests` run a shell command (PLS-DO S1) — refuse until `approved`
    is set, which the caller only does after a person has confirmed it once
    for this step. A locked-down network path (the CSRF token in server.py)
    stops a forged request from reaching here at all; this stops a person who
    reaches it legitimately from being surprised that a prompt's deliverable
    check executes a shell command.
    """
    kind = (kind or "manual").strip()
    spec = (spec or "").strip()
    cwd = _safe_dir(workdir)

    try:
        if kind == "manual":
            return _r(0, "Manual check — mark it satisfied when you're happy.")

        if kind in ("command", "tests") and not approved:
            return _r(0, "This check runs a shell command and needs a one-time "
                         "confirmation before it can run. Approve it below, then run again.")

        if kind == "file_exists":
            if not spec:
                return _r(0, "No path configured.")
            paths = [s.strip() for s in spec.split(",") if s.strip()]
            missing, small = [], []
            for s in paths:
                f = Path(s) if os.path.isabs(s) else Path(cwd) / s
                if not f.exists():
                    missing.append(s)
                elif f.is_file() and f.stat().st_size < 16:
                    small.append(s)
            if missing:
                return _r(-1, f"Missing: {', '.join(missing)}")
            if small:
                return _r(-1, f"Empty or near-empty: {', '.join(small)}")
            return _r(1, f"All {len(paths)} file(s) present and non-empty.")

        if kind in ("command", "tests"):
            if not spec:
                return _r(0, "No command configured.")
            proc = subprocess.run(spec, shell=True, cwd=cwd, capture_output=True,
                                  text=True, timeout=TIMEOUT)
            tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
            if proc.returncode == 0:
                return _r(1, f"`{spec}` exited 0.", tail)
            return _r(-1, f"`{spec}` exited {proc.returncode}.", tail)

        if kind == "contains":
            if "::" in spec:
                target, pattern = spec.split("::", 1)
            else:
                target, pattern = spec, ""
            f = Path(target.strip()) if os.path.isabs(target.strip()) else Path(cwd) / target.strip()
            if not f.exists():
                return _r(-1, f"{target} does not exist.")
            body = f.read_text(encoding="utf-8", errors="replace")
            if not pattern:
                return _r(1 if body.strip() else -1, "File read.")
            try:
                ok = re.search(pattern, body, re.I | re.M) is not None
            except re.error:
                ok = pattern.lower() in body.lower()
            return _r(1 if ok else -1,
                      f"Pattern {'found' if ok else 'not found'} in {target}.")

        if kind == "json_valid":
            f = Path(spec) if os.path.isabs(spec) else Path(cwd) / spec
            if not f.exists():
                return _r(-1, f"{spec} does not exist.")
            try:
                json.loads(f.read_text(encoding="utf-8"))
                return _r(1, f"{spec} is valid JSON.")
            except Exception as e:
                return _r(-1, f"Invalid JSON: {e}")

        if kind == "no_progress":
            if not last_output or not prev_output:
                return _r(0, "Need two iterations to compare.")
            if last_output.strip() == prev_output.strip():
                return _r(1, "Output stopped changing — loop has converged, stop here.")
            return _r(0, "Output still changing.")

    except subprocess.TimeoutExpired:
        return _r(-1, f"Timed out after {TIMEOUT}s.")
    except Exception as e:                                    # noqa: BLE001
        return _r(0, f"Oracle error: {e}")

    return _r(0, f"Unknown oracle kind '{kind}'.")


def _r(sat: int, note: str, detail: str = "") -> dict:
    return {"satisfied": sat, "note": note, "detail": detail[-3000:]}
