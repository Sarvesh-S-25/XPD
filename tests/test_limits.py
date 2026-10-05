"""limits.py: subscription limits for Codex/ChatGPT and Gemini plans.

The plan's numbers are the user's (they are not published as stable figures), so
the module's job is narrow: store what the user says their plan allows, and
measure reported usage from that provider's models against it over the plan's
window. These pin the counting rules and the validation.
"""
from __future__ import annotations

import time
import unittest

from promptmeter import db, limits, watcher
from tests.helpers import DBTestCase


def _turn(ts: float, model: str, cost: float = 0.0, uid: str | None = None) -> None:
    db.run("INSERT INTO turns(uuid,ts,model,cost_usd,source) VALUES(?,?,?,?,'api')",
           (uid or f"{model}-{ts}", ts, model, cost))


def _win(data: dict, provider: str, wid: str) -> dict:
    p = next(x for x in data["providers"] if x["provider"] == provider)
    return next(w for w in p["windows"] if w["id"] == wid)


class DefaultsTest(DBTestCase):
    def setUp(self):
        super().setUp()
        watcher.init()

    def test_codex_and_gemini_are_offered_with_their_own_windows(self):
        d = limits.get()
        self.assertEqual([p["provider"] for p in d["providers"]], ["openai", "google"])
        self.assertEqual([w["id"] for w in d["providers"][0]["windows"]], ["5h", "week"])
        self.assertEqual([w["id"] for w in d["providers"][1]["windows"]], ["day"])
        self.assertEqual([w["hours"] for w in d["providers"][0]["windows"]], [5, 168])

    def test_nothing_is_assumed_about_a_plans_size(self):
        for p in limits.get()["providers"]:
            self.assertFalse(p["configured"])
            for w in p["windows"]:
                self.assertIsNone(w["limit"])
                self.assertIsNone(w["pct"])           # unset means unknown, never "0%"

    def test_claude_is_not_in_this_module(self):
        # Claude's windows come from Claude Code readings (meter.py)
        self.assertNotIn("anthropic", limits.PROVIDERS)


class CountingTest(DBTestCase):
    def setUp(self):
        super().setUp()
        watcher.init()
        self.now = time.time()

    def test_only_turns_inside_the_window_count(self):
        _turn(self.now - 60, "gpt-5.3-codex")                    # inside 5h and week
        _turn(self.now - 6 * 3600, "gpt-5.3-codex")              # outside 5h, inside week
        _turn(self.now - 9 * 86400, "gpt-5.3-codex")             # outside both
        limits.save("openai", {"5h": {"limit": 10}, "week": {"limit": 100}})
        d = limits.get(self.now)
        self.assertEqual(_win(d, "openai", "5h")["used"], 1)
        self.assertEqual(_win(d, "openai", "week")["used"], 2)

    def test_percent_is_used_over_limit(self):
        for i in range(5):
            _turn(self.now - 60 - i, "gpt-5.3-codex")
        limits.save("openai", {"5h": {"limit": 20}})
        w = _win(limits.get(self.now), "openai", "5h")
        self.assertEqual((w["used"], w["limit"], w["pct"]), (5, 20.0, 25.0))

    def test_only_that_providers_models_are_counted(self):
        _turn(self.now - 60, "gpt-5.3-codex")
        _turn(self.now - 60, "gemini-3.1-pro")
        _turn(self.now - 60, "claude-sonnet-5")
        _turn(self.now - 60, "grok-9")                            # unknown model: nobody's plan
        limits.save("openai", {"5h": {"limit": 10}})
        limits.save("google", {"day": {"limit": 10}})
        d = limits.get(self.now)
        self.assertEqual(_win(d, "openai", "5h")["used"], 1)
        self.assertEqual(_win(d, "google", "day")["used"], 1)

    def test_a_custom_model_is_not_silently_counted_against_a_real_plan(self):
        db.set_setting("custom_models", {"mistral-large": {"in": 1, "out": 2}})
        _turn(self.now - 60, "mistral-large")
        limits.save("openai", {"5h": {"limit": 10}})
        self.assertEqual(_win(limits.get(self.now), "openai", "5h")["used"], 0)

    def test_dollars_unit_sums_list_price_cost(self):
        _turn(self.now - 60, "gpt-5.3-codex", cost=1.25, uid="a")
        _turn(self.now - 90, "gpt-5.3-codex", cost=0.75, uid="b")
        limits.save("openai", {"5h": {"limit": 10, "unit": "usd"}})
        w = _win(limits.get(self.now), "openai", "5h")
        self.assertEqual(w["unit"], "usd")
        self.assertAlmostEqual(w["used"], 2.0)
        self.assertAlmostEqual(w["pct"], 20.0)

    def test_usage_is_reported_even_before_a_limit_is_set(self):
        _turn(self.now - 60, "gemini-3.1-pro")
        w = _win(limits.get(self.now), "google", "day")
        self.assertEqual(w["turns"], 1)
        self.assertIsNone(w["pct"])

    def test_frees_at_is_when_the_oldest_counted_turn_leaves_the_window(self):
        _turn(self.now - 3600, "gpt-5.3-codex", uid="old")
        _turn(self.now - 60, "gpt-5.3-codex", uid="new")
        w = _win(limits.get(self.now), "openai", "5h")
        self.assertAlmostEqual(w["frees_at"], self.now - 3600 + 5 * 3600, delta=1)

    def test_no_usage_means_nothing_frees(self):
        self.assertIsNone(_win(limits.get(self.now), "openai", "5h")["frees_at"])

    def test_reported_through_the_ledger_end_to_end(self):
        watcher.add_usage([{"model": "gpt-5.3-codex", "in_tokens": 1000, "id": "x1"},
                           {"model": "gpt-5.3-codex", "in_tokens": 1000, "id": "x2"}])
        limits.save("openai", {"5h": {"limit": 4}})
        self.assertEqual(_win(limits.get(), "openai", "5h")["pct"], 50.0)


