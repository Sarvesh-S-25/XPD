"""planner.py: attributed() — turns claimed by a step from the watcher's
ledger — and step_spend()'s combined view.

Covers two real bugs: a demo/sample step could pick up cost from genuine
Claude Code activity elsewhere on the machine because its fabricated
started_at/ended_at window happened to overlap real turns, and a step with a
workdir-scoped project could claim turns from completely unrelated work that
just happened to run at the same time.
"""
from __future__ import annotations

import time
import unittest

from promptmeter import db, planner
from tests.helpers import DBTestCase


class AttributedTest(DBTestCase):
    def setUp(self) -> None:
        super().setUp()
        from promptmeter import watcher
        watcher.init()

    def _project(self, workdir: str = "", is_demo: int = 0) -> int:
        now = time.time()
        return db.run(
            "INSERT INTO projects(name,task_class,workdir,is_demo,created_at,updated_at) "
            "VALUES('t','multi_file_feature',?,?,?,?)",
            (workdir, is_demo, now, now))

    def _step(self, pid: int, start: float, end: float | None, is_demo: int = 0) -> dict:
        sid = db.run(
            """INSERT INTO steps(project_id,idx,title,model,is_demo,status,
                   started_at,ended_at,created_at)
               VALUES(?,0,'s','claude-sonnet-5',?,'done',?,?,?)""",
            (pid, is_demo, start, end, start))
        return db.row("SELECT * FROM steps WHERE id=?", (sid,))

    def _turn(self, ts: float, cwd: str = "", cost: float = 1.0) -> None:
        uid = f"u{ts}-{cwd}"
        db.run(
            """INSERT INTO turns(uuid,ts,cwd,model,in_tokens,out_tokens,cost_usd)
               VALUES(?,?,?,'claude-sonnet-5',1000,500,?)""",
            (uid, ts, cwd, cost))

    def test_demo_step_never_attributes_real_turns(self):
        now = time.time()
        pid = self._project(is_demo=1)
        step = self._step(pid, now - 100, now, is_demo=1)
        self._turn(now - 50)          # real turn, right inside the window
        r = planner.attributed(step)
        self.assertEqual(r["cost"], 0.0)
        self.assertEqual(r["turns"], 0)

    def test_no_workdir_falls_back_to_window_only(self):
        now = time.time()
        pid = self._project(workdir="")
        step = self._step(pid, now - 100, now)
        self._turn(now - 50, cwd="C:/unrelated/project", cost=2.5)
        r = planner.attributed(step)
        self.assertEqual(r["cost"], 2.5)
        self.assertEqual(r["turns"], 1)

    def test_workdir_excludes_turns_from_other_projects(self):
        now = time.time()
        pid = self._project(workdir="C:/Users/me/recipe-app")
        step = self._step(pid, now - 100, now)
        self._turn(now - 60, cwd="C:/Users/me/recipe-app", cost=1.0)
        self._turn(now - 50, cwd="C:/Users/me/some-other-thing", cost=9.0)
        r = planner.attributed(step)
        self.assertEqual(r["cost"], 1.0)
        self.assertEqual(r["turns"], 1)

    def test_workdir_match_is_case_insensitive_and_matches_subfolders(self):
        now = time.time()
        pid = self._project(workdir="C:/Users/me/App")
        step = self._step(pid, now - 100, now)
        self._turn(now - 50, cwd="c:/users/me/app/src", cost=3.0)
        r = planner.attributed(step)
        self.assertEqual(r["cost"], 3.0)


class StepSpendConsistencyTest(DBTestCase):
    """The per-step number the UI shows must be the same figure
    project_progress() sums into the project's total — they used to be two
    different ledgers (logged-only vs. logged+watcher) that could disagree."""

    def test_step_spend_matches_what_project_progress_sums(self):
        from promptmeter import watcher
        watcher.init()
        now = time.time()
        pid = db.run(
            "INSERT INTO projects(name,task_class,workdir,created_at,updated_at) "
            "VALUES('t','multi_file_feature','C:/proj',?,?)", (now, now))
        sid = db.run(
            """INSERT INTO steps(project_id,idx,title,model,est_cost_p50,status,
                   started_at,ended_at,created_at)
               VALUES(?,0,'s','claude-sonnet-5',1.0,'done',?,?,?)""",
            (pid, now - 100, now, now))
        db.run(
            """INSERT INTO turns(uuid,ts,cwd,model,in_tokens,out_tokens,cost_usd)
               VALUES('u1',?,'C:/proj','claude-sonnet-5',1000,500,4.2)""", (now - 50,))
        step = db.row("SELECT * FROM steps WHERE id=?", (sid,))
        per_step = planner.step_spend(step)["spent"]
        total = planner.project_progress(pid)["spent"]
        self.assertEqual(per_step, 4.2)
        self.assertEqual(per_step, total)


if __name__ == "__main__":
    unittest.main()
