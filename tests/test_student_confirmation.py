import sqlite3
import unittest

from image_import_migrations import ensure_image_import_schema
from student_image_matching import confirm_student_by_master, confirm_student_match


SCHEMA = """
CREATE TABLE CAMPS (camp_id INTEGER PRIMARY KEY);
CREATE TABLE INSTRUCTORS (
    instructor_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,status TEXT
);
CREATE TABLE STUDENTS (
    student_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,enrollment_status TEXT
);
CREATE TABLE SUBJECTS (subject_id INTEGER PRIMARY KEY);
CREATE TABLE PERIODS (period_number INTEGER PRIMARY KEY);
CREATE TABLE TIME_SLOTS (slot_id INTEGER PRIMARY KEY);
CREATE TABLE CAMP_COURSE_ENROLLMENTS (enrollment_id INTEGER PRIMARY KEY);
CREATE TABLE CAMP_STUDENT_AVAILABILITY (availability_id INTEGER PRIMARY KEY);
"""


class StudentConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO CAMPS VALUES(1)")
        self.conn.execute("INSERT INTO INSTRUCTORS VALUES(1,'担当','講師','在籍')")
        self.conn.executemany(
            "INSERT INTO STUDENTS VALUES(?,?,?,?)",
            [(1, "第一", "候補", "在籍"), (2, "第二", "候補", "在籍"),
             (3, "候補外", "生徒", "在籍")],
        )
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
            """
            INSERT INTO IMAGE_IMPORT_PAGES
                (batch_id,page_number,page_image_path,page_image_hash,created_at)
            VALUES(1,1,'p.png',?,'now')
            """,
            ("b" * 64,),
        )
        for student_id, rank in ((1, 1), (2, 2)):
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
                    (page_id,candidate_student_id,candidate_rank,match_confidence,
                     match_status,created_at)
                VALUES(1,?,?,0.7,'AMBIGUOUS','now')
                """,
                (student_id, rank),
            )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                (page_id,item_type,related_page_student_id,created_at)
            VALUES(1,'STUDENT_MATCH',1,'now')
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_confirming_second_candidate_resolves_as_correction_and_audits_operator(self):
        student_id = confirm_student_match(
            self.conn,
            page_id=1,
            page_student_id=2,
            operator_instructor_id=1,
        )

        self.assertEqual(student_id, 2)
        selected = self.conn.execute(
            """
            SELECT candidate_student_id,match_status,is_selected
            FROM IMAGE_IMPORT_PAGE_STUDENTS WHERE is_selected=1
            """
        ).fetchone()
        self.assertEqual(selected, (2, "MANUALLY_CONFIRMED", 1))
        review = self.conn.execute(
            "SELECT resolution,corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS"
        ).fetchone()
        self.assertEqual(review, ("CORRECTED", "第二候補"))
        audit = self.conn.execute(
            "SELECT action_type,actor_type,operator_instructor_id,operator_name FROM IMAGE_IMPORT_AUDIT_LOG"
        ).fetchone()
        self.assertEqual(audit, ("CORRECT", "INSTRUCTOR", 1, "担当講師"))
        self.assertEqual(
            self.conn.execute("SELECT status FROM IMAGE_IMPORT_BATCHES").fetchone()[0],
            "PROCESSING",
        )

    def test_non_active_operator_is_rejected_without_changes(self):
        self.conn.execute("UPDATE INSTRUCTORS SET status='退職' WHERE instructor_id=1")
        self.conn.commit()
        with self.assertRaises(ValueError):
            confirm_student_match(
                self.conn,
                page_id=1,
                page_student_id=1,
                operator_instructor_id=1,
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM IMAGE_IMPORT_PAGE_STUDENTS WHERE is_selected=1"
            ).fetchone()[0],
            0,
        )

    def test_student_outside_image_candidates_can_be_confirmed_from_master(self):
        student_id = confirm_student_by_master(
            self.conn,
            page_id=1,
            student_id=3,
            operator_instructor_id=1,
        )
        self.assertEqual(student_id, 3)
        self.assertEqual(
            self.conn.execute(
                """
                SELECT candidate_student_id,match_status,is_selected
                FROM IMAGE_IMPORT_PAGE_STUDENTS WHERE is_selected=1
                """
            ).fetchone(),
            (3, "MANUALLY_CONFIRMED", 1),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolution,corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS"
            ).fetchone(),
            ("CORRECTED", "候補外生徒"),
        )


if __name__ == "__main__":
    unittest.main()
