"""Shared test setup — not a test module itself (no test_ prefix, so
`unittest discover` skips it).

Every test gets its own throwaway `~/.promptmeter`-equivalent directory via
`PROMPTMETER_HOME`, so tests never touch the real user's database and never
see each other's data. `db.connect()` caches one sqlite3 connection per
thread in `db._LOCAL`; since unittest runs everything in one thread,
`fresh_db()`/`cleanup()` clear that cache explicitly so each test actually
gets a new connection pointed at its new temp directory rather than reusing
the previous test's.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from promptmeter import db


class DBTestCase(unittest.TestCase):
    """Base class for any test that touches the database. Subclass it and
    call super().setUp()/tearDown() if you need your own fixture too."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="promptmeter-test-")
        os.environ["PROMPTMETER_HOME"] = self._tmp
        db._LOCAL.conn = None
        db.init()

    def tearDown(self) -> None:
        db._LOCAL.conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)
