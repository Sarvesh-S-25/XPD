"""estimator.py: classify_detail()'s ambiguous flag.

A prompt that matches no class signal at all used to fall back silently to
'multi_file_feature' (12-40 turns) with the same confident framing as a real
reading — two equally underspecified prompts (a bare word vs. one that
happens to brush a keyword pattern) could land on wildly different verdicts
with no way to tell the difference was luck of the wording, not a real
signal. classify_detail() surfaces that so the Plan screen can say so.
"""
from __future__ import annotations

import unittest

from promptmeter import estimator


class ClassifyDetailTest(unittest.TestCase):
    def test_bare_word_is_ambiguous(self):
        d = estimator.classify_detail("swiggy")
        self.assertTrue(d["ambiguous"])
        self.assertEqual(d["signal_score"], 0.0)

    def test_clear_build_request_is_not_ambiguous(self):
        d = estimator.classify_detail("build an app like swiggy")
        self.assertFalse(d["ambiguous"])
        self.assertEqual(d["task_class"], "build_app")

    def test_clear_bugfix_request_is_not_ambiguous(self):
        d = estimator.classify_detail("fix the typo in auth.py")
        self.assertFalse(d["ambiguous"])

    def test_classify_still_returns_a_plain_string(self):
        # classify() is the pre-existing public surface (planner.py calls
        # it directly) — classify_detail() must not change its contract.
        self.assertEqual(estimator.classify("swiggy"),
                         estimator.classify_detail("swiggy")["task_class"])

    def test_estimate_surfaces_ambiguous_flag(self):
        est = estimator.estimate("swiggy", model="claude-sonnet-5")
        self.assertTrue(est["ambiguous"])
        est2 = estimator.estimate("build an app like swiggy", model="claude-sonnet-5")
        self.assertFalse(est2["ambiguous"])

    def test_explicit_task_class_skips_ambiguity_check(self):
        # A caller that already knows the class (e.g. a re-estimate inside a
        # drafted plan) never gets flagged — there was no naive guess to warn about.
        est = estimator.estimate("swiggy", model="claude-sonnet-5", task_class="qa_explain")
        self.assertFalse(est["ambiguous"])


if __name__ == "__main__":
    unittest.main()
