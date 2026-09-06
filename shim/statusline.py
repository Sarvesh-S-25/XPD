#!/usr/bin/env python3
"""PromptMeter status line for Claude Code.

Claude Code pipes session JSON to this script on stdin. We forward it to the
local PromptMeter daemon (fire and forget, short timeout) and print a compact
bar. If the daemon is not running, the bar still renders — the shim never
blocks or breaks your session.

Wire it up in ~/.claude/settings.json:

    { "statusLine": { "type": "command",
                      "command": "python \\"<full path>\\\\statusline.py\\"" } }
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

PORT = os.environ.get("PROMPTMETER_PORT", "7777")
ENDPOINT = f"http://127.0.0.1:{PORT}/api/ingest"
TIMEOUT = 0.4
WIDTH = 10
SELFTEST = os.environ.get("PROMPTMETER_SELFTEST") == "1"


def bar(pct: float | None) -> str:
    if pct is None:
        return "----------"
    filled = max(0, min(WIDTH, round(pct / 100 * WIDTH)))
    return "#" * filled + "." * (WIDTH - filled)


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        print("promptmeter")
        return 0

    if not SELFTEST:
        try:
            req = urllib.request.Request(
                ENDPOINT, data=raw.encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=TIMEOUT).read()
        except (urllib.error.URLError, OSError, ValueError):
            pass  # daemon down — never block the session

    rl = (data.get("rate_limits") or {})
    five = (rl.get("five_hour") or {}).get("used_percentage")
    seven = (rl.get("seven_day") or {}).get("used_percentage")
    ctx = (data.get("context_window") or {}).get("used_percentage")
    model = (data.get("model") or {}).get("display_name", "")

    bits = []
    if model:
        bits.append(model)
    if five is not None:
        bits.append(f"5h [{bar(five)}] {five:.0f}%")
    if seven is not None:
        bits.append(f"wk [{bar(seven)}] {seven:.0f}%")
    if ctx is not None:
        bits.append(f"ctx {ctx:.0f}%")
    if not bits:
        bits.append("promptmeter: waiting for first response")
    if SELFTEST:
        bits.append("(self-test OK)")

    print("  ".join(bits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
