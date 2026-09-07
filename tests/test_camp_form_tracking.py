import sqlite3
import unittest

from camp_form_tracking import (
    add_manual_target,
    list_tracking_rows,
    mark_all_distributed,
    record_scanned_return,
    sync_regular_targets,
)
from image_import_migrations import ensure_image_import_schema


class CampFormTrackingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """
            CREATE TABLE CAMPS(camp_id INTEGER PRIMARY KEY);
            CREATE TABLE INSTRUCTORS(
              instructor_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,
              last_name_kana TEXT,first_name_kana TEXT,status TEXT
            );
            CREATE TABLE STUDENTS(
              student_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,
              last_name_kana TEXT,first_name_kana TEXT,enrollment_status TEXT
            );
            CREATE TABLE SUBJECTS(
              subject_id INTEGER PRIMARY KEY,course_category TEXT
            );
            CREATE TABLE REGULAR_COURSE_ENROLLMENTS(
              enrollment_id INTEGER PRIMARY KEY,student_id INTEGER,subject_id INTEGER,
              effective_start_date TEXT,effective_end_date TEXT
            );
            CREATE TABLE PERIODS(period_number INTEGER PRIMARY KEY);
            CREATE TABLE TIME_SLOTS(slot_id INTEGER PRIMARY KEY);
            CREATE TABLE CAMP_COURSE_ENROLLMENTS(enrollment_id INTEGER PRIMARY KEY);
            CREATE TABLE CAMP_STUDENT_AVAILABILITY(availability_id INTEGER PRIMARY KEY);
            INSERT INTO CAMPS VALUES(1);
            INSERT INTO INSTRUCTORS VALUES(9,'確認','太郎','かくにん','たろう','在籍');
            INSERT INTO STUDENTS VALUES(1,'個別','一郎','こべつ','いちろう','在籍');
            INSERT INTO STUDENTS VALUES(2,'戦略','二郎','せんりゃく','じろう','在籍');
            INSERT INTO STUDENTS VALUES(3,'退会','三郎','たいかい','さぶろう','退会');
            INSERT INTO SUBJECTS VALUES(1,'個別指導');
            INSERT INTO SUBJECTS VALUES(2,'戦略指導');
            INSERT INTO REGULAR_COURSE_ENROLLMENTS VALUES(1,1,1,'2020-01-01',NULL);
            INSERT INTO REGULAR_COURSE_ENROLLMENTS VALUES(2,2,2,'2020-01-01',NULL);
            INSERT INTO REGULAR_COURSE_ENROLLMENTS VALUES(3,3,1,'2020-01-01',NULL);
            """
        )
        ensure_image_import_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _add_scan_page(self, page_number: int) -> int:
        batch = self.conn.execute(
            "SELECT batch_id FROM IMAGE_IMPORT_BATCHES WHERE camp_id=1 LIMIT 1"
        ).fetchone()
        if batch:
            batch_id = batch[0]
            self.conn.execute(
                "UPDATE IMAGE_IMPORT_BATCHES SET page_count=page_count+1 WHERE batch_id=?",
                (batch_id,),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_BATCHES
                  (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
                   source_pdf_path,source_pdf_hash,page_count,created_at)
                VALUES(1,2026,'夏期','hq-standard',1,'x.pdf',?,1,'now')
                """,
                ("a" * 64,),
            )
            batch_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGES
              (batch_id,page_number,page_image_path,page_image_hash,created_at)
            VALUES(?,?,?,?,'now')
            """,
            (batch_id, page_number, f"p{page_number}.png", str(page_number) * 64),
        )
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_regular_targets_exclude_strategy_only_and_inactive_students(self):
        count = sync_regular_targets(self.conn, camp_id=1, operator_instructor_id=9)
        self.assertEqual(count, 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT student_id,source,status FROM CAMP_FORM_DISTRIBUTIONS"
            ).fetchone(),
            (1, "AUTO_REGULAR", "TARGET"),
        )
        self.assertEqual(
            sync_regular_targets(self.conn, camp_id=1, operator_instructor_id=9), 0
        )

    def test_exception_can_be_added_and_targets_can_be_marked_distributed(self):
        sync_regular_targets(self.conn, camp_id=1, operator_instructor_id=9)
        add_manual_target(
            self.conn, camp_id=1, student_id=2, operator_instructor_id=9
        )
        self.assertEqual(
            mark_all_distributed(self.conn, camp_id=1, operator_instructor_id=9), 2
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM CAMP_FORM_DISTRIBUTIONS WHERE status='DISTRIBUTED' AND distributed_at IS NOT NULL"
            ).fetchone()[0],
            2,
        )

    def test_scanned_exception_is_returned_and_duplicate_pages_are_visible(self):
        first_page = self._add_scan_page(1)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
              (page_id,candidate_student_id,candidate_rank,match_status,is_selected,created_at)
            VALUES(?,2,1,'AUTO_MATCHED',1,'now')
            """,
            (first_page,),
        )
        record_scanned_return(self.conn, page_id=first_page, student_id=2)
        second_page = self._add_scan_page(2)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
              (page_id,candidate_student_id,candidate_rank,match_status,is_selected,created_at)
            VALUES(?,2,1,'AUTO_MATCHED',1,'now')
            """,
            (second_page,),
        )
        record_scanned_return(self.conn, page_id=second_page, student_id=2)
        row = self.conn.execute(
            "SELECT source,status,returned_page_id FROM CAMP_FORM_DISTRIBUTIONS WHERE student_id=2"
        ).fetchone()
        self.assertEqual(row, ("SCAN_DISCOVERED", "RETURNED", first_page))
        tracking = list_tracking_rows(self.conn, camp_id=1)
        self.assertEqual(tracking[0][7], 2)


if __name__ == "__main__":
    unittest.main()
