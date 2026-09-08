import json
import sqlite3
import unittest

from image_import_migrations import ensure_image_import_schema
from review_corrections import correct_resolved_review


PARENT_SCHEMA = """
CREATE TABLE CAMPS (camp_id INTEGER PRIMARY KEY);
CREATE TABLE INSTRUCTORS (
    instructor_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,status TEXT
);
CREATE TABLE STUDENTS (
    student_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,
    enrollment_status TEXT,enrollment_year INTEGER,base_grade INTEGER,track TEXT
);
CREATE TABLE SUBJECTS (
    subject_id INTEGER PRIMARY KEY,course_category TEXT,grade_band TEXT,
    track TEXT,subject_group TEXT,subject_name TEXT
);
CREATE TABLE PERIODS (period_number INTEGER PRIMARY KEY);
CREATE TABLE TIME_SLOTS (slot_id INTEGER PRIMARY KEY);
CREATE TABLE CAMP_COURSE_ENROLLMENTS (enrollment_id INTEGER PRIMARY KEY);
CREATE TABLE CAMP_STUDENT_AVAILABILITY (availability_id INTEGER PRIMARY KEY);
"""


class ReviewCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(PARENT_SCHEMA)
        self.conn.execute("INSERT INTO CAMPS VALUES(1)")
        self.conn.execute("INSERT INTO INSTRUCTORS VALUES(1,'訂正','担当','在籍')")
        self.conn.executemany(
            "INSERT INTO STUDENTS VALUES(?,?,?,?,?,?,?)",
            [
                (1, "旧", "生徒", "在籍", 2026, 7, None),
                (2, "新", "生徒", "在籍", 2026, 7, None),
            ],
        )
        self.conn.executemany(
            "INSERT INTO SUBJECTS VALUES(?,?,?,?,?,?)",
            [
                (1, "個別指導", "中学生", None, "数学", "数学"),
                (2, "個別指導", "中学生", None, "理科", "理科"),
            ],
        )
        self.conn.execute("INSERT INTO PERIODS VALUES(1)")
        ensure_image_import_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_BATCHES
              (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
               source_pdf_path,source_pdf_hash,page_count,status,school_id,school_name,created_at)
            VALUES(1,2026,'夏期','hq-standard',1,'x.pdf',?,1,'REVIEWED','S1','本校','now')
            """,
            ("a" * 64,),
        )
        self.conn.execute(
            "INSERT INTO IMAGE_IMPORT_PAGES(batch_id,page_number,page_image_path,page_image_hash,created_at) VALUES(1,1,'p.png',?,'now')",
            ("b" * 64,),
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
              (page_id,candidate_student_id,candidate_rank,match_status,is_selected,
               reviewed_by_instructor_id,reviewed_at,created_at)
            VALUES(1,1,1,'MANUALLY_CONFIRMED',1,1,'then','now')
            """
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
              (page_id,subject_row_label,resolved_count,count_status,resolved_subject_id,
               subject_match_status,reviewed_by_instructor_id,reviewed_at,created_at)
            VALUES(1,'数学・算数',3,'MANUALLY_CONFIRMED',1,'MANUALLY_CONFIRMED',1,'then','now')
            """
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AVAILABILITY
              (page_id,session_date,period_number,resolved_is_available,
               availability_status,reviewed_by_instructor_id,reviewed_at,created_at)
            VALUES(1,'2026-07-01',1,1,'MANUALLY_CONFIRMED',1,'then','now')
            """
        )
        self.review_ids = {}
        for item_type, student_id, enrollment_id, availability_id, value in (
            ("STUDENT_MATCH", 1, None, None, "旧生徒"),
            ("COUNT_AMBIGUOUS", None, 1, None, "3"),
            ("SUBJECT_MATCH", None, 1, None, "数学/数学"),
            ("AVAILABILITY_AMBIGUOUS", None, None, 1, "対応可"),
        ):
            cursor = self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                  (page_id,item_type,related_page_student_id,related_subject_enrollment_id,
                   related_availability_id,resolution,corrected_value_text,
                   resolved_by_instructor_id,resolved_at,created_at)
                VALUES(1,?,?,?,?,'CORRECTED',?,1,'then','now')
                """,
                (item_type, student_id, enrollment_id, availability_id, value),
            )
            self.review_ids[item_type] = cursor.lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _correct(self, item_type, value, reason="転記ミスを確認"):
        return correct_resolved_review(
            self.conn,
            review_item_id=self.review_ids[item_type],
            replacement_value=value,
            reason=reason,
            operator_instructor_id=1,
        )

    def test_count_correction_preserves_original_and_records_relation_and_reason(self):
        new_id = self._correct("COUNT_AMBIGUOUS", 5)
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved_count,count_status FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS WHERE import_enrollment_id=1"
            ).fetchone(),
            (5, "MANUALLY_CONFIRMED"),
        )
        reviews = self.conn.execute(
            "SELECT review_item_id,resolution,corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS WHERE item_type='COUNT_AMBIGUOUS' ORDER BY review_item_id"
        ).fetchall()
        self.assertEqual(reviews[0][1:], ("CORRECTED", "3"))
        self.assertEqual(reviews[1], (new_id, "CORRECTED", "5"))
        audit = self.conn.execute(
            "SELECT target_id,before_value_json,after_value_json FROM IMAGE_IMPORT_AUDIT_LOG"
        ).fetchone()
        self.assertEqual(audit[0], new_id)
        before = json.loads(audit[1])
        self.assertEqual(before["superseded_review_item_id"], self.review_ids["COUNT_AMBIGUOUS"])
        self.assertEqual(before["correction_reason"], "転記ミスを確認")

    def test_only_latest_correction_can_be_corrected(self):
        new_id = self._correct("COUNT_AMBIGUOUS", 5)
        with self.assertRaisesRegex(ValueError, "最新の履歴"):
            self._correct("COUNT_AMBIGUOUS", 6)
        second = correct_resolved_review(
            self.conn,
            review_item_id=new_id,
            replacement_value=6,
            reason="再確認",
            operator_instructor_id=1,
        )
        self.assertGreater(second, new_id)

    def test_subject_and_availability_can_be_corrected(self):
        self._correct("SUBJECT_MATCH", 2)
        self._correct("AVAILABILITY_AMBIGUOUS", 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved_subject_id FROM IMAGE_IMPORT_SUBJECT_ENROLLMENTS WHERE import_enrollment_id=1"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved_is_available FROM IMAGE_IMPORT_AVAILABILITY WHERE import_availability_id=1"
            ).fetchone()[0],
            0,
        )

    def test_resolved_missing_slot_decision_can_be_corrected(self):
        cursor = self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
              (page_id,item_type,related_availability_id,resolution,corrected_value_text,
               resolved_by_instructor_id,resolved_at,created_at)
            VALUES(1,'SLOT_NOT_FOUND',1,'CORRECTED','対応可',1,'then','now')
            """
        )
        self.conn.commit()
        new_id = correct_resolved_review(
            self.conn,
            review_item_id=cursor.lastrowid,
            replacement_value=0,
            reason="可否を再確認",
            operator_instructor_id=1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved_is_available FROM IMAGE_IMPORT_AVAILABILITY WHERE import_availability_id=1"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT item_type,corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS WHERE review_item_id=?",
                (new_id,),
            ).fetchone(),
            ("SLOT_NOT_FOUND", "対応不可"),
        )

    def test_student_can_be_corrected_without_rewriting_original_review(self):
        original = self.review_ids["STUDENT_MATCH"]
        new_id = self._correct("STUDENT_MATCH", 2)
        selected = self.conn.execute(
            "SELECT candidate_student_id FROM IMAGE_IMPORT_PAGE_STUDENTS WHERE is_selected=1"
        ).fetchone()[0]
        self.assertEqual(selected, 2)
        self.assertEqual(
            self.conn.execute(
                "SELECT corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS WHERE review_item_id=?",
                (original,),
            ).fetchone()[0],
            "旧生徒",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT corrected_value_text FROM IMAGE_IMPORT_REVIEW_ITEMS WHERE review_item_id=?",
                (new_id,),
            ).fetchone()[0],
            "新生徒",
        )

    def test_reason_is_required_and_imported_batch_is_locked(self):
        with self.assertRaisesRegex(ValueError, "訂正理由"):
            self._correct("COUNT_AMBIGUOUS", 5, reason=" ")
        self.conn.execute("UPDATE IMAGE_IMPORT_BATCHES SET status='IMPORTED' WHERE batch_id=1")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "本登録済み"):
            self._correct("COUNT_AMBIGUOUS", 5)


if __name__ == "__main__":
    unittest.main()
