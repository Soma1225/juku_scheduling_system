import sqlite3
import tempfile
import unittest
from pathlib import Path

from image_import_commit import build_import_plan, execute_import_batch
from image_import_migrations import ensure_image_import_schema


class RegularOcrImportCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        self.backup_dir = Path(self.temp.name) / "backups"
        self.conn = sqlite3.connect(self.db_path)
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
            CREATE TABLE TERMS(
              term_id INTEGER PRIMARY KEY,term_name TEXT,start_date TEXT,end_date TEXT
            );
            CREATE TABLE CAMP_COURSE_ENROLLMENTS(
              enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,camp_id INTEGER,student_id INTEGER,
              subject_id INTEGER,contracted_count INTEGER,format TEXT,
              assigned_instructor_id INTEGER,enrollment_end_date TEXT
            );
            CREATE TABLE CAMP_STUDENT_AVAILABILITY(
              availability_id INTEGER PRIMARY KEY AUTOINCREMENT,student_id INTEGER,slot_id INTEGER,
              is_available INTEGER,UNIQUE(student_id,slot_id)
            );
            CREATE TABLE STUDENT_WEEKLY_AVAILABILITY(
              availability_id INTEGER PRIMARY KEY AUTOINCREMENT,student_id INTEGER,term_id INTEGER,
              day_of_week TEXT,period_number INTEGER,is_available INTEGER,
              UNIQUE(student_id,term_id,day_of_week,period_number)
            );
            CREATE TABLE REGULAR_COURSE_ENROLLMENTS(
              enrollment_id INTEGER PRIMARY KEY,student_id INTEGER,subject_id INTEGER,
              instructor_id INTEGER,day_of_week TEXT,period_number INTEGER,
              effective_start_date TEXT,effective_end_date TEXT
            );
            INSERT INTO INSTRUCTORS VALUES(9,'確認','太郎','在籍');
            INSERT INTO STUDENTS VALUES(1);
            INSERT INTO SUBJECTS VALUES(2);
            INSERT INTO PERIODS VALUES(1);
            INSERT INTO TERMS VALUES(7,'2026年度前期','2026-03-01','2026-08-31');
            """
        )
        ensure_image_import_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_BATCHES
              (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
               source_pdf_path,source_pdf_hash,page_count,status,created_at)
            VALUES(NULL,2026,'通常','regular-standard',1,'x.pdf',?,1,'REVIEWED','now')
            """,
            ("a" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGES
              (batch_id,page_number,page_image_path,page_image_hash,layout_quality,processing_status,created_at)
            VALUES(1,1,'p.png',?,'OK','REVIEWED','now')
            """,
            ("b" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
              (page_id,candidate_student_id,candidate_rank,match_confidence,match_status,is_selected,created_at)
            VALUES(1,1,1,1.0,'AUTO_MATCHED',1,'now')
            """
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
              (page_id,subject_row_label,recognized_count,resolved_count,count_status,
               recognized_subject_id,resolved_subject_id,subject_match_status,created_at)
            VALUES(1,'数学',4,4,'AUTO_RECOGNIZED',2,2,'AUTO_MATCHED','now')
            """
        )
        self.conn.executemany(
            """
            INSERT INTO IMAGE_IMPORT_AVAILABILITY
              (page_id,session_date,period_number,recognized_is_available,
               resolved_is_available,availability_status,created_at)
            VALUES(1,?,1,0,0,'AUTO_UNAVAILABLE','now')
            """,
            [("月",), ("火",)],
        )
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_REQUESTS
               (student_id,term_id,subject_id,desired_count_per_week,format,status)
               VALUES(1,7,2,2,'1:1','SCHEDULED')"""
        )
        self.conn.execute(
            """INSERT INTO STUDENT_WEEKLY_AVAILABILITY
               (student_id,term_id,day_of_week,period_number,is_available)
               VALUES(1,7,'月',1,1)"""
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_term_is_required_for_regular_batch(self):
        plan = build_import_plan(self.conn, batch_id=1)
        self.assertFalse(plan.can_import)
        self.assertIn("学期を選択", "".join(plan.blockers))

    def test_regular_commit_upserts_requests_and_weekly_availability_only(self):
        plan = build_import_plan(self.conn, batch_id=1, term_id=7)
        self.assertTrue(plan.can_import)
        self.assertTrue(plan.is_regular)
        self.assertEqual((len(plan.enrollment_inserts), len(plan.enrollment_updates)), (0, 1))
        self.assertEqual((len(plan.availability_inserts), len(plan.availability_updates)), (1, 1))

        result, backup = execute_import_batch(
            self.conn, batch_id=1, term_id=7, operator_instructor_id=9,
            backup_dir=self.backup_dir,
        )
        self.assertTrue(result.can_import)
        self.assertTrue(backup.is_file())
        self.assertEqual(
            self.conn.execute(
                "SELECT desired_count_per_week,format,status FROM REGULAR_COURSE_REQUESTS"
            ).fetchone(),
            (4, "1:1", "PENDING"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT day_of_week,is_available FROM STUDENT_WEEKLY_AVAILABILITY ORDER BY day_of_week"
            ).fetchall(),
            [("月", 0), ("火", 0)],
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM REGULAR_COURSE_ENROLLMENTS").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT status FROM IMAGE_IMPORT_BATCHES").fetchone()[0], "IMPORTED")
        targets = {
            row[0] for row in self.conn.execute(
                "SELECT target_table FROM IMAGE_IMPORT_AUDIT_LOG WHERE action_type='IMPORT'"
            )
        }
        self.assertIn("REGULAR_COURSE_REQUESTS", targets)
        self.assertIn("STUDENT_WEEKLY_AVAILABILITY", targets)
        self.assertNotIn("REGULAR_COURSE_ENROLLMENTS", targets)


if __name__ == "__main__":
    unittest.main()
