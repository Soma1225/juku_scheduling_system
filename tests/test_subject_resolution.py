import sqlite3
import unittest

from image_import_migrations import ensure_image_import_schema
from subject_resolution import confirm_page_subjects, resolve_page_subjects


class SubjectResolutionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(
            """
            CREATE TABLE CAMPS(camp_id INTEGER PRIMARY KEY);
            CREATE TABLE INSTRUCTORS(
                instructor_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,status TEXT
            );
            CREATE TABLE STUDENTS(
                student_id INTEGER PRIMARY KEY,enrollment_year INTEGER,base_grade INTEGER,track TEXT
            );
            CREATE TABLE SUBJECTS(
                subject_id INTEGER PRIMARY KEY,course_category TEXT,grade_band TEXT,
                track TEXT,subject_group TEXT,subject_name TEXT
            );
            CREATE TABLE PERIODS(period_number INTEGER PRIMARY KEY);
            CREATE TABLE TIME_SLOTS(slot_id INTEGER PRIMARY KEY);
            CREATE TABLE CAMP_COURSE_ENROLLMENTS(enrollment_id INTEGER PRIMARY KEY);
            CREATE TABLE CAMP_STUDENT_AVAILABILITY(availability_id INTEGER PRIMARY KEY);
            """
        )
        self.conn.execute("INSERT INTO CAMPS VALUES(1)")
        self.conn.execute("INSERT INTO INSTRUCTORS VALUES(9,'確認','太郎','在籍')")
        self.conn.executemany(
            "INSERT INTO SUBJECTS VALUES(?,?,?,?,?,?)",
            [
                (1, "個別指導", "中学生", None, "数学", "数学"),
                (2, "個別指導", "高校生", None, "数学", "数1A"),
                (3, "個別指導", "高校生", None, "理科", "物理"),
                (4, "個別指導", "高校生", None, "理科", "化学"),
                (5, "戦略指導", "高校生", None, "戦略指導", "戦略指導(面談)"),
            ],
        )
        ensure_image_import_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _make_page(self, *, student_id, enrollment_year, base_grade, paper_fy, rows):
        self.conn.execute(
            "INSERT INTO STUDENTS VALUES(?,?,?,NULL)",
            (student_id, enrollment_year, base_grade),
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_BATCHES
              (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
               source_pdf_path,source_pdf_hash,page_count,status,school_id,school_name,created_at)
            VALUES(1,?,'夏期','hq-standard',1,'x.pdf',?,1,'PROCESSING','S1','本校','now')
            """,
            (paper_fy, f"{student_id:064d}"),
        )
        batch_id = self.conn.execute("SELECT MAX(batch_id) FROM IMAGE_IMPORT_BATCHES").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGES
              (batch_id,page_number,page_image_path,page_image_hash,created_at)
            VALUES(?,1,'p.png',?,'now')
            """,
            (batch_id, f"{student_id + 100:064d}"),
        )
        page_id = self.conn.execute("SELECT MAX(page_id) FROM IMAGE_IMPORT_PAGES").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
              (page_id,candidate_student_id,candidate_rank,match_confidence,match_status,is_selected,created_at)
            VALUES(?,?,1,1.0,'AUTO_MATCHED',1,'now')
            """,
            (page_id, student_id),
        )
        for label, count in rows:
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                  (page_id,subject_row_label,recognized_count,resolved_count,count_status,created_at)
                VALUES(?,?,?,?,?,'now')
                """,
                (page_id, label, count, count, "AUTO_BLANK" if count == 0 else "AUTO_RECOGNIZED"),
            )
        self.conn.commit()
        return page_id

    def test_middle_school_math_is_auto_matched_and_zero_row_is_ignored(self):
        page_id = self._make_page(
            student_id=1, enrollment_year=2026, base_grade=7, paper_fy=2026,
            rows=[("数学・算数", 5), ("その他", 0)],
        )
        result = resolve_page_subjects(self.conn, page_id=page_id)
        self.assertEqual(result, {"matched": 1, "review": 0, "skipped": 1})
        rows = self.conn.execute(
            "SELECT resolved_subject_id,subject_match_status FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS ORDER BY import_enrollment_id"
        ).fetchall()
        self.assertEqual(rows, [(1, "AUTO_MATCHED"), (None, "PENDING")])

    def test_form_fiscal_year_grade_is_used_instead_of_runtime_grade(self):
        # 2025年度に中3だった生徒の古い用紙は、後年に再処理しても中学生科目になる。
        page_id = self._make_page(
            student_id=2, enrollment_year=2025, base_grade=9, paper_fy=2025,
            rows=[("数学・算数", 3)],
        )
        resolve_page_subjects(self.conn, page_id=page_id)
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved_subject_id,subject_match_status FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS"
            ).fetchone(),
            (1, "AUTO_MATCHED"),
        )

    def test_ambiguous_high_school_science_requires_staff_choice(self):
        page_id = self._make_page(
            student_id=3, enrollment_year=2026, base_grade=10, paper_fy=2026,
            rows=[("理科", 4)],
        )
        result = resolve_page_subjects(self.conn, page_id=page_id)
        self.assertEqual(result["review"], 1)
        enrollment_id = self.conn.execute(
            "SELECT import_enrollment_id FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS"
        ).fetchone()[0]
        self.assertEqual(
            self.conn.execute(
                "SELECT subject_match_status FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS"
            ).fetchone()[0],
            "AMBIGUOUS",
        )
        confirm_page_subjects(
            self.conn,
            page_id=page_id,
            confirmed_subjects={enrollment_id: 4},
            operator_instructor_id=9,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved_subject_id,subject_match_status FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS"
            ).fetchone(),
            (4, "MANUALLY_CONFIRMED"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolution,corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS"
            ).fetchone(),
            ("CORRECTED", "理科/化学"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT action_type,operator_name FROM IMAGE_IMPORT_AUDIT_LOG"
            ).fetchone(),
            ("CORRECT", "確認太郎"),
        )


if __name__ == "__main__":
    unittest.main()
