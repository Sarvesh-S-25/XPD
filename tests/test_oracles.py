"""oracles.py: deliverable checks, and the shell-oracle approval gate (PLS-DO S1)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from promptmeter import oracles


class ApprovalGateTest(unittest.TestCase):
    def test_unapproved_command_refuses_to_run(self):
        r = oracles.check("command", "echo hi", approved=False)
        self.assertEqual(r["satisfied"], 0)
        self.assertIn("confirmation", r["note"])

    def test_approved_command_runs(self):
        r = oracles.check("command", "echo hi", approved=True)
        self.assertEqual(r["satisfied"], 1)

    def test_unapproved_tests_kind_also_refuses(self):
        r = oracles.check("tests", "echo hi", approved=False)
        self.assertEqual(r["satisfied"], 0)

    def test_manual_and_file_exists_need_no_approval(self):
        self.assertEqual(oracles.check("manual", "", approved=False)["satisfied"], 0)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "out.txt"
            f.write_text("hello, this file is deliberately over the 16-byte floor")
            r = oracles.check("file_exists", "out.txt", workdir=d, approved=False)
            self.assertEqual(r["satisfied"], 1)


class FileExistsOracleTest(unittest.TestCase):
    def test_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as d:
            r = oracles.check("file_exists", "nope.txt", workdir=d)
            self.assertEqual(r["satisfied"], -1)

    def test_empty_file_fails(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "empty.txt").write_text("")
            r = oracles.check("file_exists", "empty.txt", workdir=d)
            self.assertEqual(r["satisfied"], -1)


if __name__ == "__main__":
    unittest.main()
