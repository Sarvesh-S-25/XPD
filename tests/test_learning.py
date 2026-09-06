"""learning.py: shrinkage, quantiles, the new calibration function, and the
is_demo filtering that keeps sample data out of every real number."""
from __future__ import annotations

import time
import unittest

from promptmeter import db, learning
from tests.helpers import DBTestCase


class QuantileTest(unittest.TestCase):
    def test_empty_is_zero(self):
        self.assertEqual(learning._quantile([], 0.5), 0.0)

    def test_single_value(self):
        self.assertEqual(learning._quantile([7.0], 0.95), 7.0)

    def test_median_of_five(self):
        self.assertEqual(learning._quantile([1, 2, 3, 4, 5], 0.5), 3.0)


class CalibrationFactorTest(DBTestCase):
    """Mirrors the hand-check done during implementation: below the trust
    threshold the prior band is untouched; above it, the blend moves toward
    the observed ratio and only ever moves further as n grows."""

    def _seed(self, n: int, actual_ratio: float) -> None:
        db.run("DELETE FROM iterations")
        db.run("DELETE FROM steps")
        db.run("DELETE FROM projects")
        now = time.time()
        pid = db.run(
            "INSERT INTO projects(name,task_class,created_at,updated_at) "
            "VALUES('t','multi_file_feature',?,?)", (now, now))
        for i in range(n):
            sid = db.run(
                """INSERT INTO steps(project_id,idx,title,model,est_cost_p50,
                       est_cost_p95,status,started_at,ended_at,created_at)
                   VALUES(?,?,?,?,?,?,'done',?,?,?)""",
                (pid, i, f"s{i}", "claude-sonnet-5", 1.0, 1.5, now - 1000, now, now))
            db.run(
                "INSERT INTO iterations(step_id,project_id,n,cost_usd,model,created_at) "
                "VALUES(?,?,1,?,?,?)",
                (sid, pid, actual_ratio, "claude-sonnet-5", now))

    def test_below_threshold_keeps_the_prior_untouched(self):
        self._seed(3, actual_ratio=1.9)
        cal = learning.calibration_factor("multi_file_feature", prior_ratio=1.5)
        self.assertFalse(cal["calibrated"])
        self.assertEqual(cal["ratio"], 1.5)

    def test_at_threshold_blends_toward_observed(self):
        self._seed(8, actual_ratio=1.9)
        cal = learning.calibration_factor("multi_file_feature", prior_ratio=1.5)
        self.assertTrue(cal["calibrated"])
        self.assertGreater(cal["ratio"], 1.5)
        self.assertLess(cal["ratio"], 1.9)

    def test_more_samples_moves_closer_to_observed(self):
        self._seed(8, actual_ratio=1.9)
        at8 = learning.calibration_factor("multi_file_feature", prior_ratio=1.5)
        self._seed(20, actual_ratio=1.9)
        at20 = learning.calibration_factor("multi_file_feature", prior_ratio=1.5)
        self.assertGreater(at20["ratio"], at8["ratio"])

    def test_ratio_never_goes_below_one(self):
        # actual consistently cheaper than predicted p50 must not produce a
        # p95 below p50 — that would be nonsensical.
        self._seed(10, actual_ratio=0.3)
        cal = learning.calibration_factor("multi_file_feature", prior_ratio=1.2)
        self.assertGreaterEqual(cal["ratio"], 1.0)

    def test_a_different_task_class_has_no_history_and_stays_uncalibrated(self):
        self._seed(20, actual_ratio=1.9)
        cal = learning.calibration_factor("qa_explain", prior_ratio=1.1)
        self.assertFalse(cal["calibrated"])


class DemoFilteringTest(DBTestCase):
    """Sample rows loaded by demo.py must never leak into calibration or
    real cost totals (PLS-DO P2)."""

    def _seed_project(self, is_demo: int, cost: float) -> None:
        now = time.time()
        pid = db.run(
            "INSERT INTO projects(name,task_class,is_demo,created_at,updated_at) "
            "VALUES('t','multi_file_feature',?,?,?)", (is_demo, now, now))
        sid = db.run(
            """INSERT INTO steps(project_id,idx,title,model,is_demo,status,created_at)
               VALUES(?,0,'s','claude-sonnet-5',?,'done',?)""",
            (pid, is_demo, now))
        db.run(
            "INSERT INTO iterations(step_id,project_id,n,cost_usd,model,is_demo,created_at) "
            "VALUES(?,?,1,?,?,?,?)",
            (sid, pid, cost, "claude-sonnet-5", is_demo, now))

    def test_model_table_excludes_demo_rows(self):
        self._seed_project(is_demo=1, cost=99.0)
        self.assertEqual(learning.model_table(), [])
        self._seed_project(is_demo=0, cost=0.5)
        table = learning.model_table()
        self.assertEqual(len(table), 1)
        self.assertEqual(table[0]["cost"], 0.5)

    def test_accuracy_excludes_demo_rows(self):
        self._seed_project(is_demo=1, cost=99.0)
        self.assertEqual(learning.accuracy(), [])


class ModelTableWatcherFallbackTest(DBTestCase):
    """PLS-DO A5: a step driven entirely through the automatic watcher (no
    manually-logged iteration) must still show up in model_table(), the same
    way observations() already falls back to the turns ledger."""

    def test_step_with_only_watcher_turns_is_counted(self):
        from promptmeter import watcher
        watcher.init()

        now = time.time()
        pid = db.run(
            "INSERT INTO projects(name,task_class,created_at,updated_at) "
            "VALUES('t','multi_file_feature',?,?)", (now, now))
        sid = db.run(
            """INSERT INTO steps(project_id,idx,title,model,status,started_at,
                   ended_at,created_at)
               VALUES(?,0,'s','claude-opus-5','done',?,?,?)""",
            (pid, now - 100, now, now))
        # No row in `iterations` for this step at all — only the watcher saw it.
        db.run(
            """INSERT INTO turns(uuid,ts,model,in_tokens,out_tokens,cost_usd)
               VALUES('u1',?,'claude-opus-5',1000,500,0.75)""", (now - 50,))

        table = learning.model_table()
        self.assertEqual(len(table), 1)
        self.assertEqual(table[0]["model"], "claude-opus-5")
        self.assertEqual(table[0]["cost"], 0.75)
        self.assertEqual(table[0]["iters"], 1)


if __name__ == "__main__":
    unittest.main()
