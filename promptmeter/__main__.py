"""Entry point:  python -m promptmeter  [--port 7777] [--no-browser]"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser

from . import db, server


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="promptmeter",
                                 description="Local plan-window budgeting for Claude work.")
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--demo", action="store_true", help="seed sample data and exit")
    ap.add_argument("--where", action="store_true", help="print the data file path and exit")
    ap.add_argument("--connect", action="store_true",
                    help="write the Claude Code status line setting, then exit")
    ap.add_argument("--disconnect", action="store_true",
                    help="remove the status line setting, then exit")
    ap.add_argument("--force", action="store_true",
                    help="with --connect, replace an existing status line")
    args = ap.parse_args(argv)

    db.init()

    if args.where:
        print(db.db_path())
        return 0
    if args.connect or args.disconnect:
        return _connect(args)
    if args.demo:
        from .demo import seed
        print(seed()["message"])
        return 0

    if not args.no_browser:
        url = f"http://{args.host}:{args.port}"
        threading.Thread(
            target=lambda: (time.sleep(1.0), webbrowser.open(url)),
            daemon=True).start()

    try:
        server.serve(args.host, args.port)
    except OSError as e:
        print(f"\n  Could not start on port {args.port}: {e}")
        print(f"  Try:  python -m promptmeter --port {args.port + 1}\n")
        return 1
    return 0


def _connect(args) -> int:
    from . import installer

    if args.disconnect:
        r = installer.uninstall()
        print("\n  " + (r.get("message") or r.get("error") or ""))
        return 0 if r.get("ok") else 1

    st = installer.status()
    print("\n  Connecting PromptMeter to Claude Code")
    print("  " + "-" * 38)
    print(f"  Settings file : {st['settings_path']}")
    print(f"  Python        : {st['python']}")
    print(f"  Script        : {st['shim_path']}")

    r = installer.install(force=args.force)
    if not r.get("ok"):
        print(f"\n  Could not do it: {r.get('error')}")
        if r.get("conflict"):
            print(f"  Your current status line: {r.get('current_command')}")
            print("  Run again with --force to replace it (a backup is saved either way).")
        return 1

    if r.get("backup"):
        print(f"  Backup saved  : {r['backup']}")
    if r.get("kept"):
        print(f"  Kept settings : {', '.join(r['kept'])}")

    t = installer.selftest()
    print(f"\n  Test          : {'OK — ' + t.get('output', '') if t.get('ok') else 'FAILED'}")
    if not t.get("ok"):
        print(f"                  {t.get('error')}")
        print("\n  Usually this means Python is not on your PATH. Reinstall it from")
        print("  python.org and tick 'Add python.exe to PATH' on the first screen.")
        return 1

    print("\n  Done. Now close Claude Code completely, open it again, and send")
    print("  one message. A usage bar appears at the bottom of the session.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
