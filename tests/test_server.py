"""server.py over real HTTP: a live ThreadingHTTPServer on an ephemeral port
against a throwaway database.

Covers the guards (Host header, CSRF token, path traversal), static serving of
the three frontend files, settings validation, /api/preview's input handling
and the project/step lifecycle — plus the malformed-input cases that used to
500 or drop the connection (non-dict JSON body, non-numeric turn cap or token
counts, bad Content-Length).
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer

from promptmeter import server, watcher
from tests.helpers import DBTestCase


class LiveServerTest(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        watcher.init()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def call(self, method, path, body=None, *, token=True, headers=None, raw=None):
        h = dict(headers or {})
        payload = raw
        if body is not None:
            payload = json.dumps(body)
            h.setdefault("Content-Type", "application/json")
        if token and method != "GET":
            h[server.CSRF_HEADER] = server.CSRF_TOKEN
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=payload, headers=h)
            r = conn.getresponse()
            data = r.read()
            ctype = r.getheader("Content-Type") or ""
            parsed = json.loads(data) if "json" in ctype else data.decode("utf-8", "replace")
            return r.status, parsed, r
        finally:
            conn.close()


class StaticServingTest(LiveServerTest):
    def test_index_is_served_with_html_type(self):
        status, body, r = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", r.getheader("Content-Type"))
        self.assertIn('data-route="plan"', body)

    def test_frontend_assets_have_correct_types(self):
        _, css, rc = self.call("GET", "/styles.css")
        _, js, rj = self.call("GET", "/app.js")
        self.assertIn("text/css", rc.getheader("Content-Type"))
        self.assertIn("javascript", rj.getheader("Content-Type"))
        self.assertIn(".acc-item", css)
        self.assertIn("function accordion", js)

    def test_the_bundled_font_is_served_as_a_font(self):
        # not in every Python's mimetypes table; octet-stream here would make some
        # browsers refuse the face and the whole design falls back to system type
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", "/fonts/archivo-latin.woff2")
            r = conn.getresponse()
            data = r.read()
        finally:
            conn.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Content-Type"), "font/woff2")
        self.assertEqual(data[:4], b"wOF2")

    def test_never_cached(self):
        _, _, r = self.call("GET", "/app.js")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_path_traversal_cannot_read_source(self):
        for p in ("/../promptmeter/db.py", "/..%2fpromptmeter/db.py", "/%2e%2e/promptmeter/db.py"):
            status, body, _ = self.call("GET", p)
            self.assertNotIn("SQLite storage for PromptMeter", str(body), p)

    def test_unknown_asset_falls_back_to_the_app_shell(self):
        status, body, _ = self.call("GET", "/no-such-file.txt")
        self.assertEqual(status, 200)
        self.assertIn("<title>PromptMeter</title>", body)


class GuardsTest(LiveServerTest):
    def test_foreign_host_header_is_rejected(self):
        status, body, _ = self.call("GET", "/api/status", headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)
        self.assertIn("Host", body["error"])

    def test_localhost_host_header_is_accepted(self):
        status, _, _ = self.call("GET", "/api/status", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_mutating_route_needs_the_csrf_token(self):
        status, body, _ = self.call("PATCH", "/api/settings", {"effort": "low"}, token=False)
        self.assertEqual(status, 403)
        self.assertIn("CSRF", body["error"])

    def test_wrong_csrf_token_is_rejected(self):
        status, _, _ = self.call("PATCH", "/api/settings", {"effort": "low"},
                                 token=False, headers={server.CSRF_HEADER: "nope"})
        self.assertEqual(status, 403)

    def test_status_publishes_the_token_the_frontend_needs(self):
        _, body, _ = self.call("GET", "/api/status")
        self.assertEqual(body["csrf_token"], server.CSRF_TOKEN)

    def test_unknown_api_route_is_a_json_404(self):
        status, body, _ = self.call("GET", "/api/definitely-not-a-route")
        self.assertEqual(status, 404)
        self.assertIn("No route", body["error"])

    def test_garbage_content_length_is_a_400_not_a_dropped_connection(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall((f"POST /api/preview HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                       f"{server.CSRF_HEADER}: {server.CSRF_TOKEN}\r\n"
                       "Content-Length: banana\r\n\r\n").encode())
            first = s.recv(4096).split(b"\r\n", 1)[0]
        finally:
            s.close()
        self.assertIn(b"400", first)


class SettingsTest(LiveServerTest):
    def test_windows_provider_round_trips(self):
        for v in ("claude", "other", ""):
            status, body, _ = self.call("PATCH", "/api/settings", {"windows_provider": v})
            self.assertEqual(status, 200)
            _, got, _ = self.call("GET", "/api/settings")
            # '' is "never asked", but an install with real signal auto-answers 'claude'
            if v:
                self.assertEqual(got["windows_provider"], v)

    def test_unknown_windows_provider_is_rejected(self):
        status, _, _ = self.call("PATCH", "/api/settings", {"windows_provider": "gemini"})
        self.assertEqual(status, 400)

    def test_unknown_effort_and_surface_are_rejected(self):
        self.assertEqual(self.call("PATCH", "/api/settings", {"effort": "warp"})[0], 400)
        self.assertEqual(self.call("PATCH", "/api/settings", {"surface": "cowork"})[0], 400)

    def test_fresh_install_with_no_signal_is_not_assumed_to_be_claude(self):
        # No turns, no readings in this throwaway DB — the Windows gate must ask.
        _, got, _ = self.call("GET", "/api/settings")
        self.assertEqual(got["windows_provider"], "")

    def test_existing_signal_auto_answers_claude(self):
        from promptmeter import db
        db.run("INSERT INTO turns(uuid,ts,model,cost_usd) VALUES('u',1,'claude-sonnet-5',0.1)")
        _, got, _ = self.call("GET", "/api/settings")
        self.assertEqual(got["windows_provider"], "claude")


class PreviewTest(LiveServerTest):
    PROMPT = ("Build a small todo web app. - Create the SQLite schema in db.py "
              "- Write a FastAPI backend in api.py - Add pytest tests in test_api.py")

    def test_empty_prompt_is_a_400(self):
        self.assertEqual(self.call("POST", "/api/preview", {"prompt": "   "})[0], 400)

    def test_json_list_body_is_a_400_not_a_500(self):
        status, body, _ = self.call("POST", "/api/preview", raw="[1,2,3]",
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("Prompt", body["error"])

    def test_non_numeric_turn_cap_is_a_400(self):
        status, body, _ = self.call("POST", "/api/preview", {"prompt": self.PROMPT, "turn_cap": "abc"})
        self.assertEqual(status, 400)
        self.assertIn("whole number", body["error"])

    def test_negative_turn_cap_is_a_400(self):
        self.assertEqual(self.call("POST", "/api/preview", {"prompt": self.PROMPT, "turn_cap": -3})[0], 400)

    def test_blank_and_zero_turn_cap_mean_no_cap(self):
        for cap in ("", 0, "0", None):
            status, body, _ = self.call("POST", "/api/preview", {"prompt": self.PROMPT, "turn_cap": cap})
            self.assertEqual(status, 200, cap)
            self.assertEqual(body["estimate"]["features"]["no_turn_cap"], 1)

    def test_a_real_cap_is_applied(self):
        _, body, _ = self.call("POST", "/api/preview", {"prompt": self.PROMPT, "turn_cap": "6"})
        self.assertEqual(body["estimate"]["features"]["no_turn_cap"], 0)
        self.assertLessEqual(body["estimate"]["turns_p95"], 6)

    def test_bare_word_is_flagged_ambiguous_and_a_real_ask_is_not(self):
        _, vague, _ = self.call("POST", "/api/preview", {"prompt": "swiggy"})
        _, clear, _ = self.call("POST", "/api/preview", {"prompt": self.PROMPT})
        self.assertTrue(vague["estimate"]["ambiguous"])
        self.assertFalse(clear["estimate"]["ambiguous"])

    def test_non_claude_model_gets_no_window_percentages_in_prose(self):
        _, body, _ = self.call("POST", "/api/preview", {"prompt": self.PROMPT, "model": "gemini-3.1-pro"})
        text = json.dumps(body["plain"]).lower()
        self.assertNotIn("session", text)
        self.assertNotIn("window", text)

    def test_single_sentence_that_needs_splitting_offers_a_next_step(self):
        _, body, _ = self.call("POST", "/api/preview", {"prompt": "build an app like swiggy"})
        self.assertFalse(body["split"])
        self.assertIn("nothing to cut on", body["split_reason"])
        self.assertFalse(body["can_draft"])          # no provider connected in a fresh DB


class ProjectLifecycleTest(LiveServerTest):
    PROMPT = ("Build a small todo app.\n- Create the SQLite schema in db.py\n"
              "- Write a FastAPI backend in api.py\n- Then add tests in test_api.py\n")

    def _create(self, **extra):
        status, p, _ = self.call("POST", "/api/projects",
                                 {"name": "todo", "prompt": self.PROMPT, "force": "forced", **extra})
        self.assertEqual(status, 200, p)
        return p

    def test_create_lists_and_fetches(self):
        p = self._create()
        _, lst, _ = self.call("GET", "/api/projects?status=active")
        self.assertIn(p["id"], [x["id"] for x in lst["projects"]])
        _, one, _ = self.call("GET", f"/api/projects/{p['id']}")
        self.assertGreaterEqual(len(one["steps"]), 2)
        self.assertEqual(one["done"], 0)

    def test_marking_a_step_done_moves_progress_and_project_status(self):
        p = self._create()
        _, one, _ = self.call("GET", f"/api/projects/{p['id']}")
        sid = one["steps"][0]["id"]
        status, step, _ = self.call("PATCH", f"/api/steps/{sid}", {"status": "done"})
        self.assertEqual(status, 200)
        self.assertEqual(step["status"], "done")
        _, after, _ = self.call("GET", f"/api/projects/{p['id']}")
        self.assertEqual(after["done"], 1)
        self.assertGreater(after["progress"], 0)
        # ...and un-ticking a checkbox (done -> pending) must put it back
        self.call("PATCH", f"/api/steps/{sid}", {"status": "pending"})
        _, undone, _ = self.call("GET", f"/api/projects/{p['id']}")
        self.assertEqual(undone["done"], 0)

    def test_start_sets_started_at_once(self):
        p = self._create()
        _, one, _ = self.call("GET", f"/api/projects/{p['id']}")
        sid = one["steps"][0]["id"]
        _, s1, _ = self.call("PATCH", f"/api/steps/{sid}", {"status": "running"})
        self.assertIsNotNone(s1["started_at"])
        _, s2, _ = self.call("PATCH", f"/api/steps/{sid}", {"status": "running"})
        self.assertEqual(s1["started_at"], s2["started_at"])

    def test_bad_project_turn_cap_is_a_400(self):
        status, _, _ = self.call("POST", "/api/projects",
                                 {"name": "x", "prompt": self.PROMPT, "turn_cap": "lots"})
        self.assertEqual(status, 400)

    def test_iteration_with_non_numeric_tokens_is_a_400(self):
        p = self._create()
        _, one, _ = self.call("GET", f"/api/projects/{p['id']}")
        sid = one["steps"][0]["id"]
        status, body, _ = self.call("POST", f"/api/steps/{sid}/iterate",
                                    {"in_tokens": "a lot", "summary": "x"})
        self.assertEqual(status, 400)
        self.assertIn("in_tokens", body["error"])

    def test_logged_iteration_shows_up_as_step_and_project_spend(self):
        p = self._create()
        _, one, _ = self.call("GET", f"/api/projects/{p['id']}")
        sid = one["steps"][0]["id"]
        self.call("POST", f"/api/steps/{sid}/iterate",
                  {"in_tokens": 10000, "out_tokens": 1000, "summary": "first pass"})
        _, after, _ = self.call("GET", f"/api/projects/{p['id']}")
        step = next(s for s in after["steps"] if s["id"] == sid)
        self.assertGreater(step["spent"], 0)
        self.assertEqual(step["spent"], after["spent"])   # only one step has spend

    def test_archive_then_hard_delete(self):
        p = self._create()
        self.assertEqual(self.call("DELETE", f"/api/projects/{p['id']}")[0], 200)
        _, archived, _ = self.call("GET", "/api/projects?status=archived")
        self.assertIn(p["id"], [x["id"] for x in archived["projects"]])
        self.call("DELETE", f"/api/projects/{p['id']}?hard=1")
        self.assertEqual(self.call("GET", f"/api/projects/{p['id']}")[0], 404)

    def test_unknown_project_is_404(self):
        self.assertEqual(self.call("GET", "/api/projects/99999")[0], 404)


class AnyAgentApiTest(LiveServerTest):
    def test_usage_endpoint_records_a_non_claude_turn(self):
        status, body, _ = self.call("POST", "/api/usage", {"model": "gpt-5.3-codex", "in_tokens": 1000,
                                                          "out_tokens": 100, "agent": "codex"})
        self.assertEqual(status, 200)
        self.assertEqual(body["added"], 1)
        _, summary, _ = self.call("GET", "/api/usage/summary")
        self.assertEqual(summary["models"][0]["model"], "gpt-5.3-codex")
        self.assertEqual(summary["models"][0]["vendor"], "openai")

    def test_usage_accepts_a_batch_and_needs_the_token(self):
        recs = {"records": [{"model": "a", "in_tokens": 1}, {"model": "b", "in_tokens": 1}]}
        self.assertEqual(self.call("POST", "/api/usage", recs, token=False)[0], 403)
        status, body, _ = self.call("POST", "/api/usage", recs)
        self.assertEqual((status, body["added"]), (200, 2))

    def test_usage_rejects_bad_input_with_a_message_not_a_500(self):
        for payload in ({}, {"model": "m", "in_tokens": "x"}, {"model": "m", "in_tokens": -1},
                        {"records": []}, {"records": "nope"}):
            status, body, _ = self.call("POST", "/api/usage", payload)
            self.assertEqual(status, 400, payload)
            self.assertTrue(body["error"])

    def test_reported_usage_never_makes_the_install_look_like_claude(self):
        self.call("POST", "/api/usage", {"model": "gpt-5.3-codex", "in_tokens": 5000})
        _, got, _ = self.call("GET", "/api/settings")
        self.assertEqual(got["windows_provider"], "")

    def test_custom_model_lifecycle(self):
        body = {"id": "mistral-large", "label": "Mistral Large", "vendor": "Mistral",
                "in": 2, "out": 6, "context": 128000}
        status, models, _ = self.call("POST", "/api/models/custom", body)
        self.assertEqual(status, 200)
        added = next(m for m in models["models"] if m["id"] == "mistral-large")
        self.assertTrue(added["custom"])
        self.assertEqual(added["vendor"], "custom-mistral")
        self.assertIn("Mistral", [v["label"] for v in models["vendors"]])
        # it can now be estimated and is priced as itself, with no window prose
        _, prev, _ = self.call("POST", "/api/preview", {"prompt": "fix the typo in auth.py", "model": "mistral-large"})
        self.assertEqual(prev["estimate"]["spec"]["vendor"], "custom-mistral")
        self.assertNotIn("session", json.dumps(prev["plain"]).lower())
        status, after, _ = self.call("DELETE", "/api/models/custom?id=mistral-large")
        self.assertEqual(status, 200)
        self.assertNotIn("mistral-large", [m["id"] for m in after["models"]])
        self.assertEqual(self.call("DELETE", "/api/models/custom?id=mistral-large")[0], 404)

    def test_adding_a_price_reprices_turns_logged_before_it(self):
        self.call("POST", "/api/usage", {"model": "mistral-large", "in_tokens": 1_000_000, "id": "m1"})
        _, before, _ = self.call("GET", "/api/usage/summary")
        self.assertFalse(before["models"][0]["known"])
        status, saved, _ = self.call("POST", "/api/models/custom",
                                     {"id": "mistral-large", "label": "Mistral Large", "vendor": "Mistral",
                                      "in": 0.5, "out": 1})
        self.assertEqual((status, saved["repriced"]), (200, 1))
        _, after, _ = self.call("GET", "/api/usage/summary")
        self.assertTrue(after["models"][0]["known"])
        self.assertEqual(after["models"][0]["label"], "Mistral Large")
        self.assertAlmostEqual(after["models"][0]["cost"], 0.5, places=3)

    def test_custom_model_validation(self):
        base = {"id": "m1", "in": 1, "out": 1}
        for bad in ({**base, "id": ""}, {**base, "id": "has space"}, {**base, "id": "../x"},
                    {**base, "in": -1}, {**base, "out": "cheap"}, {**base, "context": 10}):
            self.assertEqual(self.call("POST", "/api/models/custom", bad)[0], 400, bad)

    def test_unknown_model_estimate_is_not_branded_as_claude(self):
        _, prev, _ = self.call("POST", "/api/preview", {"prompt": "fix the typo in auth.py", "model": "grok-9"})
        self.assertEqual(prev["estimate"]["spec"]["vendor"], "other")
        self.assertNotIn("session", json.dumps(prev["plain"]).lower())

    def test_default_model_follows_usage_not_a_hardcoded_vendor(self):
        _, m0, _ = self.call("GET", "/api/models")
        for i in range(2):
            self.call("POST", "/api/usage", {"model": "gemini-3.1-pro", "in_tokens": 10, "id": f"g{i}"})
        _, m1, _ = self.call("GET", "/api/models")
        self.assertEqual(m1["default_model"], "gemini-3.1-pro")
        _, st, _ = self.call("GET", "/api/settings")
        self.assertEqual(st["model"], "gemini-3.1-pro")


class LimitsApiTest(LiveServerTest):
    def test_get_lists_codex_and_gemini_with_nothing_assumed(self):
        status, body, _ = self.call("GET", "/api/limits")
        self.assertEqual(status, 200)
        self.assertEqual([p["provider"] for p in body["providers"]], ["openai", "google"])
        self.assertTrue(all(w["limit"] is None for p in body["providers"] for w in p["windows"]))

    def test_saving_a_limit_measures_reported_usage_against_it(self):
        for i in range(3):
            self.call("POST", "/api/usage", {"model": "gpt-5.3-codex", "in_tokens": 100, "id": f"u{i}"})
        status, body, _ = self.call("POST", "/api/limits",
                                    {"provider": "openai", "windows": {"5h": {"limit": 12, "unit": "turns"}}})
        self.assertEqual(status, 200)
        w = next(w for p in body["providers"] if p["provider"] == "openai" for w in p["windows"] if w["id"] == "5h")
        self.assertEqual((w["used"], w["limit"], w["pct"]), (3, 12.0, 25.0))

    def test_other_agents_usage_does_not_count_for_this_plan(self):
        self.call("POST", "/api/usage", {"model": "gemini-3.1-pro", "in_tokens": 100, "id": "g1"})
        _, body, _ = self.call("POST", "/api/limits", {"provider": "openai", "windows": {"5h": {"limit": 5}}})
        w = next(w for p in body["providers"] if p["provider"] == "openai" for w in p["windows"] if w["id"] == "5h")
        self.assertEqual(w["used"], 0)

    def test_bad_input_is_a_400_and_needs_the_token(self):
        self.assertEqual(self.call("POST", "/api/limits", {"provider": "openai", "windows": {"5h": {"limit": 1}}},
                                   token=False)[0], 403)
        for payload in ({"provider": "anthropic", "windows": {}}, {"provider": "openai", "windows": {"5h": {"limit": -1}}},
                        {"provider": "openai", "windows": {"5h": {"limit": "x"}}}, {"provider": "", "windows": {}}):
            self.assertEqual(self.call("POST", "/api/limits", payload)[0], 400, payload)


if __name__ == "__main__":
    unittest.main()
