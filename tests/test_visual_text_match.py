import unittest

from visual_text_match import compare_masks, render_candidate_mask


class VisualTextMatchTests(unittest.TestCase):
    def test_same_text_scores_higher_than_different_text(self):
        observed = render_candidate_mask("磯野カツオ")
        same = compare_masks(observed, render_candidate_mask("磯野カツオ"))
        different = compare_masks(observed, render_candidate_mask("山田太郎"))
        self.assertGreater(same, 0.95)
        self.assertGreater(same, different + 0.15)

    def test_grade_labels_are_distinguishable(self):
        observed = render_candidate_mask("小5")
        correct = compare_masks(observed, render_candidate_mask("小5"))
        wrong = compare_masks(observed, render_candidate_mask("中2"))
        self.assertGreater(correct, wrong + 0.10)


if __name__ == "__main__":
    unittest.main()
