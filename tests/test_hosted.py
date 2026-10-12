"""The hosted website: open to anyone, one shared workspace, and the guards
that remain once sign-in is gone.

The live-server tests need Postgres (the website's database):
  PROMPTMETER_TEST_DATABASE_URL=postgresql://... python -m unittest tests.test_hosted
Without it they are skipped; the pure checks at the bottom always run.
"""
from __future__ import annotations

import http.client
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer

from promptmeter import db, oracles, server, watcher

PG_URL = os.environ.get("PROMPTMETER_TEST_DATABASE_URL", "")


@unittest.skipUnless(PG_URL, "set PROMPTMETER_TEST_DATABASE_URL to a Postgres server to run hosted tests")
class HostedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {k: os.environ.get(k) for k in
                     ("PROMPTMETER_DATABASE_URL", "PROMPTMETER_HOSTED", "PROMPTMETER_PG_SCHEMA")}
        os.environ["PROMPTMETER_DATABASE_URL"] = PG_URL
        os.environ["PROMPTMETER_HOSTED"] = "1"
        os.environ.pop("PROMPTMETER_PG_SCHEMA", None)
        db.close()
        self._wipe()
        server._PUBLIC_READY = False
        watcher._INITED.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self._wipe()
        db.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _wipe(self) -> None:
        conn = db.connect()
        for r in conn.execute("SELECT nspname FROM pg_namespace WHERE nspname IN "
                              "('pm_public', 'pm_shared', 'pm_nobody') OR nspname ~ '^u_[0-9]+$'").fetchall():
            conn.execute(f"DROP SCHEMA {r['nspname']} CASCADE")
        db.close()

    # ------------------------------------------------------------ http
    def call(self, method, path, body=None, *, csrf=None, headers=None, raw=None):
        h = dict(headers or {})
        payload = raw
        if body is not None:
            payload = json.dumps(body)
            h.setdefault("Content-Type", "application/json")
        if csrf:
            h[server.CSRF_HEADER] = csrf
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=payload, headers=h)
            r = conn.getresponse()
            data = r.read()
            ctype = r.getheader("Content-Type") or ""
            parsed = json.loads(data) if "json" in ctype else data.decode("utf-8", "replace")
            return r.status, parsed, r
        finally:
            conn.close()

    def csrf(self) -> str:
        status, st, _ = self.call("GET", "/api/status")
        self.assertEqual(status, 200, st)
        return st["csrf_token"]

    # ------------------------------------------------------------ no sign-in
    def test_anyone_can_use_it_without_signing_in(self):
        status, st, _ = self.call("GET", "/api/status")
        self.assertEqual(status, 200, st)
        self.assertTrue(st["hosted"])
        self.assertTrue(st["csrf_token"])
        self.assertNotIn("account", st)
        self.assertEqual(self.call("GET", "/api/projects")[0], 200)

    def test_the_sign_in_endpoints_are_gone(self):
        for method, path in (("POST", "/api/auth/login"), ("POST", "/api/auth/signup"),
                             ("POST", "/api/auth/verify"), ("POST", "/api/auth/forgot"),
                             ("POST", "/api/auth/reset"), ("POST", "/api/auth/logout"),
                             ("GET", "/api/tokens"), ("POST", "/api/tokens")):
            status, _, _ = self.call(method, path, {} if method == "POST" else None, csrf=self.csrf())
            self.assertEqual(status, 404, f"{method} {path}")

    def test_no_account_tables_are_created(self):
        self.call("GET", "/api/status")
        left = db.connect().execute("SELECT nspname FROM pg_namespace WHERE nspname = 'pm_shared'").fetchall()
        self.assertEqual(left, [])

    # ------------------------------------------------------------ one shared workspace
    def test_every_visitor_sees_the_same_workspace(self):
        csrf = self.csrf()
        _, p, _ = self.call("POST", "/api/projects", {"name": "shared", "prompt": "fix the typo in a.py"},
                            csrf=csrf)
        # A second, entirely separate visitor: no cookie, no session, nothing carried over.
        _, theirs, _ = self.call("GET", "/api/projects?status=all")
        self.assertEqual([x["name"] for x in theirs["projects"]], ["shared"])
        self.assertEqual(self.call("GET", f"/api/projects/{p['id']}")[0], 200)
        # ...and they can delete it, because it is the same workspace.
        self.assertEqual(self.call("DELETE", f"/api/projects/{p['id']}?hard=1", csrf=csrf)[0], 200)
        self.assertEqual(self.call("GET", "/api/projects?status=all")[1]["projects"], [])

    def test_everything_lands_in_the_shared_schema(self):
        self.call("POST", "/api/projects", {"name": "in-public", "prompt": "fix a typo"}, csrf=self.csrf())
        with db.request_scope(server.PUBLIC_SCHEMA):
            names = [r["name"] for r in db.rows("SELECT name FROM projects")]
        self.assertEqual(names, ["in-public"])

    # ------------------------------------------------------------ the guards that remain
    def test_writes_need_the_csrf_token(self):
        body = {"name": "x", "prompt": "fix the typo in a.py"}
        self.assertEqual(self.call("POST", "/api/projects", body)[0], 403)
        self.assertEqual(self.call("POST", "/api/projects", body, csrf="forged")[0], 403)
        self.assertEqual(self.call("POST", "/api/projects", body, csrf=self.csrf())[0], 200)

    def test_writes_must_be_json(self):
        status, _, _ = self.call("POST", "/api/projects", raw="name=x&prompt=y",
                                 csrf=self.csrf(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)

    def test_usage_is_reportable_without_a_browser(self):
        # An agent has no CSRF token to send; this is the one route that accepts that.
        status, _, _ = self.call("POST", "/api/usage", {"model": "gpt-5.3-codex", "in_tokens": 500,
                                                        "out_tokens": 100, "agent": "codex"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("GET", "/api/usage/summary")[1]["turns"], 1)

    def test_machine_features_are_switched_off(self):
        csrf = self.csrf()
        for path in ("/api/setup/install", "/api/setup/scan", "/api/providers", "/api/providers/ollama/check",
                     "/api/ingest", "/api/setup/test"):
            self.assertEqual(self.call("POST", path, {}, csrf=csrf)[0], 403, path)
        _, setup, _ = self.call("GET", "/api/setup")
        self.assertNotIn("settings_path", setup)       # never describes the server's files

    def test_shell_and_file_checks_cannot_be_used(self):
        csrf = self.csrf()
        _, models, _ = self.call("GET", "/api/models")
        self.assertEqual({o["id"] for o in models["oracles"]}, set(oracles.HOSTED_KINDS))
        _, p, _ = self.call("POST", "/api/projects", {"name": "x", "prompt": "Create report.md and data.csv",
                                                      "force": "single"}, csrf=csrf)
        _, proj, _ = self.call("GET", f"/api/projects/{p['id']}")
        step = proj["steps"][0]
        self.assertEqual(step["oracle_kind"], "manual")          # never defaulted to a file check
        status, _, _ = self.call("PATCH", f"/api/steps/{step['id']}",
                                 {"oracle_kind": "command", "oracle_spec": "whoami"}, csrf=csrf)
        self.assertEqual(status, 400)
        self.assertEqual(oracles.check("command", "whoami", approved=True)["satisfied"], 0)

    # ------------------------------------------------------------ the app itself
    def test_the_app_works_for_any_visitor(self):
        csrf = self.csrf()
        status, prev, _ = self.call("POST", "/api/preview",
                                    {"prompt": "fix the typo in a.py", "model": "gemini-3.1-pro"}, csrf=csrf)
        self.assertEqual(status, 200)
        self.assertGreater(prev["estimate"]["cost_p50"], 0)
        self.assertEqual(self.call("POST", "/api/demo", {}, csrf=csrf)[0], 200)
        _, h, _ = self.call("GET", "/api/history")
        self.assertGreaterEqual(len(h["projects"]), 3)

    def test_command_line_reports_to_the_website(self):
        from promptmeter import __main__ as cli
        rc = cli.main(["--log-usage", "--model", "gpt-5.3-codex", "--in-tokens", "500", "--agent", "codex",
                       "--remote", f"http://127.0.0.1:{self.port}"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.call("GET", "/api/usage/summary")[1]["turns"], 1)

    def test_static_files_and_vercel_rewrite_fallback(self):
        status, body, r = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("<title>PromptMeter</title>", body)
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        status, body, _ = self.call("GET", "/api/index?__path=/api/status")
        self.assertEqual(status, 200)                   # routed to /api/status, which anyone may call
        self.assertTrue(body["hosted"])


class PureHostedChecks(unittest.TestCase):
    """No database needed."""

    def test_no_sign_in_routes_are_registered(self):
        paths = [rx.pattern for _, rx, _ in server.ROUTES]
        self.assertEqual([p for p in paths if "/api/auth" in p or "/api/tokens" in p], [])

    def test_only_the_agent_endpoint_skips_csrf(self):
        self.assertEqual(server.CSRF_EXEMPT, {("POST", "/api/usage")})

    def test_schema_names_cannot_be_injected(self):
        db.request_scope(server.PUBLIC_SCHEMA)          # the shared schema is a legal name
        for bad in ("u_1; drop", "Public", "a-b", "", "1; DROP SCHEMA pm_public"):
            with self.assertRaises(ValueError):
                db.request_scope(bad)

    def test_sql_translation(self):
        self.assertEqual(db._pg("SELECT * FROM t WHERE a=? AND b=?", True), "SELECT * FROM t WHERE a=%s AND b=%s")
        self.assertTrue(db._pg("INSERT OR IGNORE INTO t(a) VALUES(?)", True).endswith("ON CONFLICT DO NOTHING"))
        self.assertIn("BIGSERIAL PRIMARY KEY", db._pg("id INTEGER PRIMARY KEY AUTOINCREMENT", False))
        self.assertIn("DOUBLE PRECISION", db._pg("ts REAL NOT NULL", False))
        self.assertIn('"offset"', db._pg("SELECT offset FROM scanned", False))
        self.assertIn("LIKE 'a%%'", db._pg("SELECT 1 WHERE x LIKE 'a%' AND y=?", True))

    def test_local_mode_is_not_hosted_unless_asked(self):
        saved = {k: os.environ.pop(k, None) for k in ("PROMPTMETER_HOSTED", "VERCEL", "DATABASE_URL")}
        try:
            self.assertFalse(db.hosted())
            os.environ["DATABASE_URL"] = "postgresql://somewhere/else"   # another project's variable
            self.assertFalse(db.is_pg() and not os.environ.get("PROMPTMETER_DATABASE_URL"))
            os.environ["VERCEL"] = "1"
            self.assertTrue(db.hosted())
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
