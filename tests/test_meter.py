"""meter.py: window reconstruction and capacity solving — the two-ledger,
percent-is-ground-truth core of the whole app."""
from __future__ import annotations

import time
import unittest

from promptmeter import db, meter
from tests.helpers import DBTestCase


class CapacityUsdTest(DBTestCase):
    def test_falls_back_to_plan_estimate_with_no_calibration(self):
        cap5, cap7, basis = meter.capacity_usd()
        self.assertEqual(basis, "plan estimate")
        self.assertGreater(cap5, 0)
        self.assertGreater(cap7, 0)

    def test_measured_capacity_wins_once_fitted(self):
        db.set_setting("cap5_fitted", 42.0)
        db.set_setting("cap7_fitted", 300.0)
        cap5, cap7, basis = meter.capacity_usd()
        self.assertEqual(basis, "measured")
        self.assertEqual(cap5, 42.0)
        self.assertEqual(cap7, 300.0)


class CalibrateFromReadingTest(DBTestCase):
    def test_solves_capacity_from_one_real_reading(self):
        from promptmeter import watcher
        watcher.init()
        now = time.time()
        # $2.00 spent in the current 5h window, reading says 20% used ->
        # implied window capacity = 2.00 / 0.20 = $10.00
        db.run("INSERT INTO turns(uuid,ts,cost_usd) VALUES('u1',?,2.0)", (now - 60,))
        out = meter.calibrate_from_reading(pct5=20.0, pct7=None)
        self.assertTrue(out["ok"])
        self.assertAlmostEqual(out["capacity"]["5-hour"], 10.0, places=2)

    def test_skips_when_local_transcripts_account_for_almost_none_of_it(self):
        # A high percentage with ~no measured local spend (Cowork/browser
        # usage) must not fit a capacity to a number transcripts can't see.
        out = meter.calibrate_from_reading(pct5=80.0, pct7=None)
        self.assertTrue(out["ok"])
        self.assertEqual(out["capacity"], {})
        self.assertIn("5-hour", out["skipped"])

    def test_ignores_a_reading_below_the_noise_floor(self):
        out = meter.calibrate_from_reading(pct5=1.0, pct7=None)
        self.assertEqual(out["capacity"], {})


class DerivedWindowTest(DBTestCase):
    def test_no_reading_and_no_turns_returns_none(self):
        self.assertIsNone(meter.derived())

    def test_anchored_reading_ticks_forward_with_new_spend(self):
        from promptmeter import watcher
        watcher.init()
        now = time.time()
        db.set_setting("cap5_fitted", 10.0)
        db.set_setting("cap7_fitted", 70.0)
        db.run(
            """INSERT INTO meter(ts,pct5,pct7,resets5,resets7,session_id)
               VALUES(?,?,?,?,?,'manual')""",
            (now - 120, 20.0, 5.0, now + FIVE_HOURS_MINUS(now, 120), now + 6 * 86400))
        db.run("INSERT INTO turns(uuid,ts,cost_usd) VALUES('u1',?,1.0)", (now - 60,))
        d = meter.derived()
        self.assertIsNotNone(d)
        # $1 more spend against a $10 5h-window = +10 percentage points
        self.assertAlmostEqual(d["pct5"], 30.0, places=1)

    def test_window_resets_to_zero_after_the_reset_time_passes(self):
        now = time.time()
        db.set_setting("cap5_fitted", 10.0)
        db.set_setting("cap7_fitted", 70.0)
        db.run(
            """INSERT INTO meter(ts,pct5,pct7,resets5,resets7,session_id)
               VALUES(?,?,?,?,?,'manual')""",
            (now - 3600, 90.0, 50.0, now - 1, now + 6 * 86400))
        d = meter.derived()
        self.assertIsNotNone(d)
        self.assertEqual(d["pct5"], 0.0)


def FIVE_HOURS_MINUS(now, seconds_ago):
    return meter.FIVE_HOURS - seconds_ago


class RegimeTest(DBTestCase):
    def test_unknown_with_too_few_samples(self):
        self.assertEqual(meter.regime(), "unknown")


if __name__ == "__main__":
    unittest.main()
