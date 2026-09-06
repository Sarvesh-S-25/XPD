"""providers.py: API-key storage — encryption, and the no-downgrade guarantee.

This covers a real bug caught during implementation: an earlier version of
save_config() round-tripped through config() (which decrypts for internal
use), so saving any ONE key re-persisted every OTHER already-encrypted key
back to disk in plaintext. That must never happen again.
"""
from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
