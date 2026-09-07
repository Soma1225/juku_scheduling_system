import sqlite3
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from handwritten_counts import (
    analyze_count_cell,
    confirm_subject_counts,
    recognize_count_cell,
    store_subject_count_candidates,
)
from image_import_migrations import ensure_image_import_schema


class HandwrittenCountTests(unittest.TestCase):
    def test_clean_blank_is_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blank.png"
            cv2.imwrite(str(path), np.full((70, 180), 255, dtype=np.uint8))
            result = analyze_count_cell(path)
            self.assertTrue(result.is_blank)
            self.assertEqual(result.ink_pixels, 0)

    def test_small_scan_specks_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "specks.png"
            image = np.full((70, 180), 255, dtype=np.uint8)
            image[10, 10] = 0
            image[40:42, 80:82] = 0
            cv2.imwrite(str(path), image)
            self.assertTrue(analyze_count_cell(path).is_blank)

    def test_scattered_scan_dust_from_real_blank_cell_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dust.png"
            image = np.full((70, 180), 255, dtype=np.uint8)
            for x, y in ((20, 15), (60, 35), (110, 20), (145, 48)):
                image[y:y + 3, x:x + 4] = 0
            cv2.imwrite(str(path), image)
            result = recognize_count_cell(path)
            self.assertTrue(result.is_blank)
            self.assertEqual(result.recognized_count, 0)

    def test_handwritten_digit_is_not_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "digit.png"
            image = np.full((70, 180), 255, dtype=np.uint8)
            cv2.putText(image, "5", (70, 55), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 1.6, 0, 2)
            cv2.imwrite(str(path), image)
            result = analyze_count_cell(path)
            self.assertFalse(result.is_blank)
            self.assertGreater(result.ink_pixels, 40)

    def test_clear_digit_is_recognized_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "digit.png"
            image = np.full((70, 180), 255, dtype=np.uint8)
            cv2.putText(image, "5", (70, 58), cv2.FONT_HERSHEY_SIMPLEX, 1.8, 0, 3)
            cv2.imwrite(str(path), image)
            result = recognize_count_cell(path)
            self.assertEqual(result.recognized_count, 5)
            self.assertIsNotNone(result.confidence)

    def test_clear_two_digit_count_is_recognized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "two_digits.png"
            image = np.full((70, 180), 255, dtype=np.uint8)
            cv2.putText(image, "24", (55, 58), cv2.FONT_HERSHEY_SIMPLEX, 1.8, 0, 3)
            cv2.imwrite(str(path), image)
            result = recognize_count_cell(path)
            self.assertEqual(result.recognized_count, 24)
            self.assertIsNotNone(result.confidence_margin)


class CountConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """
            CREATE TABLE CAMPS(camp_id INTEGER PRIMARY KEY);
            CREATE TABLE INSTRUCTORS(instructor_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,status TEXT);
            CREATE TABLE STUDENTS(student_id INTEGER PRIMARY KEY);
            CREATE TABLE SUBJECTS(subject_id INTEGER PRIMARY KEY);
            CREATE TABLE PERIODS(period_number INTEGER PRIMARY KEY);
            CREATE TABLE TIME_SLOTS(slot_id INTEGER PRIMARY KEY);
            CREATE TABLE CAMP_COURSE_ENROLLMENTS(enrollment_id INTEGER PRIMARY KEY);
            CREATE TABLE CAMP_STUDENT_AVAILABILITY(availability_id INTEGER PRIMARY KEY);
            """
        )
        self.conn.execute("INSERT INTO CAMPS VALUES(1)")
        self.conn.execute("INSERT INTO INSTRUCTORS VALUES(1,'担当','講師','在籍')")
        ensure_image_import_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_BATCHES
                (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
                 source_pdf_path,source_pdf_hash,page_count,status,created_at)
            VALUES(1,2026,'夏期','hq-standard',1,'x.pdf',?,1,'REVIEW_PENDING','now')
            """,
            ("a" * 64,),
        )
        self.conn.execute(
            "INSERT INTO IMAGE_IMPORT_PAGES(batch_id,page_number,page_image_path,page_image_hash,created_at) VALUES(1,1,'p.png',?,'now')",
            ("b" * 64,),
        )
        self.conn.execute(
            "INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS(page_id,subject_row_label,count_status,created_at) VALUES(1,'数学','AMBIGUOUS','now')"
        )
        self.conn.execute(
            "INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS(page_id,item_type,related_subject_enrollment_id,crop_image_path,created_at) VALUES(1,'COUNT_AMBIGUOUS',1,'count.png','now')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_confirm_count_updates_candidate_review_and_audit(self):
        confirmed = confirm_subject_counts(
            self.conn, page_id=1, confirmed_counts={1: 5}, operator_instructor_id=1
        )
        self.assertEqual(confirmed, 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved_count,count_status FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS"
            ).fetchone(),
            (5, "MANUALLY_CONFIRMED"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolution,corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS"
            ).fetchone(),
            ("CORRECTED", "5"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT action_type,operator_name FROM IMAGE_IMPORT_AUDIT_LOG"
            ).fetchone(),
            ("CORRECT", "担当講師"),
        )

    def test_out_of_range_count_is_rejected(self):
        with self.assertRaises(ValueError):
            confirm_subject_counts(
                self.conn, page_id=1, confirmed_counts={1: 100}, operator_instructor_id=1
            )

    def test_clear_count_is_stored_as_auto_recognized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "count.png"
            image = np.full((70, 180), 255, dtype=np.uint8)
            cv2.putText(image, "24", (55, 58), cv2.FONT_HERSHEY_SIMPLEX, 1.8, 0, 3)
            cv2.imwrite(str(path), image)
            reviews = store_subject_count_candidates(
                self.conn, page_id=1, count_regions={"英語": path},
            )
            self.assertEqual(reviews, 0)
            self.assertEqual(
                self.conn.execute(
                    "SELECT recognized_count,resolved_count,count_status "
                    "FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS WHERE subject_row_label='英語'"
                ).fetchone(),
                (24, 24, "AUTO_RECOGNIZED"),
            )


if __name__ == "__main__":
    unittest.main()
