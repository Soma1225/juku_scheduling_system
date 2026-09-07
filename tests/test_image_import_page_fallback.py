import sqlite3
import unittest

from image_import_commit import build_import_plan
from image_import_migrations import ensure_image_import_schema
from image_import_page_fallback import restore_skipped_page, skip_problem_page


class ImageImportPageFallbackTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """
            CREATE TABLE CAMPS(camp_id INTEGER PRIMARY KEY);
            CREATE TABLE INSTRUCTORS(
              instructor_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,status TEXT
            );
            CREATE TABLE STUDENTS(student_id INTEGER PRIMARY KEY);
            CREATE TABLE SUBJECTS(subject_id INTEGER PRIMARY KEY);
            CREATE TABLE PERIODS(period_number INTEGER PRIMARY KEY);
            CREATE TABLE TIME_SLOTS(slot_id INTEGER PRIMARY KEY);
            CREATE TABLE CAMP_COURSE_ENROLLMENTS(
              enrollment_id INTEGER PRIMARY KEY,camp_id INTEGER,student_id INTEGER,
              subject_id INTEGER,contracted_count INTEGER,format TEXT,
              assigned_instructor_id INTEGER,enrollment_end_date TEXT
            );
            CREATE TABLE CAMP_STUDENT_AVAILABILITY(
              availability_id INTEGER PRIMARY KEY,student_id INTEGER,slot_id INTEGER,is_available INTEGER
            );
            INSERT INTO CAMPS VALUES(1);
            INSERT INTO INSTRUCTORS VALUES(9,'確認','太郎','在籍');
            INSERT INTO STUDENTS VALUES(1);
            """
        )
        ensure_image_import_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_BATCHES
              (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
               source_pdf_path,source_pdf_hash,page_count,status,created_at)
            VALUES(1,2026,'夏期','hq-standard',1,'x.pdf',?,2,'REVIEW_PENDING','now')
            """,
            ("a" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGES
              (batch_id,page_number,page_image_path,page_image_hash,layout_quality,processing_status,created_at)
            VALUES(1,1,'ok.png',?,'OK','RECOGNIZED','now')
            """,
            ("b" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
              (page_id,candidate_student_id,candidate_rank,match_status,is_selected,created_at)
            VALUES(1,1,1,'AUTO_MATCHED',1,'now')
            """
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGES
              (batch_id,page_number,page_image_path,page_image_hash,layout_quality,processing_status,created_at)
            VALUES(1,2,'bad.png',?,'STRUCTURE_FAILED','RECOGNIZED','now')
            """,
            ("c" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
              (page_id,candidate_student_id,candidate_rank,match_status,is_selected,created_at)
            VALUES(2,1,1,'AMBIGUOUS',0,'now')
            """
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
              (page_id,item_type,related_page_student_id,created_at)
            VALUES(2,'STUDENT_MATCH',2,'now')
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_bad_page_can_be_skipped_and_restored_without_deleting_evidence(self):
        self.assertFalse(build_import_plan(self.conn, batch_id=1).can_import)
        skip_problem_page(
            self.conn, page_id=2, fallback_mode="RESCAN", operator_instructor_id=9
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT processing_status FROM IMAGE_IMPORT_PAGES WHERE page_id=2"
            ).fetchone()[0],
            "SKIPPED",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolution,is_deleted FROM IMAGE_IMPORT_REVIEW_ITEMS"
            ).fetchone(),
            ("PENDING", 0),
        )
        self.assertTrue(build_import_plan(self.conn, batch_id=1).can_import)
        self.assertEqual(
            self.conn.execute(
                "SELECT action_type,operator_name FROM IMAGE_IMPORT_AUDIT_LOG"
            ).fetchone(),
            ("REJECT", "確認太郎"),
        )

        restore_skipped_page(self.conn, page_id=2, operator_instructor_id=9)
        self.assertEqual(
            self.conn.execute(
                "SELECT processing_status FROM IMAGE_IMPORT_PAGES WHERE page_id=2"
            ).fetchone()[0],
            "RECOGNIZED",
        )
        self.assertEqual(
            self.conn.execute("SELECT is_deleted FROM IMAGE_IMPORT_REVIEW_ITEMS").fetchone()[0],
            0,
        )
        self.assertFalse(build_import_plan(self.conn, batch_id=1).can_import)
        self.assertEqual(
            self.conn.execute(
                "SELECT action_type FROM IMAGE_IMPORT_AUDIT_LOG ORDER BY audit_log_id DESC LIMIT 1"
            ).fetchone()[0],
            "RESTORE",
        )

    def test_ok_page_cannot_be_skipped_by_quality_fallback(self):
        with self.assertRaises(ValueError):
            skip_problem_page(
                self.conn, page_id=1, fallback_mode="MANUAL", operator_instructor_id=9
            )


if __name__ == "__main__":
    unittest.main()
