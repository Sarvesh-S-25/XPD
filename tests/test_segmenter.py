"""segmenter.py: prompt -> dependency graph. Pure functions, no DB."""
from __future__ import annotations

import unittest

from promptmeter import segmenter


class SplitItemsTest(unittest.TestCase):
    def test_bulleted_prompt_splits_on_bullets(self):
        prompt = "- Write the schema\n- Build the API\n- Write the frontend\n"
        items = segmenter.split_items(prompt)
        self.assertEqual(len(items), 3)

    def test_single_sentence_stays_one_item(self):
        items = segmenter.split_items("Fix the typo in the README.")
        self.assertEqual(len(items), 1)

    def test_never_exceeds_max_items(self):
        prompt = "\n".join(f"- do thing number {i}" for i in range(40))
        items = segmenter.split_items(prompt)
        self.assertLessEqual(len(items), segmenter.MAX_ITEMS)


class DependencyGraphTest(unittest.TestCase):
    def test_ordering_cue_creates_a_dependency_on_the_previous_item(self):
        items = ["Build the API.", "Then build the frontend that calls it."]
        arts = [segmenter.artifacts_of(t) for t in items]
        deps = segmenter.infer_edges(items, arts)
        self.assertIn(0, deps[1])
        self.assertEqual(deps[0], [])

    def test_independent_items_have_no_edges(self):
        items = ["Write the README.", "Add a LICENSE file."]
        arts = [segmenter.artifacts_of(t) for t in items]
        deps = segmenter.infer_edges(items, arts)
        self.assertEqual(deps, [[], []])


class StageLayeringTest(unittest.TestCase):
    def test_linear_chain_gets_increasing_stages(self):
        deps = [[], [0], [1], [2]]
        self.assertEqual(segmenter.stages(deps), [0, 1, 2, 3])

    def test_independent_items_share_a_stage(self):
        deps = [[], [], []]
        self.assertEqual(segmenter.stages(deps), [0, 0, 0])

    def test_a_forward_reference_is_ignored_not_treated_as_a_cycle(self):
        # deps[0] = [1] is a forward reference (1 is not < 0) — stages() only
        # ever looks backward, so it is silently dropped rather than crashing
        # or looping. deps[1] = [0] is a genuine, valid backward reference,
        # so item 1 correctly lands one stage after item 0.
        deps = [[1], [0]]
        result = segmenter.stages(deps)
        self.assertEqual(result, [0, 1])


class FullSegmentTest(unittest.TestCase):
    def test_segment_returns_one_dict_per_item_with_required_keys(self):
        out = segmenter.segment("- Build the schema\n- Build the API\n")
        self.assertEqual(len(out), 2)
        for item in out:
            for key in ("idx", "title", "prompt", "artifacts", "depends_on", "stage"):
                self.assertIn(key, item)


if __name__ == "__main__":
    unittest.main()