class SaveTest(DBTestCase):
    def setUp(self):
        super().setUp()
        watcher.init()

    def test_settings_round_trip_and_default_the_unit_to_turns(self):
        limits.save("google", {"day": {"limit": "1500"}})
        w = _win(limits.get(), "google", "day")
        self.assertEqual((w["limit"], w["unit"]), (1500.0, "turns"))

    def test_a_blank_limit_clears_that_window_only(self):
        limits.save("openai", {"5h": {"limit": 10}, "week": {"limit": 100}})
        limits.save("openai", {"5h": {"limit": ""}})
        d = limits.get()
        self.assertIsNone(_win(d, "openai", "5h")["limit"])
        self.assertEqual(_win(d, "openai", "week")["limit"], 100.0)

    def test_providers_are_independent(self):
        limits.save("openai", {"5h": {"limit": 10}})
        limits.save("google", {"day": {"limit": 20}})
        d = limits.get()
        self.assertEqual(_win(d, "openai", "5h")["limit"], 10.0)
        self.assertEqual(_win(d, "google", "day")["limit"], 20.0)

    def test_validation(self):
        bad = [
            ("anthropic", {"5h": {"limit": 1}}),                  # not this module's
            ("nope", {}),
            ("openai", {"month": {"limit": 1}}),                  # no such window
            ("openai", {"5h": {"limit": "lots"}}),
            ("openai", {"5h": {"limit": -3}}),
            ("openai", {"5h": {"limit": 0}}),
            ("openai", {"5h": {"limit": float("inf")}}),
            ("openai", {"5h": {"limit": 1, "unit": "tokens"}}),
            ("openai", "not an object"),
        ]
        for provider, windows in bad:
            with self.assertRaises(ValueError, msg=str((provider, windows))):
                limits.save(provider, windows)

    def test_one_bad_window_saves_nothing(self):
        limits.save("openai", {"5h": {"limit": 10}})
        with self.assertRaises(ValueError):
            limits.save("openai", {"5h": {"limit": 99}, "week": {"limit": "x"}})
        self.assertEqual(_win(limits.get(), "openai", "5h")["limit"], 10.0)


if __name__ == "__main__":
    unittest.main()
