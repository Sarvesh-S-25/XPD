"""The hosted website: accounts, isolation between accounts, and the guards.

The live-server tests need Postgres (the website's database):
  PROMPTMETER_TEST_DATABASE_URL=postgresql://... python -m unittest tests.test_hosted
Without it they are skipped; the pure checks at the bottom always run.

Emails are captured (mailer.TRANSPORT), never sent.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from promptmeter import accounts, db, mailer, oracles, server, watcher

PG_URL = os.environ.get("PROMPTMETER_TEST_DATABASE_URL", "")
APP_URL = "https://pm.example.test"
PW = "correct horse battery"


@unittest.skipUnless(PG_URL, "set PROMPTMETER_TEST_DATABASE_URL to a Postgres server to run hosted tests")
class HostedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {k: os.environ.get(k) for k in
                     ("PROMPTMETER_DATABASE_URL", "PROMPTMETER_HOSTED", "PROMPTMETER_PG_SCHEMA", "APP_URL")}
        os.environ["PROMPTMETER_DATABASE_URL"] = PG_URL
        os.environ["PROMPTMETER_HOSTED"] = "1"
        os.environ["APP_URL"] = APP_URL
        os.environ.pop("PROMPTMETER_PG_SCHEMA", None)
        db.close()
        self._wipe()
        server._SHARED_READY = False
        server._SPACES_READY.clear()
        watcher._INITED.clear()
        self.sent: list = []
        mailer.TRANSPORT = self.sent.append
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        mailer.TRANSPORT = None
        self._wipe()
        db.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _wipe(self) -> None:
        conn = db.connect()
        for r in conn.execute("SELECT nspname FROM pg_namespace WHERE nspname = 'pm_shared' "
                              "OR nspname = 'pm_nobody' OR nspname ~ '^u_[0-9]+$'").fetchall():
            conn.execute(f"DROP SCHEMA {r['nspname']} CASCADE")
        db.close()

    # ------------------------------------------------------------ http
    def call(self, method, path, body=None, *, cookie=None, csrf=None, headers=None, raw=None):
        h = dict(headers or {})
        payload = raw
        if body is not None:
            payload = json.dumps(body)
            h.setdefault("Content-Type", "application/json")
        if cookie:
            h["Cookie"] = f"pm_session={cookie}"
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

    def last_link(self, kind):
        body = self.sent[-1].get_content()
        m = re.search(r"#(verify|reset)/([A-Za-z0-9_-]+)", body)
        self.assertIsNotNone(m, body)
        self.assertEqual(m.group(1), kind)
        return m.group(2)

    def signup_and_verify(self, email="a@example.com"):
        status, body, _ = self.call("POST", "/api/auth/signup", {"email": email, "password": PW})
        self.assertEqual(status, 200, body)
        status, body, r = self.call("POST", "/api/auth/verify", {"token": self.last_link("verify")})
        self.assertEqual(status, 200, body)
        cookie = re.search(r"pm_session=([^;]+)", r.getheader("Set-Cookie")).group(1)
        return cookie, body["csrf_token"]

    # ------------------------------------------------------------ sign-up, verify, login
    def test_signed_out_requests_are_refused(self):
        status, body, _ = self.call("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertTrue(body["auth_required"])
        self.assertEqual(self.call("GET", "/api/projects")[0], 401)

    def test_signup_verify_and_use(self):
        cookie, csrf = self.signup_and_verify()
        status, st, _ = self.call("GET", "/api/status", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(st["hosted"])
        self.assertEqual(st["account"]["email"], "a@example.com")
        self.assertEqual(st["csrf_token"], csrf)

    def test_session_cookie_is_locked_down(self):
        self.call("POST", "/api/auth/signup", {"email": "c@example.com", "password": PW})
        _, _, r = self.call("POST", "/api/auth/verify", {"token": self.last_link("verify")})
        sc = r.getheader("Set-Cookie")
        for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, sc)

    def test_cannot_sign_in_before_verifying(self):
        self.call("POST", "/api/auth/signup", {"email": "b@example.com", "password": PW})
        status, body, _ = self.call("POST", "/api/auth/login", {"email": "b@example.com", "password": PW})
        self.assertEqual(status, 403)
        self.assertIn("Verify", body["error"])

    def test_signup_does_not_reveal_whether_an_email_exists(self):
        _, first, _ = self.call("POST", "/api/auth/signup", {"email": "d@example.com", "password": PW})
        _, again, _ = self.call("POST", "/api/auth/signup", {"email": "d@example.com", "password": "another password!"})
        self.assertEqual(first, again)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM pm_shared.accounts"), 1)

    def test_signup_validation(self):
        self.assertEqual(self.call("POST", "/api/auth/signup", {"email": "nope", "password": PW})[0], 400)
        self.assertEqual(self.call("POST", "/api/auth/signup", {"email": "e@example.com", "password": "short"})[0], 400)
        self.assertEqual(self.sent, [])

    def test_wrong_password_and_unknown_email_get_the_same_answer(self):
        self.signup_and_verify("f@example.com")
        a = self.call("POST", "/api/auth/login", {"email": "f@example.com", "password": "wrong password!!"})
        b = self.call("POST", "/api/auth/login", {"email": "nobody@example.com", "password": "wrong password!!"})
        self.assertEqual((a[0], a[1]), (b[0], b[1]))
        self.assertEqual(a[0], 401)

    def test_login_locks_after_repeated_failures(self):
        self.signup_and_verify("g@example.com")
        for _ in range(accounts.LOCK_AFTER):
            self.call("POST", "/api/auth/login", {"email": "g@example.com", "password": "wrong password!!"})
        status, _, _ = self.call("POST", "/api/auth/login", {"email": "g@example.com", "password": PW})
        self.assertEqual(status, 429)                 # even the right password, while locked

    def test_logout_ends_the_session(self):
        cookie, csrf = self.signup_and_verify()
        _, _, r = self.call("POST", "/api/auth/logout", {}, cookie=cookie)
        self.assertIn("Max-Age=0", r.getheader("Set-Cookie"))
        self.assertEqual(self.call("GET", "/api/status", cookie=cookie)[0], 401)

    # ------------------------------------------------------------ password reset
    def test_password_reset_flow(self):
        old_cookie, _ = self.signup_and_verify("h@example.com")
        status, body, _ = self.call("POST", "/api/auth/forgot", {"email": "h@example.com"})
        self.assertEqual(status, 200)
        token = self.last_link("reset")
        status, _, r = self.call("POST", "/api/auth/reset", {"token": token, "password": "a brand new password"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("GET", "/api/status", cookie=old_cookie)[0], 401)     # other sessions gone
        self.assertEqual(self.call("POST", "/api/auth/reset", {"token": token, "password": "yet another password"})[0], 400)
        self.assertEqual(self.call("POST", "/api/auth/login", {"email": "h@example.com", "password": PW})[0], 401)
        self.assertEqual(self.call("POST", "/api/auth/login",
                                   {"email": "h@example.com", "password": "a brand new password"})[0], 200)

    def test_forgot_does_not_reveal_whether_an_email_exists(self):
        a = self.call("POST", "/api/auth/forgot", {"email": "ghost@example.com"})
        self.signup_and_verify("i@example.com")
        n = len(self.sent)
        b = self.call("POST", "/api/auth/forgot", {"email": "i@example.com"})
        self.assertEqual((a[0], a[1]), (b[0], b[1]))
        self.assertEqual(len(self.sent), n + 1)          # but only the real account got mail

    def test_email_links_use_app_url_never_the_request_host(self):
        self.call("POST", "/api/auth/signup", {"email": "j@example.com", "password": PW},
                  headers={"Host": "evil.example.com"})
        body = self.sent[-1].get_content()
        self.assertIn(APP_URL + "/#verify/", body)
        self.assertNotIn("evil.example.com", body)

    def test_mail_to_one_address_is_rate_limited(self):
        self.call("POST", "/api/auth/signup", {"email": "k@example.com", "password": PW})
        for _ in range(5):
            self.call("POST", "/api/auth/resend", {"email": "k@example.com"})
        self.assertEqual(len(self.sent), accounts.EMAILS_PER_HOUR)

    def test_expired_verify_link_is_rejected(self):
        self.call("POST", "/api/auth/signup", {"email": "l@example.com", "password": PW})
        token = self.last_link("verify")
        db.run("UPDATE pm_shared.email_tokens SET expires_at=?", (time.time() - 1,))
        self.assertEqual(self.call("POST", "/api/auth/verify", {"token": token})[0], 400)

    # ------------------------------------------------------------ guards
    def test_writes_need_the_sessions_csrf_token(self):
        cookie, csrf = self.signup_and_verify()
        body = {"name": "x", "prompt": "fix the typo in a.py"}
        self.assertEqual(self.call("POST", "/api/projects", body, cookie=cookie)[0], 403)
        self.assertEqual(self.call("POST", "/api/projects", body, cookie=cookie, csrf="forged")[0], 403)
        self.assertEqual(self.call("POST", "/api/projects", body, cookie=cookie, csrf=csrf)[0], 200)

    def test_writes_must_be_json(self):
        cookie, csrf = self.signup_and_verify()
        status, _, _ = self.call("POST", "/api/auth/login", raw="email=a&password=b",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)

    def test_machine_features_are_switched_off(self):
        cookie, csrf = self.signup_and_verify()
        for path in ("/api/setup/install", "/api/setup/scan", "/api/providers", "/api/providers/ollama/check",
                     "/api/ingest", "/api/setup/test"):
            self.assertEqual(self.call("POST", path, {}, cookie=cookie, csrf=csrf)[0], 403, path)
        _, setup, _ = self.call("GET", "/api/setup", cookie=cookie)
        self.assertNotIn("settings_path", setup)       # never describes the server's files

    def test_shell_and_file_checks_cannot_be_used(self):
        cookie, csrf = self.signup_and_verify()
        _, models, _ = self.call("GET", "/api/models", cookie=cookie)
        self.assertEqual({o["id"] for o in models["oracles"]}, set(oracles.HOSTED_KINDS))
        _, p, _ = self.call("POST", "/api/projects", {"name": "x", "prompt": "Create report.md and data.csv",
                                                      "force": "single"}, cookie=cookie, csrf=csrf)
        _, proj, _ = self.call("GET", f"/api/projects/{p['id']}", cookie=cookie)
        step = proj["steps"][0]
        self.assertEqual(step["oracle_kind"], "manual")          # never defaulted to a file check
        status, _, _ = self.call("PATCH", f"/api/steps/{step['id']}",
                                 {"oracle_kind": "command", "oracle_spec": "whoami"}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 400)
        self.assertEqual(oracles.check("command", "whoami", approved=True)["satisfied"], 0)

    # ------------------------------------------------------------ isolation
    def test_accounts_cannot_see_each_others_data(self):
        a_cookie, a_csrf = self.signup_and_verify("a@example.com")
        b_cookie, b_csrf = self.signup_and_verify("b2@example.com")
        _, p, _ = self.call("POST", "/api/projects", {"name": "secret", "prompt": "fix the typo in a.py"},
                            cookie=a_cookie, csrf=a_csrf)
        _, mine, _ = self.call("GET", "/api/projects?status=all", cookie=a_cookie)
        _, theirs, _ = self.call("GET", "/api/projects?status=all", cookie=b_cookie)
        self.assertEqual([x["name"] for x in mine["projects"]], ["secret"])
        self.assertEqual(theirs["projects"], [])
        self.assertEqual(self.call("GET", f"/api/projects/{p['id']}", cookie=b_cookie)[0], 404)
        self.assertEqual(self.call("DELETE", f"/api/projects/{p['id']}?hard=1", cookie=b_cookie, csrf=b_csrf)[0], 200)
        _, still, _ = self.call("GET", "/api/projects?status=all", cookie=a_cookie)
        self.assertEqual([x["name"] for x in still["projects"]], ["secret"])   # B's delete touched B's space only

    def test_erase_everything_only_erases_your_own_space(self):
        a_cookie, a_csrf = self.signup_and_verify("a@example.com")
        b_cookie, b_csrf = self.signup_and_verify("b3@example.com")
        tok = self.call("POST", "/api/tokens", {"label": "x"}, cookie=a_cookie, csrf=a_csrf)[1]["token"]
        self.call("POST", "/api/usage", {"model": "gpt-5.3-codex", "in_tokens": 10},
                  headers={"Authorization": f"Bearer {tok}"})
        self.call("POST", "/api/projects", {"name": "keep", "prompt": "fix a typo"}, cookie=a_cookie, csrf=a_csrf)
        self.call("POST", "/api/reset", {"confirm": "RESET_ALL_DATA"}, cookie=b_cookie, csrf=b_csrf)
        self.assertEqual(self.call("GET", "/api/usage/summary", cookie=a_cookie)[1]["turns"], 1)
        self.call("POST", "/api/reset", {"confirm": "RESET_ALL_DATA"}, cookie=a_cookie, csrf=a_csrf)
        self.assertEqual(self.call("GET", "/api/usage/summary", cookie=a_cookie)[1]["turns"], 0)

    # ------------------------------------------------------------ agent tokens
    def test_agent_token_reports_usage_into_its_own_account(self):
        a_cookie, a_csrf = self.signup_and_verify("a@example.com")
        b_cookie, _ = self.signup_and_verify("b4@example.com")
        status, tok, _ = self.call("POST", "/api/tokens", {"label": "codex"}, cookie=a_cookie, csrf=a_csrf)
        self.assertEqual(status, 200)
        self.assertTrue(tok["token"].startswith("pm_"))
        status, res, _ = self.call("POST", "/api/usage", {"model": "gpt-5.3-codex", "in_tokens": 1000},
                                   headers={"Authorization": f"Bearer {tok['token']}"})
        self.assertEqual((status, res["added"]), (200, 1))
        self.assertEqual(self.call("GET", "/api/usage/summary", cookie=a_cookie)[1]["turns"], 1)
        self.assertEqual(self.call("GET", "/api/usage/summary", cookie=b_cookie)[1]["turns"], 0)
        listed = self.call("GET", "/api/tokens", cookie=a_cookie)[1]["tokens"]
        self.assertEqual(listed[0]["label"], "codex")
        self.assertNotIn("token", listed[0])                       # never shown again

    def test_agent_token_only_opens_the_usage_endpoint(self):
        cookie, csrf = self.signup_and_verify()
        tok = self.call("POST", "/api/tokens", {"label": "x"}, cookie=cookie, csrf=csrf)[1]["token"]
        bearer = {"Authorization": f"Bearer {tok}"}
        self.assertEqual(self.call("GET", "/api/projects", headers=bearer)[0], 401)
        self.assertEqual(self.call("POST", "/api/reset", {"confirm": "RESET_ALL_DATA"}, headers=bearer)[0], 401)

    def test_revoked_or_made_up_tokens_are_refused(self):
        cookie, csrf = self.signup_and_verify()
        tok = self.call("POST", "/api/tokens", {"label": "x"}, cookie=cookie, csrf=csrf)[1]
        self.call("DELETE", f"/api/tokens?prefix={tok['prefix']}", cookie=cookie, csrf=csrf)
        for t in (tok["token"], "pm_madeup", "not-even-a-token"):
            status, _, _ = self.call("POST", "/api/usage", {"model": "m", "in_tokens": 1},
                                     headers={"Authorization": f"Bearer {t}"})
            self.assertEqual(status, 401, t)

    def test_tokens_are_stored_only_as_hashes(self):
        cookie, csrf = self.signup_and_verify()
        tok = self.call("POST", "/api/tokens", {"label": "x"}, cookie=cookie, csrf=csrf)[1]["token"]
        stored = [r["token_hash"] for r in db.rows("SELECT token_hash FROM pm_shared.agent_tokens")]
        self.assertNotIn(tok, stored)
        self.assertNotIn(cookie, [r["token_hash"] for r in db.rows("SELECT token_hash FROM pm_shared.sessions")])
        pw = db.scalar("SELECT pw_hash FROM pm_shared.accounts")
        self.assertTrue(pw.startswith("scrypt$"))
        self.assertNotIn(PW, pw)

    def test_command_line_reports_to_the_website_with_a_token(self):
        from promptmeter import __main__ as cli
        cookie, csrf = self.signup_and_verify()
        tok = self.call("POST", "/api/tokens", {"label": "cli"}, cookie=cookie, csrf=csrf)[1]["token"]
        rc = cli.main(["--log-usage", "--model", "gpt-5.3-codex", "--in-tokens", "500", "--agent", "codex",
                       "--remote", f"http://127.0.0.1:{self.port}", "--token", tok])
        self.assertEqual(rc, 0)
        self.assertEqual(self.call("GET", "/api/usage/summary", cookie=cookie)[1]["turns"], 1)
        rc = cli.main(["--log-usage", "--model", "m", "--remote", f"http://127.0.0.1:{self.port}",
                       "--token", "pm_wrong"])
        self.assertEqual(rc, 2)

    # ------------------------------------------------------------ the app itself
    def test_the_app_works_for_a_signed_in_user(self):
        cookie, csrf = self.signup_and_verify()
        status, prev, _ = self.call("POST", "/api/preview", {"prompt": "fix the typo in a.py", "model": "gemini-3.1-pro"},
                                    cookie=cookie, csrf=csrf)
        self.assertEqual(status, 200)
        self.assertGreater(prev["estimate"]["cost_p50"], 0)
        self.assertEqual(self.call("POST", "/api/demo", {}, cookie=cookie, csrf=csrf)[0], 200)
        _, h, _ = self.call("GET", "/api/history", cookie=cookie)
        self.assertGreaterEqual(len(h["projects"]), 3)

    def test_static_files_and_vercel_rewrite_fallback(self):
        status, body, r = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("<title>PromptMeter</title>", body)
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        status, body, _ = self.call("GET", "/api/index?__path=/api/status")
        self.assertEqual(status, 401)                   # routed to /api/status, which needs sign-in


class PureHostedChecks(unittest.TestCase):
    """No database needed."""

    def test_password_hashing(self):
        h = accounts.hash_password("my long password")
        self.assertTrue(accounts.check_password("my long password", h))
        self.assertFalse(accounts.check_password("my long passwor", h))
        self.assertFalse(accounts.check_password("anything", "not-a-hash"))
        self.assertNotEqual(h, accounts.hash_password("my long password"))     # salted

    def test_email_and_password_rules(self):
        self.assertEqual(accounts.normalise_email("  A@Example.COM "), "a@example.com")
        for bad in ("", "a", "a@b", "a b@c.com", "@x.com"):
            with self.assertRaises(accounts.AuthError):
                accounts.normalise_email(bad)
        with self.assertRaises(accounts.AuthError):
            accounts.check_new_password("x" * (accounts.PW_MIN - 1))
        with self.assertRaises(accounts.AuthError):
            accounts.check_new_password("x" * (accounts.PW_MAX + 1))

    def test_schema_names_cannot_be_injected(self):
        self.assertEqual(accounts.schema_for(42), "u_42")
        with self.assertRaises(ValueError):
            accounts.schema_for("1; DROP SCHEMA pm_shared")
        for bad in ("u_1; drop", "Public", "a-b", ""):
            with self.assertRaises(ValueError):
                db.request_scope(bad)

    def test_sql_translation(self):
        self.assertEqual(db._pg("SELECT * FROM t WHERE a=? AND b=?", True), "SELECT * FROM t WHERE a=%s AND b=%s")
        self.assertTrue(db._pg("INSERT OR IGNORE INTO t(a) VALUES(?)", True).endswith("ON CONFLICT DO NOTHING"))
        self.assertIn("BIGSERIAL PRIMARY KEY", db._pg("id INTEGER PRIMARY KEY AUTOINCREMENT", False))
        self.assertIn("DOUBLE PRECISION", db._pg("ts REAL NOT NULL", False))
        self.assertIn('"offset"', db._pg("SELECT offset FROM scanned", False))
        self.assertIn("LIKE 'a%%'", db._pg("SELECT 1 WHERE x LIKE 'a%' AND y=?", True))

    def test_mailer_needs_app_url_and_credentials(self):
        saved = {k: os.environ.pop(k, None) for k in ("APP_URL", "SMTP_USER", "SMTP_PASSWORD")}
        try:
            with self.assertRaises(mailer.MailNotConfigured):
                mailer.app_url()
            os.environ["APP_URL"] = "http://evil.example.com"           # plain http to elsewhere: refused
            with self.assertRaises(mailer.MailNotConfigured):
                mailer.app_url()
            os.environ["APP_URL"] = "https://pm.example.test/"
            self.assertEqual(mailer.app_url(), "https://pm.example.test")
            with self.assertRaises(mailer.MailNotConfigured):
                mailer._config()
            msg = mailer.build("x@example.com", "reset", "TOKEN123")
            self.assertIn("https://pm.example.test/#reset/TOKEN123", msg.get_content())
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_app_password_spaces_are_ignored(self):
        saved = {k: os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD")}
        try:
            os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"] = "me@gmail.com", "abcd efgh ijkl mnop"
            self.assertEqual(mailer._config()["password"], "abcdefghijklmnop")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

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
