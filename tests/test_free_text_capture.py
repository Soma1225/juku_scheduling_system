import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from free_text_capture import MIN_MEANINGFUL_INK_PIXELS, meaningful_ink_pixels


class FreeTextCaptureTests(unittest.TestCase):
    def test_blank_region_has_no_meaningful_ink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blank.png"
            cv2.imwrite(str(path), np.full((100, 500), 255, dtype=np.uint8))
            self.assertLess(meaningful_ink_pixels(path), MIN_MEANINGFUL_INK_PIXELS)

    def test_handwriting_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.png"
            image = np.full((100, 500), 255, dtype=np.uint8)
            cv2.putText(image, "request", (20, 70), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 1.5, 0, 2)
            cv2.imwrite(str(path), image)
            self.assertGreaterEqual(meaningful_ink_pixels(path), MIN_MEANINGFUL_INK_PIXELS)


if __name__ == "__main__":
    unittest.main()
