"""pricing.py: the cost formula, the model catalogue, and learned thinking_share."""
from __future__ import annotations

import unittest

from promptmeter import pricing
from tests.helpers import DBTestCase


class ModelCatalogueTest(unittest.TestCase):
    """Regression test for the Phase A data fix — these four models were
    stuck at the prior generation's 200k-context/64k-output figures. If this
    ever regresses (a bad hand-edit to models.json, or DEFAULT_MODELS drifting
    from it again), this must fail loudly.
    """

    def test_current_models_have_correct_context_and_output(self):
        expected = {
            "claude-opus-5":    {"context": 1_000_000, "max_output": 128_000},
            "claude-sonnet-5":  {"context": 1_000_000, "max_output": 128_000},
            "claude-sonnet-4-6": {"context": 1_000_000, "max_output": 64_000},
            "claude-fable-5":   {"context": 1_000_000, "max_output": 128_000},
            "claude-haiku-4-5": {"context": 200_000, "max_output": 32_000},
        }
        m = pricing.models()
        for model_id, fields in expected.items():
            for field, value in fields.items():
                self.assertEqual(m[model_id][field], value, f"{model_id}.{field}")

    def test_default_models_fallback_matches_json(self):
        """The in-code fallback must never silently reintroduce stale numbers
        if models.json is ever missing and gets regenerated from it."""
        for model_id in ("claude-opus-5", "claude-sonnet-5", "claude-sonnet-4-6",
                         "claude-fable-5"):
            self.assertEqual(
                pricing.DEFAULT_MODELS[model_id]["context"],
                pricing.models()[model_id]["context"], model_id)
            self.assertEqual(
                pricing.DEFAULT_MODELS[model_id]["max_output"],
                pricing.models()[model_id]["max_output"], model_id)

    def test_prices_are_untouched_by_the_context_fix(self):
        # The fix only touched context/max_output — prices were already right.
        m = pricing.models()
        self.assertEqual(m["claude-opus-5"]["in"], 5.0)
        self.assertEqual(m["claude-opus-5"]["out"], 25.0)
        self.assertEqual(m["claude-sonnet-5"]["in"], 2.0)
        self.assertEqual(m["claude-sonnet-5"]["out"], 10.0)


class CacheEconomicsTest(unittest.TestCase):
    def test_cache_multipliers_match_documented_anthropic_rates(self):
        # 1.25x write for 5-min TTL, 2x for 1-hour TTL, 0.1x read discount —
        # checked against Anthropic's own prompt-caching docs. Do not "fix"
        # these without re-checking the source.
        self.assertEqual(pricing.CACHE["write_5m"], 1.25)
        self.assertEqual(pricing.CACHE["write_1h"], 2.0)
        self.assertEqual(pricing.CACHE["read"], 0.1)


class LoopBudgetTest(unittest.TestCase):
    def test_cost_increases_with_turns(self):
        low = pricing.loop_budget("claude-sonnet-5", 10_000, 3, 1000, 800)
        high = pricing.loop_budget("claude-sonnet-5", 10_000, 15, 1000, 800)
        self.assertGreater(high["cost"], low["cost"])

    def test_context_window_uses_the_corrected_figure(self):
        r = pricing.loop_budget("claude-opus-5", 10_000, 5, 1000, 800)
        self.assertEqual(r["context_window"], 1_000_000)

    def test_compaction_triggers_only_once_peak_exceeds_the_window(self):
        # A run whose peak context comfortably fits should never compact.
        small = pricing.loop_budget("claude-opus-5", 10_000, 5, 1000, 800)
        self.assertEqual(small["compactions"], 0)
        # A run with heavy per-turn growth against a small model should.
        big = pricing.loop_budget("claude-haiku-4-5", 10_000, 200, 5000, 3000)
        self.assertGreater(big["compactions"], 0)

    def test_higher_effort_costs_more(self):
        low = pricing.loop_budget("claude-sonnet-5", 10_000, 5, 1000, 800, effort="low")
        high = pricing.loop_budget("claude-sonnet-5", 10_000, 5, 1000, 800, effort="max")
        self.assertGreater(high["cost"], low["cost"])


class ThinkingShareLearningTest(DBTestCase):
    def test_falls_back_to_static_table_with_no_observations(self):
        self.assertEqual(pricing.thinking_share("claude-opus-5", "high"), 0.60)

    def test_stays_on_static_table_below_the_trust_threshold(self):
        for _ in range(pricing.MIN_THINKING_OBS - 1):
            pricing.learn_thinking_share("claude-opus-5", 0.9)
        self.assertEqual(pricing.thinking_share("claude-opus-5", "high"), 0.60)

    def test_switches_to_learned_value_once_trusted(self):
        for _ in range(pricing.MIN_THINKING_OBS):
            pricing.learn_thinking_share("claude-opus-5", 0.9)
        share = pricing.thinking_share("claude-opus-5", "high")
        self.assertNotEqual(share, 0.60)
        self.assertGreater(share, 0.60)   # EWMA toward 0.9 from a 0.6 static start

    def test_unobserved_model_is_unaffected(self):
        for _ in range(pricing.MIN_THINKING_OBS):
            pricing.learn_thinking_share("claude-opus-5", 0.9)
        self.assertEqual(pricing.thinking_share("claude-haiku-4-5", "high"), 0.45)

    def test_learn_ignores_out_of_range_ratios(self):
        pricing.learn_thinking_share("claude-opus-5", 1.5)   # invalid, > 1.0
        self.assertEqual(pricing.learned_thinking_share("claude-opus-5")["n"], 0)


if __name__ == "__main__":
    unittest.main()
