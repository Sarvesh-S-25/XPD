"""Vercel entrypoint. Not run locally — `python -m promptmeter` is the local
dev server (see promptmeter/server.py's own `serve()`).

Vercel's Python runtime auto-detects a module-level `handler` class that
inherits http.server.BaseHTTPRequestHandler and calls it directly, so this
file only needs to make the sibling `promptmeter` package importable and
re-export the one Handler that already knows how to run in hosted mode
(db.hosted() is true once Vercel sets VERCEL=1).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promptmeter.server import Handler as handler  # noqa: E402,N813
