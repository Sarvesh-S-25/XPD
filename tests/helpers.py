"""Shared test setup — not a test module itself (no test_ prefix, so
`unittest discover` skips it).

Every test gets its own throwaway database. Locally that is a temporary
`~/.promptmeter`-equivalent directory via `PROMPTMETER_HOME`. With
`PROMPTMETER_TEST_DATABASE_URL` set to a Postgres server, the WHOLE suite runs
on Postgres instead — the backend the hosted website uses — each test inside a
fresh schema of its own, dropped afterwards. That is what proves the engine's
SQL (written for SQLite, translated in db._pg) really works on Postgres.

`db.connect()` caches one connection per thread in `db._LOCAL`; since
unittest runs everything in one thread, setUp/tearDown clear that cache
explicitly so each test gets a connection pointed at its own database.
"""
from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import unittest

from promptmeter import db

PG_URL = os.environ.get("PROMPTMETER_TEST_DATABASE_URL", "")


class DBTestCase(unittest.TestCase):
    """Base class for any test that touches the database. Subclass it and
    call super().setUp()/tearDown() if you need your own fixture too."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="promptmeter-test-")
        os.environ["PROMPTMETER_HOME"] = self._tmp
        db.close()
        db._LOCAL.conn = None
        if PG_URL:
            self._schema = "t_" + secrets.token_hex(6)
            os.environ["PROMPTMETER_DATABASE_URL"] = PG_URL
            os.environ["PROMPTMETER_PG_SCHEMA"] = self._schema
            db.ensure_schema(self._schema)
        db.init()

    def tearDown(self) -> None:
        db.close()
        if PG_URL:
            os.environ.pop("PROMPTMETER_PG_SCHEMA", None)
            db.drop_schema(self._schema)
            db.close()
            os.environ.pop("PROMPTMETER_DATABASE_URL", None)
        db._LOCAL.conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)
