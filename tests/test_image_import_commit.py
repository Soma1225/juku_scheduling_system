import sqlite3
import tempfile
import unittest
from pathlib import Path

from image_import_commit import build_import_plan, execute_import_batch
from image_import_migrations import ensure_image_import_schema


class ImageImportCommitTests(unittest.TestCase):
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
            CREATE TABLE TIME_SLOTS(
              slot_id INTEGER PRIMARY KEY,session_date TEXT,period_number INTEGER REFERENCES PERIODS(period_number)
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
            INSERT INTO CAMPS VALUES(1);
            INSERT INTO INSTRUCTORS VALUES(9,'確認','太郎','在籍');
            INSERT INTO STUDENTS VALUES(1);
            INSERT INTO SUBJECTS VALUES(2);
            INSERT INTO PERIODS VALUES(1);
            INSERT INTO TIME_SLOTS VALUES(3,'2026-07-01',1);
            """
        )
        ensure_image_import_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_BATCHES
              (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
               source_pdf_path,source_pdf_hash,page_count,status,created_at)
            VALUES(1,2026,'夏期','hq-standard',1,'x.pdf',?,1,'REVIEWED','now')
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
            VALUES(1,'数学・算数',5,5,'AUTO_RECOGNIZED',2,2,'AUTO_MATCHED','now')
            """
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AVAILABILITY
              (page_id,session_date,period_number,matched_slot_id,recognized_is_available,
               resolved_is_available,availability_status,created_at)
            VALUES(1,'2026-07-01',1,3,1,1,'AUTO_AVAILABLE','now')
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_insert_plan_backup_and_atomic_import(self):
        plan = build_import_plan(self.conn, batch_id=1)
        self.assertTrue(plan.can_import)
        self.assertEqual(len(plan.enrollment_inserts), 1)
        self.assertEqual(len(plan.availability_inserts), 1)
        result, backup_path = execute_import_batch(
            self.conn, batch_id=1, operator_instructor_id=9, backup_dir=self.backup_dir
        )
        self.assertTrue(result.can_import)
        self.assertTrue(backup_path.is_file())
        self.assertEqual(
            self.conn.execute(
                "SELECT camp_id,student_id,subject_id,contracted_count,format FROM CAMP_COURSE_ENROLLMENTS"
            ).fetchone(),
            (1, 1, 2, 5, "1:2"),
        )
        self.assertEqual(
            self.conn.execute("SELECT student_id,slot_id,is_available FROM CAMP_STUDENT_AVAILABILITY").fetchone(),
            (1, 3, 1),
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM IMAGE_IMPORT_BATCHES").fetchone()[0],
            "IMPORTED",
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM IMAGE_IMPORT_AUDIT_LOG WHERE action_type='IMPORT'").fetchone()[0],
            3,
        )

    def test_one_existing_enrollment_is_update_and_preserves_other_fields(self):
        self.conn.execute(
            """
            INSERT INTO CAMP_COURSE_ENROLLMENTS
              (camp_id,student_id,subject_id,contracted_count,format,assigned_instructor_id,enrollment_end_date)
            VALUES(1,1,2,3,'1:1',9,'2026-08-20')
            """
        )
        self.conn.commit()
        plan = build_import_plan(self.conn, batch_id=1)
        self.assertTrue(plan.can_import)
        self.assertEqual(len(plan.enrollment_updates), 1)
        execute_import_batch(
            self.conn, batch_id=1, operator_instructor_id=9, backup_dir=self.backup_dir
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT contracted_count,format,assigned_instructor_id,enrollment_end_date FROM CAMP_COURSE_ENROLLMENTS"
            ).fetchone(),
            (5, "1:1", 9, "2026-08-20"),
        )

    def test_duplicate_existing_enrollments_block_import(self):
        self.conn.executemany(
            """
            INSERT INTO CAMP_COURSE_ENROLLMENTS
              (camp_id,student_id,subject_id,contracted_count,format)
            VALUES(1,1,2,?,'1:2')
            """,
            [(3,), (4,)],
        )
        self.conn.commit()
        plan = build_import_plan(self.conn, batch_id=1)
        self.assertFalse(plan.can_import)
        self.assertEqual(len(plan.conflicts), 1)
        with self.assertRaises(ValueError):
            execute_import_batch(
                self.conn, batch_id=1, operator_instructor_id=9, backup_dir=self.backup_dir
            )
        self.assertFalse(self.backup_dir.exists())


if __name__ == "__main__":
    unittest.main()
