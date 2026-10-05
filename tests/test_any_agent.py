"""Nothing here may assume the user is on Claude.

Covers: an unknown model is not silently priced/branded as Claude; users can add
their own models; any agent can report usage; that usage counts for projects
and history but never leaks into the Claude subscription-window maths; and the
default model is chosen from what the user actually uses, not hard-coded.
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path

from promptmeter import __main__ as cli
from promptmeter import db, meter, planner, pricing, watcher
from tests.helpers import DBTestCase

ROOT = Path(__file__).resolve().parent.parent


class UnknownModelIsNotClaudeTest(DBTestCase):
    def test_unknown_model_has_a_neutral_vendor(self):
        self.assertEqual(pricing.spec("mistral-large")["vendor"], "other")
        self.assertNotEqual(pricing.spec("mistral-large")["vendor"], "anthropic")

    def test_is_known_distinguishes_catalogue_from_fallback(self):
        self.assertTrue(pricing.is_known("claude-sonnet-5"))
        self.assertTrue(pricing.is_known("gpt-5.3-codex"))
        self.assertFalse(pricing.is_known("mistral-large"))
        self.assertFalse(pricing.is_known(""))
        self.assertFalse(pricing.is_known(None))

    def test_unknown_model_never_takes_the_claude_tier(self):
        # tier "opus"/"sonnet"/"haiku" drives routing and Claude-only branches
        self.assertEqual(pricing.spec("mistral-large")["tier"], "custom")


class CustomModelTest(DBTestCase):
    def _add(self, mid="mistral-large", **kw):
        raw = {"label": "Mistral Large", "vendor": "Mistral", "in": 2, "out": 6, **kw}
        cur = dict(db.get_setting("custom_models") or {})
        cur[mid] = raw
        db.set_setting("custom_models", cur)

    def test_custom_model_joins_the_catalogue_with_its_own_vendor(self):
        self._add()
        spec = pricing.spec("mistral-large")
        self.assertTrue(pricing.is_known("mistral-large"))
        self.assertEqual(spec["vendor"], "custom-mistral")
        self.assertEqual((spec["in"], spec["out"]), (2.0, 6.0))
        self.assertEqual(spec["tier"], "custom")

    def test_cost_uses_the_custom_price(self):
        self._add(**{"in": 4, "out": 8})
        # 1M in + 1M out at $4 / $8
        self.assertAlmostEqual(pricing.cost_usd("mistral-large", 1_000_000, 1_000_000), 12.0, places=4)

    def test_custom_vendor_appears_in_the_picker_after_the_builtin_ones(self):
        self._add()
        groups = pricing.by_vendor()
        self.assertEqual(groups[-1]["id"], "custom-mistral")
        self.assertEqual(groups[-1]["label"], "Mistral")
        self.assertEqual([m["id"] for m in groups[-1]["models"]], ["mistral-large"])

    def test_two_custom_vendors_stay_separate_so_routing_never_crosses_them(self):
        self._add("mistral-large")
        self._add("deepseek-v3", label="DeepSeek V3", vendor="DeepSeek")
        self.assertNotEqual(pricing.spec("mistral-large")["vendor"], pricing.spec("deepseek-v3")["vendor"])
        # a cheap step never gets moved onto a different provider's model
        self.assertEqual(planner.route_model("write the readme and a changelog", "mistral-large"), "mistral-large")

    def test_bad_price_is_rejected_and_bad_stored_rows_never_break_the_catalogue(self):
        with self.assertRaises(ValueError):
            pricing.build_custom("m", {"in": -1, "out": 1})
        with self.assertRaises(ValueError):
            pricing.build_custom("m", {"in": "cheap", "out": 1})
        db.set_setting("custom_models", {"broken": {"in": "x"}, "ok": {"in": 1, "out": 1}})
        self.assertIn("ok", pricing.custom_models())
        self.assertNotIn("broken", pricing.custom_models())
        self.assertIn("claude-sonnet-5", pricing.models())          # built-ins untouched


class DefaultModelTest(DBTestCase):
    def test_saved_choice_wins(self):
        db.set_setting("default_model", "gemini-3.1-pro")
        self.assertEqual(pricing.default_model(), "gemini-3.1-pro")

    def test_most_used_model_is_the_default_whoever_the_vendor(self):
        watcher.init()
        for i in range(3):
            watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 10, "id": f"a{i}"}])
        watcher.add_usage([{"model": "gemini-3.1-pro", "in_tokens": 10, "id": "b"}])
        self.assertEqual(pricing.default_model(), "gpt-5.3-codex")

    def test_connected_provider_sets_the_vendor_when_there_is_no_usage(self):
        db.set_setting("provider_config", {"active": "gemini"})
        self.assertEqual(pricing.spec(pricing.default_model())["vendor"], "google")
        db.set_setting("provider_config", {"active": "openai"})
        self.assertEqual(pricing.spec(pricing.default_model())["vendor"], "openai")

    def test_a_stale_saved_model_that_no_longer_exists_is_ignored(self):
        db.set_setting("default_model", "removed-model")
        self.assertIn(pricing.default_model(), pricing.models())

    def test_no_source_file_hardcodes_a_claude_default(self):
        for name in ("promptmeter/server.py", "promptmeter/estimator.py",
                     "promptmeter/planner.py", "promptmeter/learning.py", "web/app.js"):
            src = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn('"claude-sonnet-5"', src, name)
            self.assertNotIn("'claude-sonnet-5'", src, name)


class UsageIngestTest(DBTestCase):
    def setUp(self):
        super().setUp()
        watcher.init()

    def test_a_non_claude_turn_is_recorded_and_priced_from_the_catalogue(self):
        r = watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 1_000_000, "out_tokens": 0}])
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["unknown_models"], [])
        row = db.row("SELECT * FROM turns")
        self.assertEqual(row["source"], "api")
        self.assertAlmostEqual(row["cost_usd"], 1.75, places=3)     # $1.75 / M input

    def test_unknown_model_is_flagged_not_hidden(self):
        r = watcher.add_usage([{"model": "grok-9", "in_tokens": 100}])
        self.assertEqual(r["unknown_models"], ["grok-9"])
        self.assertIn("Your own models", r["note"])

    def test_explicit_cost_wins_over_the_catalogue(self):
        watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 1_000_000, "cost_usd": 0.5}])
        self.assertEqual(db.row("SELECT cost_usd FROM turns")["cost_usd"], 0.5)

    def test_client_id_makes_a_retry_idempotent(self):
        rec = {"model": "gpt-5.3-codex", "in_tokens": 10, "id": "run-1"}
        self.assertEqual(watcher.add_usage([rec])["added"], 1)
        again = watcher.add_usage([rec])
        self.assertEqual((again["added"], again["duplicates"]), (0, 1))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM turns"), 1)

    def test_records_without_an_id_are_distinct(self):
        watcher.add_usage([{"model": "m", "in_tokens": 1}, {"model": "m", "in_tokens": 1}])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM turns"), 2)

    def test_one_bad_record_rejects_the_whole_batch(self):
        with self.assertRaises(ValueError) as cm:
            watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 5}, {"model": "x", "in_tokens": -1}])
        self.assertIn("record 1", str(cm.exception))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM turns"), 0)

    def test_validation(self):
        bad = [
            [],                                             # nothing sent
            [{"in_tokens": 5}],                             # no model
            ["not an object"],
            [{"model": "m", "in_tokens": "lots"}],
            [{"model": "m", "cost_usd": -3}],
            [{"model": "m", "ts": "yesterday"}],
            [{"model": "m", "ts": time.time() + 10 * 86400}],
        ]
        for recs in bad:
            with self.assertRaises(ValueError, msg=str(recs)):
                watcher.add_usage(recs)
        with self.assertRaises(ValueError):
            watcher.add_usage([{"model": "m"}] * (watcher.MAX_BATCH + 1))

    def test_millisecond_timestamps_are_understood(self):
        now = time.time()
        watcher.add_usage([{"model": "m", "in_tokens": 1, "ts": int(now * 1000)}])
        self.assertAlmostEqual(db.row("SELECT ts FROM turns")["ts"], now, delta=2)

    def test_summary_groups_by_model_across_agents(self):
        watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 100, "agent": "codex"},
                           {"model": "gpt-5.3-codex", "in_tokens": 100, "agent": "cursor"},
                           {"model": "grok-9", "in_tokens": 100}])
        db.run("INSERT INTO turns(uuid,ts,model,cost_usd,source) VALUES('t',?, 'claude-sonnet-5',1,'transcript')",
               (time.time(),))
        s = watcher.usage_summary()
        by = {m["model"]: m for m in s["models"]}
        self.assertEqual(by["gpt-5.3-codex"]["turns"], 2)
        self.assertEqual(by["gpt-5.3-codex"]["vendor"], "openai")
        self.assertFalse(by["grok-9"]["known"])
        self.assertEqual(by["grok-9"]["label"], "grok-9")            # its own name, never "unknown"
        self.assertEqual(by["claude-sonnet-5"]["sources"], ["transcript"])
        self.assertEqual(s["turns"], 4)


class PlanWindowIsolationTest(DBTestCase):
    """Reported usage is real spend — but it was not drawn from a Claude
    subscription window and must never be added to one."""

    def setUp(self):
        super().setUp()
        watcher.init()

    def test_other_agents_do_not_move_the_claude_window(self):
        watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 5_000_000, "out_tokens": 1_000_000}])
        s5, s7, n = meter.raw_window_spend()
        self.assertEqual((s5, s7, n), (0.0, 0.0, 0))

    def test_claude_code_transcript_turns_still_do(self):
        db.run("INSERT INTO turns(uuid,ts,model,cost_usd,source) VALUES('c',?, 'claude-sonnet-5', 2.5,'transcript')",
               (time.time() - 60,))
        watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 5_000_000}])
        s5, _s7, n = meter.raw_window_spend()
        self.assertAlmostEqual(s5, 2.5)
        self.assertEqual(n, 1)

    def test_no_claude_signal_from_api_turns_alone(self):
        watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 5}])
        self.assertFalse(meter._has_turns())

    def test_reported_usage_does_count_for_project_spend(self):
        now = time.time()
        pid = db.run("INSERT INTO projects(name,task_class,workdir,created_at,updated_at) "
                     "VALUES('p','multi_file_feature','C:/proj',?,?)", (now, now))
        sid = db.run("""INSERT INTO steps(project_id,idx,title,model,status,started_at,ended_at,created_at)
                        VALUES(?,0,'s','gpt-5.3-codex','done',?,?,?)""", (pid, now - 100, now, now))
        watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 1_000_000, "cwd": "C:/proj", "ts": now - 50}])
        step = db.row("SELECT * FROM steps WHERE id=?", (sid,))
        self.assertAlmostEqual(planner.step_spend(step)["spent"], 1.75, places=3)


class RepriceTest(DBTestCase):
    def setUp(self):
        super().setUp()
        watcher.init()

    def _save(self, **price):
        raw = {"label": "Mistral", "vendor": "Mistral", **price}
        return lambda: db.set_setting("custom_models", {"mistral-large": raw})

    def test_earlier_computed_turns_follow_a_newly_added_price(self):
        watcher.add_usage([{"model": "mistral-large", "in_tokens": 1_000_000, "id": "a"}])   # fallback price
        before = db.row("SELECT cost_usd FROM turns")["cost_usd"]
        n = watcher.reprice_model("mistral-large", self._save(**{"in": 0.5, "out": 1}))
        after = db.row("SELECT cost_usd FROM turns")["cost_usd"]
        self.assertEqual(n, 1)
        self.assertNotAlmostEqual(before, after)
        self.assertAlmostEqual(after, 0.5, places=4)

    def test_an_agents_own_cost_is_never_overwritten(self):
        watcher.add_usage([{"model": "mistral-large", "in_tokens": 1_000_000, "cost_usd": 9.99, "id": "a"}])
        n = watcher.reprice_model("mistral-large", self._save(**{"in": 0.5, "out": 1}))
        self.assertEqual(n, 0)
        self.assertEqual(db.row("SELECT cost_usd FROM turns")["cost_usd"], 9.99)

    def test_only_that_models_turns_are_touched(self):
        watcher.add_usage([{"model": "mistral-large", "in_tokens": 1_000_000, "id": "a"},
                           {"model": "grok-9", "in_tokens": 1_000_000, "id": "b"}])
        other = db.row("SELECT cost_usd FROM turns WHERE model='grok-9'")["cost_usd"]
        watcher.reprice_model("mistral-large", self._save(**{"in": 0.5, "out": 1}))
        self.assertEqual(db.row("SELECT cost_usd FROM turns WHERE model='grok-9'")["cost_usd"], other)

    def test_transcript_turns_are_never_repriced(self):
        db.run("INSERT INTO turns(uuid,ts,model,in_tokens,cost_usd,source) "
               "VALUES('t',?,'mistral-large',1000000,2.0,'transcript')", (time.time(),))
        self.assertEqual(watcher.reprice_model("mistral-large", self._save(**{"in": 0.5, "out": 1})), 0)


class NoClaudeWindowForOtherModelsTest(DBTestCase):
    """A Claude subscription window says nothing about a Gemini/GPT/local run,
    so it must not change that run's risk, its split verdict or its schedule."""

    PROMPT = ("Build a small todo web app. - Create the SQLite schema in db.py "
              "- Write a FastAPI backend in api.py - Build index.html - Add pytest tests in test_api.py")

    def _est(self, model, remaining):
        from promptmeter import estimator
        return estimator.estimate(self.PROMPT, model=model, turn_cap=15,
                                  remaining_pp5=remaining, remaining_pp7=remaining)

    def test_non_claude_risk_ignores_how_full_the_claude_window_is(self):
        roomy, full = self._est("gemini-3.1-pro", 100.0), self._est("gemini-3.1-pro", 0.5)
        self.assertFalse(roomy["plan_metered"])
        self.assertEqual(roomy["risk"], full["risk"])
        self.assertEqual(roomy["risk_band"], full["risk_band"])
        self.assertNotIn("Worst case exceeds what is left in the window",
                         [d["label"] for d in full["risk_drivers"]])

    def test_claude_risk_still_reacts_to_its_own_window(self):
        roomy, full = self._est("claude-sonnet-5", 100.0), self._est("claude-sonnet-5", 0.5)
        self.assertTrue(full["plan_metered"])
        self.assertGreater(full["risk"], roomy["risk"])
        self.assertIn("Worst case exceeds what is left in the window",
                      [d["label"] for d in full["risk_drivers"]])

    def test_custom_and_unknown_models_are_not_plan_metered(self):
        self.assertFalse(self._est("grok-9", 100.0)["plan_metered"])
        db.set_setting("custom_models", {"mistral-large": {"in": 1, "out": 2}})
        self.assertFalse(self._est("mistral-large", 100.0)["plan_metered"])

    def test_split_is_never_forced_by_a_window_the_model_does_not_have(self):
        from promptmeter import estimator
        est = self._est("gemini-3.1-pro", 0.5)
        est = {**est, "risk_band": "green", "turns_p95": 5.0,
               "features": {**est["features"], "verbs": 0, "paths": 0}, "pp5_p95": 999.0}
        split, reason = estimator.should_split(est)
        self.assertFalse(split, reason)
        self.assertNotIn("5-hour", reason)
        # ...while the same numbers for a Claude model do trigger it
        split, reason = estimator.should_split({**est, "plan_metered": True})
        self.assertTrue(split)
        self.assertIn("5-hour", reason)

    def test_preview_has_no_five_hour_schedule_for_other_models(self):
        big = ("Build a food delivery platform with auth, payments, maps, tracking, admin dashboard, "
               "reviews and notifications.\n" + "\n".join(f"- Implement module {i} in mod{i}.py" for i in range(12)))
        other = planner.preview(big, model="gemini-3.1-pro", force="forced")
        self.assertFalse(other["schedule"]["spans_windows"])
        self.assertEqual(other["schedule"]["windows"], [])
        self.assertFalse(other["estimate"]["plan_metered"])


class CliTest(DBTestCase):
    def test_log_usage_records_a_turn(self):
        self.assertEqual(cli.main(["--log-usage", "--model", "gpt-5.3-codex",
                                   "--in-tokens", "100", "--agent", "codex"]), 0)
        self.assertEqual(db.row("SELECT project, source FROM turns")["project"], "codex")

    def test_log_usage_without_a_model_is_a_usage_error(self):
        self.assertEqual(cli.main(["--log-usage"]), 2)

    def test_log_usage_rejects_bad_numbers(self):
        self.assertEqual(cli.main(["--log-usage", "--model", "m", "--in-tokens", "-4"]), 2)

    def test_cli_output_is_safe_on_a_cp1252_console(self):
        # a stray "→" here once crashed printing on a default Windows console
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["--log-usage", "--model", "unheard-of-model", "--in-tokens", "1"])
        buf.getvalue().encode("cp1252")                     # raises if any char is unencodable


if __name__ == "__main__":
    unittest.main()
