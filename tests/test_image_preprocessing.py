import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from image_preprocessing import classify_quality, estimate_skew, preprocess_page


class ImagePreprocessingTests(unittest.TestCase):
    def make_form(self):
        image = np.full((1400, 1000), 255, dtype=np.uint8)
        for y in range(150, 1251, 100):
            cv2.line(image, (80, y), (920, y), 0, 3)
        for x in range(80, 921, 120):
            cv2.line(image, (x, 150), (x, 1250), 0, 3)
        cv2.putText(image, "2026 TEST FORM", (120, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
        return image

    def test_estimates_rotated_form_angle(self):
        image = self.make_form()
        matrix = cv2.getRotationMatrix2D((500, 700), -3.0, 1.0)
        rotated = cv2.warpAffine(image, matrix, (1000, 1400), borderValue=255)
        angle, line_count = estimate_skew(rotated)
        self.assertIsNotNone(angle)
        self.assertGreaterEqual(line_count, 8)
        self.assertAlmostEqual(angle, 3.0, delta=0.5)

    def test_preprocess_keeps_original_and_writes_corrected_image(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.png"
            corrected = Path(directory) / "corrected.png"
            cv2.imwrite(str(original), self.make_form())

            result = preprocess_page(original, corrected)

            self.assertTrue(original.is_file())
            self.assertTrue(corrected.is_file())
            self.assertEqual(result.layout_quality, "OK")
            corrected_gray = cv2.imread(str(corrected), cv2.IMREAD_GRAYSCALE)
            corrected_angle, _ = estimate_skew(corrected_gray)
            self.assertAlmostEqual(corrected_angle, 0.0, delta=0.5)

    def test_illegible_threshold_has_priority(self):
        self.assertEqual(
            classify_quality(
                angle=0.0,
                line_count=100,
                blur_score=10.0,
                mean_brightness=240.0,
                dark_ratio=0.05,
            ),
            "ILLEGIBLE",
        )


if __name__ == "__main__":
    unittest.main()
