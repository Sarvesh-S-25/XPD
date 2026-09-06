"""db.py: schema creation and the migration runner (PLS-DO E6)."""
from __future__ import annotations

import unittest

from promptmeter import db
from tests.helpers import DBTestCase


class MigrationTest(DBTestCase):
    def test_all_migrations_are_applied_on_init(self):
        rows = db.rows("SELECT version FROM schema_version ORDER BY version")
        versions = [r["version"] for r in rows]
        self.assertEqual(versions, [v for v, _ in db.MIGRATIONS])

    def test_running_init_twice_is_a_no_op(self):
        before = db.rows("SELECT * FROM schema_version")
        db.init()
        db.init()
        after = db.rows("SELECT * FROM schema_version")
        self.assertEqual(before, after)

    def test_workspace_id_column_added_with_local_default(self):
        conn = db.connect()
        for table in ("projects", "meter"):
            cols = {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})")}
            self.assertIn("workspace_id", cols)
        pid = db.run(
            "INSERT INTO projects(name,created_at,updated_at) VALUES('t',0,0)")
        row = db.row("SELECT workspace_id FROM projects WHERE id=?", (pid,))
        self.assertEqual(row["workspace_id"], "local")

    def test_settings_table_was_not_restructured(self):
        # A deliberate scope decision (see the plan's "Where I disagree with
        # Gemini"): settings keeps its plain (key) primary key, not
        # (workspace_id, key) — nothing reads workspace_id there yet.
        conn = db.connect()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(settings)")]
        self.assertEqual(cols, ["key", "value"])

    def test_approved_column_defaults_to_not_approved(self):
        pid = db.run(
            "INSERT INTO projects(name,created_at,updated_at) VALUES('t',0,0)")
        sid = db.run(
            "INSERT INTO steps(project_id,created_at) VALUES(?,0)", (pid,))
        row = db.row("SELECT approved FROM steps WHERE id=?", (sid,))
        self.assertEqual(row["approved"], 0)

    def test_is_demo_defaults_to_zero_on_every_flagged_table(self):
        pid = db.run(
            "INSERT INTO projects(name,created_at,updated_at) VALUES('t',0,0)")
        sid = db.run(
            "INSERT INTO steps(project_id,created_at) VALUES(?,0)", (pid,))
        iid = db.run(
            "INSERT INTO iterations(step_id,project_id,created_at) VALUES(?,?,0)",
            (sid, pid))
        mid = db.run(
            "INSERT INTO meter(ts) VALUES(0)")
        self.assertEqual(db.row("SELECT is_demo FROM projects WHERE id=?", (pid,))["is_demo"], 0)
        self.assertEqual(db.row("SELECT is_demo FROM steps WHERE id=?", (sid,))["is_demo"], 0)
        self.assertEqual(db.row("SELECT is_demo FROM iterations WHERE id=?", (iid,))["is_demo"], 0)
        self.assertEqual(db.row("SELECT is_demo FROM meter WHERE id=?", (mid,))["is_demo"], 0)


if __name__ == "__main__":
    unittest.main()
