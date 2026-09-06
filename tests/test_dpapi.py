"""dpapi.py: Windows DPAPI round-trip for at-rest API-key encryption (PLS-DO S2).

Skips the round-trip assertions on non-Windows, since DPAPI genuinely does not
exist there — that is the documented, honest limit of this fix, not a gap in
the test.
"""
from __future__ import annotations

import unittest

from promptmeter import dpapi


class DpapiTest(unittest.TestCase):
    @unittest.skipUnless(dpapi.AVAILABLE, "DPAPI is Windows-only")
    def test_round_trip_recovers_the_original_string(self):
        blob = dpapi.protect("sk-ant-super-secret-12345")
        self.assertIsNotNone(blob)
        self.assertEqual(dpapi.unprotect(blob), "sk-ant-super-secret-12345")

    @unittest.skipUnless(dpapi.AVAILABLE, "DPAPI is Windows-only")
    def test_two_encryptions_of_the_same_value_are_not_identical_bytes(self):
        # DPAPI includes randomness/entropy in the output — a static-looking
        # blob would suggest something is silently falling back to plaintext.
        a = dpapi.protect("same-secret")
        b = dpapi.protect("same-secret")
        self.assertNotEqual(a, b)

    def test_garbage_input_never_raises_and_returns_none(self):
        self.assertIsNone(dpapi.unprotect("not-a-real-base64-blob"))
        self.assertIsNone(dpapi.unprotect(""))

    def test_empty_plaintext_is_not_protected(self):
        self.assertIsNone(dpapi.protect(""))

    def test_unavailable_platform_returns_none_not_an_exception(self):
        if dpapi.AVAILABLE:
            self.skipTest("this machine has DPAPI; nothing to simulate")
        self.assertIsNone(dpapi.protect("x"))
        self.assertIsNone(dpapi.unprotect("x"))


if __name__ == "__main__":
    unittest.main()
