import json
import sqlite3
import unittest

from availability_recognition import resolve_availability_exception
from image_import_migrations import ensure_image_import_schema


PARENT_SCHEMA = """
CREATE TABLE CAMPS (camp_id INTEGER PRIMARY KEY);
CREATE TABLE INSTRUCTORS (
    instructor_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,status TEXT
);
CREATE TABLE STUDENTS (student_id INTEGER PRIMARY KEY);
CREATE TABLE SUBJECTS (subject_id INTEGER PRIMARY KEY);
CREATE TABLE PERIODS (period_number INTEGER PRIMARY KEY);
CREATE TABLE TIME_SLOTS (
    slot_id INTEGER PRIMARY KEY,session_date TEXT,period_number INTEGER
);
CREATE TABLE CAMP_COURSE_ENROLLMENTS (enrollment_id INTEGER PRIMARY KEY);
CREATE TABLE CAMP_STUDENT_AVAILABILITY (availability_id INTEGER PRIMARY KEY);
"""


class AvailabilityExceptionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(PARENT_SCHEMA)
        self.conn.execute("INSERT INTO CAMPS VALUES(1)")
        self.conn.execute("INSERT INTO INSTRUCTORS VALUES(1,'確認','担当','在籍')")
        self.conn.execute("INSERT INTO PERIODS VALUES(1)")
        ensure_image_import_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_BATCHES
              (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
               source_pdf_path,source_pdf_hash,page_count,status,school_id,school_name,created_at)
            VALUES(1,2026,'夏期','hq-standard',1,'x.pdf',?,1,'REVIEW_PENDING','S1','本校','now')
            """,
            ("a" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGES
              (batch_id,page_number,page_image_path,page_image_hash,layout_quality,
               processing_status,created_at)
            VALUES(1,1,'p.png',?,'OK','RECOGNIZED','now')
            """,
            ("b" * 64,),
        )
        self.ids = {}
        for index, (session_date, status) in enumerate(
            (("2026-07-01", "SLOT_NOT_FOUND"), ("2026-06-30", "OUT_OF_CAMP_RANGE")),
            start=1,
        ):
            cursor = self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_AVAILABILITY
                  (page_id,session_date,period_number,availability_status,created_at)
                VALUES(1,?,1,?,'now')
                """,
                (session_date, status),
            )
            availability_id = cursor.lastrowid
            self.ids[status] = availability_id
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                  (page_id,item_type,related_availability_id,crop_image_path,created_at)
                VALUES(1,?,?,?,'now')
                """,
                (status, availability_id, f"cell-{index}.png"),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_missing_slot_must_exist_before_staff_can_resolve_it(self):
        with self.assertRaisesRegex(ValueError, "まだ登録されていません"):
            resolve_availability_exception(
                self.conn,
                page_id=1,
                import_availability_id=self.ids["SLOT_NOT_FOUND"],
                resolution="AVAILABLE",
                operator_instructor_id=1,
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT availability_status FROM IMAGE_IMPORT_AVAILABILITY WHERE import_availability_id=?",
                (self.ids["SLOT_NOT_FOUND"],),
            ).fetchone()[0],
            "SLOT_NOT_FOUND",
        )

    def test_missing_slot_is_relinked_and_confirmed_with_audit(self):
        self.conn.execute("INSERT INTO TIME_SLOTS VALUES(10,'2026-07-01',1)")
        self.conn.commit()
        resolve_availability_exception(
            self.conn,
            page_id=1,
            import_availability_id=self.ids["SLOT_NOT_FOUND"],
            resolution="UNAVAILABLE",
            operator_instructor_id=1,
        )
        row = self.conn.execute(
            """
            SELECT matched_slot_id,resolved_is_available,availability_status
            FROM IMAGE_IMPORT_AVAILABILITY WHERE import_availability_id=?
            """,
            (self.ids["SLOT_NOT_FOUND"],),
        ).fetchone()
        self.assertEqual(row, (10, 0, "MANUALLY_CONFIRMED"))
        review = self.conn.execute(
            """
            SELECT resolution,corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS
            WHERE related_availability_id=?
            """,
            (self.ids["SLOT_NOT_FOUND"],),
        ).fetchone()
        self.assertEqual(review, ("CORRECTED", "対応不可"))
        audit = json.loads(self.conn.execute(
            "SELECT after_value_json FROM IMAGE_IMPORT_AUDIT_LOG"
        ).fetchone()[0])
        self.assertEqual(audit["resolution_reason"], "時間枠登録後に再照合")

    def test_out_of_range_is_soft_deleted_and_review_is_rejected(self):
        resolve_availability_exception(
            self.conn,
            page_id=1,
            import_availability_id=self.ids["OUT_OF_CAMP_RANGE"],
            resolution="EXCLUDE",
            operator_instructor_id=1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT is_deleted FROM IMAGE_IMPORT_AVAILABILITY WHERE import_availability_id=?",
                (self.ids["OUT_OF_CAMP_RANGE"],),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolution FROM IMAGE_IMPORT_REVIEW_ITEMS WHERE related_availability_id=?",
                (self.ids["OUT_OF_CAMP_RANGE"],),
            ).fetchone()[0],
            "REJECTED",
        )
        audit = self.conn.execute(
            "SELECT action_type,after_value_json FROM IMAGE_IMPORT_AUDIT_LOG"
        ).fetchone()
        self.assertEqual(audit[0], "REJECT")
        self.assertEqual(json.loads(audit[1])["is_deleted"], 1)


if __name__ == "__main__":
    unittest.main()
