"""providers.py: API-key storage — encryption, and the no-downgrade guarantee.

This covers a real bug caught during implementation: an earlier version of
save_config() round-tripped through config() (which decrypts for internal
use), so saving any ONE key re-persisted every OTHER already-encrypted key
back to disk in plaintext. That must never happen again.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from promptmeter import db, providers
from tests.helpers import DBTestCase


class ProviderKeyStorageTest(DBTestCase):
    def test_saved_key_round_trips_through_config(self):
        providers.save_config({"active": "anthropic", "key": "sk-ant-AAAA",
                               "key_for": "anthropic"})
        self.assertEqual(providers.config()["keys"]["anthropic"], "sk-ant-AAAA")

    def test_public_config_never_exposes_the_full_key(self):
        providers.save_config({"key": "sk-ant-AAAA1111BBBB", "key_for": "anthropic"})
        masked = providers.public_config()["keys"]["anthropic"]
        self.assertNotIn("AAAA1111BBBB", masked)

    def test_saving_one_key_does_not_touch_another_providers_stored_shape(self):
        providers.save_config({"key": "sk-ant-AAAA", "key_for": "anthropic"})
        raw_before = db.get_setting("provider_config")["keys"]["anthropic"]

        providers.save_config({"key": "sk-openai-BBBB", "key_for": "openai"})
        raw_after = db.get_setting("provider_config")["keys"]["anthropic"]

        self.assertEqual(raw_before, raw_after)
        self.assertEqual(providers.config()["keys"]["anthropic"], "sk-ant-AAAA")
        self.assertEqual(providers.config()["keys"]["openai"], "sk-openai-BBBB")

    def test_removing_a_key_with_empty_string_clears_it(self):
        providers.save_config({"key": "sk-ant-AAAA", "key_for": "anthropic"})
        providers.save_config({"key": "", "key_for": "anthropic"})
        self.assertNotIn("anthropic", providers.config()["keys"])

    def test_re_saving_the_masked_value_does_not_overwrite_the_real_key(self):
        providers.save_config({"key": "sk-ant-AAAA1111BBBB", "key_for": "anthropic"})
        masked = providers.public_config()["keys"]["anthropic"]
        providers.save_config({"key": masked, "key_for": "anthropic"})  # UI echoes it back
        self.assertEqual(providers.config()["keys"]["anthropic"], "sk-ant-AAAA1111BBBB")

    def test_key_storage_reports_encrypted_or_plaintext_honestly(self):
        from promptmeter import dpapi
        providers.save_config({"key": "sk-ant-AAAA", "key_for": "anthropic"})
        storage = providers.public_config()["key_storage"]["anthropic"]
        self.assertEqual(storage, "encrypted" if dpapi.AVAILABLE else "plaintext")


class LoadDotenvTest(unittest.TestCase):
    """load_dotenv() itself — the file-parsing half, independent of the DB."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("PM_TEST_A", "PM_TEST_B", "PM_TEST_ALREADY_SET")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _write(self, text: str) -> Path:
        d = tempfile.mkdtemp(prefix="promptmeter-dotenv-")
        p = Path(d) / ".env"
        p.write_text(text, encoding="utf-8")
        return p

    def test_parses_key_value_lines(self):
        p = self._write("PM_TEST_A=hello\nPM_TEST_B=world\n")
        providers.load_dotenv(p)
        self.assertEqual(os.environ.get("PM_TEST_A"), "hello")
        self.assertEqual(os.environ.get("PM_TEST_B"), "world")

    def test_skips_comments_and_blank_lines(self):
        p = self._write("# a comment\n\nPM_TEST_A=value\n   \n# PM_TEST_B=nope\n")
        providers.load_dotenv(p)
        self.assertEqual(os.environ.get("PM_TEST_A"), "value")
        self.assertIsNone(os.environ.get("PM_TEST_B"))

    def test_strips_quotes(self):
        p = self._write('PM_TEST_A="quoted"\nPM_TEST_B=\'single\'\n')
        providers.load_dotenv(p)
        self.assertEqual(os.environ.get("PM_TEST_A"), "quoted")
        self.assertEqual(os.environ.get("PM_TEST_B"), "single")

    def test_never_overwrites_a_real_env_var(self):
        os.environ["PM_TEST_ALREADY_SET"] = "real-value"
        p = self._write("PM_TEST_ALREADY_SET=from-file\n")
        providers.load_dotenv(p)
        self.assertEqual(os.environ.get("PM_TEST_ALREADY_SET"), "real-value")

    def test_missing_file_does_not_raise(self):
        providers.load_dotenv(Path(tempfile.mkdtemp()) / "does-not-exist.env")  # no exception


class EnvFallbackTest(DBTestCase):
    """config()/public_config() falling back to the environment when nothing
    is saved via the Setup page — and a saved key always winning over it."""

    def setUp(self):
        super().setUp()
        self._saved = os.environ.get("ANTHROPIC_API_KEY")
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._saved
        super().tearDown()

    def test_falls_back_to_environment_with_nothing_saved(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
        self.assertEqual(providers.config()["keys"]["anthropic"], "sk-ant-from-env")

    def test_saved_key_wins_over_the_environment(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
        providers.save_config({"key": "sk-ant-from-ui", "key_for": "anthropic"})
        self.assertEqual(providers.config()["keys"]["anthropic"], "sk-ant-from-ui")

    def test_public_config_labels_an_env_sourced_key_honestly(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
        storage = providers.public_config()["key_storage"]["anthropic"]
        self.assertEqual(storage, "environment")

    def test_removing_a_saved_key_reveals_the_environment_default(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-env"
        providers.save_config({"key": "sk-ant-from-ui", "key_for": "anthropic"})
        providers.save_config({"key": "", "key_for": "anthropic"})
        self.assertEqual(providers.config()["keys"]["anthropic"], "sk-ant-from-env")

    def test_no_env_var_and_nothing_saved_means_no_key(self):
        self.assertNotIn("anthropic", providers.config()["keys"])


if __name__ == "__main__":
    unittest.main()
