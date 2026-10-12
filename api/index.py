"""Vercel entrypoint. Not run locally — `python -m promptmeter` is the local
dev server (see promptmeter/server.py's own `serve()`).

Vercel resolves the entrypoint by looking for a top-level `app` or `handler`
*declared in this file*; an aliased re-export (`import Handler as handler`)
is not recognised as a declaration, so the name is bound here by a real
subclass of the one Handler that already knows how to run in hosted mode
(db.hosted() is true once Vercel sets VERCEL=1).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promptmeter.server import Handler  # noqa: E402


class handler(Handler):  # noqa: N801 — Vercel requires this exact lowercase name
    pass
