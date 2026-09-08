"""plain.py: the plain-language layer must not claim a Claude plan window
exists for a model that isn't billed against one (a real bug — GPT/Gemini
estimates showed a "% of your 5-hour window" figure that didn't mean
anything, until this was fixed)."""
from __future__ import annotations

import unittest

from promptmeter import plain


def _est(vendor: str, **overrides) -> dict:
    base = {
        "pp5_p50": 20.0, "pp5_p95": 45.0,
        "turns_p50": 8.0, "turns_p95": 25.0,
        "risk": 0.2, "risk_band": "amber",
        "cost_p50": 1.5, "cost_p95": 4.0,
        "budget_p50": {"total_tokens": 50_000, "context_pct": 10, "compactions": 0},
        "budget_p95": {"total_tokens": 150_000},
        "spec": {"vendor": vendor},
    }
    base.update(overrides)
    return base


class VendorBranchTest(unittest.TestCase):
    def test_anthropic_speaks_in_sessions(self):
        pl = plain.explain(_est("anthropic"), remaining_pp5=100.0)
        self.assertIn("session", pl["size"]["headline"].lower())
        self.assertNotEqual(pl["left"], "")

    def test_non_anthropic_never_mentions_a_session_or_window(self):
        for vendor in ("openai", "google", "local"):
            pl = plain.explain(_est(vendor), remaining_pp5=100.0)
            self.assertNotIn("session", pl["size"]["detail"].lower())
            self.assertNotIn("window", pl["size"]["detail"].lower())
            self.assertNotIn("session", pl["verdict"]["why"].lower())
            self.assertEqual(pl["left"], "",
                             f"{vendor} has no plan window — 'left' must be empty")

    def test_non_anthropic_reports_actual_token_count(self):
        pl = plain.explain(_est("openai"), remaining_pp5=100.0)
        self.assertIn("50,000", pl["size"]["detail"])

    def test_missing_vendor_defaults_to_anthropic_behaviour(self):
        # est["spec"] is always present in real estimator output, but guard
        # the default anyway rather than crashing on a malformed dict.
        est = _est("anthropic")
        del est["spec"]
        pl = plain.explain(est, remaining_pp5=100.0)
        self.assertIn("session", pl["size"]["headline"].lower())


class RiskContextTest(unittest.TestCase):
    """A small worst-case size next to a high interruption risk used to read
    as a contradiction ("Tiny" beside "expect to be cut off") with no
    explanation that the two numbers measure different things. explain()
    must bridge that specific mismatch and stay silent otherwise."""

    def test_small_and_risky_gets_a_bridging_sentence(self):
        pl = plain.explain(_est("anthropic", pp5_p50=1.0, pp5_p95=8.0,
                                 risk_band="critical"), remaining_pp5=100.0)
        self.assertEqual(pl["worst"]["band"], "good")
        self.assertNotEqual(pl["risk_context"], "")
        self.assertIn("nothing here caps", pl["risk_context"])

    def test_small_and_safe_has_no_bridging_sentence(self):
        pl = plain.explain(_est("anthropic", pp5_p50=1.0, pp5_p95=8.0,
                                 risk_band="green"), remaining_pp5=100.0)
        self.assertEqual(pl["risk_context"], "")

    def test_large_and_risky_has_no_bridging_sentence(self):
        # Big AND risky isn't the confusing case — only small-yet-risky is.
        pl = plain.explain(_est("anthropic", pp5_p50=150.0, pp5_p95=250.0,
                                 risk_band="critical"), remaining_pp5=1000.0)
        self.assertEqual(pl["worst"]["band"], "critical")
        self.assertEqual(pl["risk_context"], "")


class SizeGenericTest(unittest.TestCase):
    def test_bands_increase_with_turns(self):
        bands = [plain.size_generic(t, None)["band"] for t in (1, 3, 10, 25, 60, 100)]
        order = {"good": 0, "warning": 1, "critical": 2}
        self.assertEqual(bands, sorted(bands, key=lambda b: order[b]))


class VerdictGenericTest(unittest.TestCase):
    def test_critical_risk_always_says_do_not_send(self):
        v = plain.verdict_generic(turns_p95=5, risk_band="critical")
        self.assertEqual(v["band"], "critical")

    def test_long_run_forces_split_even_at_green_risk(self):
        v = plain.verdict_generic(turns_p95=90, risk_band="green")
        self.assertEqual(v["band"], "critical")

    def test_short_low_risk_run_is_fine(self):
        v = plain.verdict_generic(turns_p95=3, risk_band="green")
        self.assertEqual(v["band"], "good")


if __name__ == "__main__":
    unittest.main()
