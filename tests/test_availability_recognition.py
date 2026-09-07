import sqlite3
import unittest

import cv2
import numpy as np

from availability_recognition import (
    analyze_availability_cell,
    confirm_availability_cells,
    expected_months,
)
from image_import_migrations import ensure_image_import_schema


class AvailabilityCellTests(unittest.TestCase):
    def test_blank_cell_is_available(self):
        cell = np.full((44, 54), 255, dtype=np.uint8)
        result = analyze_availability_cell(cell)
        self.assertEqual(result.state, "AVAILABLE")

    def test_x_mark_is_unavailable(self):
        cell = np.full((44, 54), 255, dtype=np.uint8)
        cv2.line(cell, (2, 2), (51, 41), 0, 2)
        cv2.line(cell, (51, 2), (2, 41), 0, 2)
        result = analyze_availability_cell(cell)
        self.assertEqual(result.state, "UNAVAILABLE")
        self.assertGreaterEqual(result.line_crossing_score, 0.60)

    def test_small_dirt_is_not_unavailable(self):
        cell = np.full((44, 54), 255, dtype=np.uint8)
        cell[10:12, 10:12] = 0
        self.assertEqual(analyze_availability_cell(cell).state, "AVAILABLE")


class ExpectedMonthsTests(unittest.TestCase):
    def test_winter_crosses_calendar_year(self):
        self.assertEqual(
            expected_months("冬期", 2026, 3),
            [(2026, 12), (2027, 1), (2027, 2)],
        )

    def test_spring_allows_march_only_or_march_and_april(self):
        self.assertEqual(expected_months("春期", 2026, 1), [(2026, 3)])
        self.assertEqual(expected_months("春期", 2026, 2), [(2026, 3), (2026, 4)])

    def test_wrong_summer_grid_count_is_rejected(self):
        with self.assertRaises(ValueError):
            expected_months("夏期", 2026, 1)


class AvailabilityConfirmationTests(unittest.TestCase):
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
        self.conn.execute("INSERT INTO PERIODS VALUES(1)")
        self.conn.execute("INSERT INTO TIME_SLOTS VALUES(1)")
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
            """
            INSERT INTO IMAGE_IMPORT_AVAILABILITY
                (page_id,session_date,period_number,matched_slot_id,availability_status,created_at)
            VALUES(1,'2026-07-01',1,1,'AMBIGUOUS','now')
            """
        )
        self.conn.execute(
            "INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS(page_id,item_type,related_availability_id,crop_image_path,created_at) VALUES(1,'AVAILABILITY_AMBIGUOUS',1,'cell.png','now')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_confirm_availability_updates_review_and_audit(self):
        confirmed = confirm_availability_cells(
            self.conn, page_id=1, confirmed_values={1: 0}, operator_instructor_id=1
        )
        self.assertEqual(confirmed, 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved_is_available,availability_status FROM IMAGE_IMPORT_AVAILABILITY"
            ).fetchone(),
            (0, "MANUALLY_CONFIRMED"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolution,corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS"
            ).fetchone(),
            ("CORRECTED", "対応不可"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT action_type,operator_name FROM IMAGE_IMPORT_AUDIT_LOG"
            ).fetchone(),
            ("CORRECT", "担当講師"),
        )


if __name__ == "__main__":
    unittest.main()
