import sqlite3
import unittest

from image_import_migrations import ensure_image_import_schema


PARENT_SCHEMA = """
CREATE TABLE CAMPS (camp_id INTEGER PRIMARY KEY);
CREATE TABLE INSTRUCTORS (instructor_id INTEGER PRIMARY KEY);
CREATE TABLE STUDENTS (student_id INTEGER PRIMARY KEY);
CREATE TABLE SUBJECTS (subject_id INTEGER PRIMARY KEY);
CREATE TABLE PERIODS (period_number INTEGER PRIMARY KEY);
CREATE TABLE TIME_SLOTS (slot_id INTEGER PRIMARY KEY);
CREATE TABLE CAMP_COURSE_ENROLLMENTS (enrollment_id INTEGER PRIMARY KEY);
CREATE TABLE CAMP_STUDENT_AVAILABILITY (availability_id INTEGER PRIMARY KEY);
"""


class ImageImportMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(PARENT_SCHEMA)
        for table, column in (
            ("CAMPS", "camp_id"),
            ("INSTRUCTORS", "instructor_id"),
            ("STUDENTS", "student_id"),
            ("SUBJECTS", "subject_id"),
            ("PERIODS", "period_number"),
        ):
            self.conn.execute(f"INSERT INTO {table} ({column}) VALUES (1)")

    def tearDown(self):
        self.conn.close()

    def migrate_and_seed_review_item(self):
        ensure_image_import_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_BATCHES
                (camp_id,paper_fiscal_year,paper_type,layout_key,layout_version,
                 source_pdf_path,source_pdf_hash,page_count,created_at)
            VALUES (1,2026,'夏期','hq-summer',1,'sample.pdf',?,2,'2026-09-06T00:00:00Z')
            """,
            ("a" * 64,),
        )
        for page_number in (1, 2):
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_PAGES
                    (batch_id,page_number,page_image_path,page_image_hash,created_at)
                VALUES (1,?,?,?,'2026-09-06T00:00:00Z')
                """,
                (page_number, f"page-{page_number}.png", str(page_number) * 64),
            )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_PAGE_STUDENTS
                (page_id,candidate_student_id,candidate_rank,match_status,created_at)
            VALUES (1,1,1,'AMBIGUOUS','2026-09-06T00:00:00Z')
            """
        )
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                (page_id,item_type,related_page_student_id,crop_image_path,
                 candidate_value_text,created_at)
            VALUES (1,'STUDENT_MATCH',1,'name.png','生徒候補','2026-09-06T00:00:00Z')
            """
        )

    def test_migration_creates_eight_tables_and_is_idempotent(self):
        self.assertTrue(ensure_image_import_schema(self.conn))
        self.assertFalse(ensure_image_import_schema(self.conn))
        table_count = self.conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name LIKE 'IMAGE_IMPORT_%'
            """
        ).fetchone()[0]
        self.assertEqual(table_count, 8)

    def test_auto_blank_requires_both_values_to_be_zero(self):
        self.migrate_and_seed_review_item()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                    (page_id,subject_row_label,recognized_count,resolved_count,
                     count_status,created_at)
                VALUES (1,'数学',NULL,0,'AUTO_BLANK','2026-09-06T00:00:00Z')
                """
            )

    def test_auto_subject_match_requires_a_recognized_subject(self):
        self.migrate_and_seed_review_item()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_SUBJECT_ENROLLMENTS
                    (page_id,subject_row_label,resolved_subject_id,
                     subject_match_status,created_at)
                VALUES (1,'数学',1,'AUTO_MATCHED','2026-09-06T00:00:00Z')
                """
            )

    def test_auto_availability_requires_a_recognized_value(self):
        self.migrate_and_seed_review_item()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_AVAILABILITY
                    (page_id,session_date,period_number,resolved_is_available,
                     availability_status,created_at)
                VALUES (1,'2026-07-01',1,1,'AUTO_AVAILABLE','2026-09-06T00:00:00Z')
                """
            )

    def test_cross_page_review_target_is_rejected(self):
        self.migrate_and_seed_review_item()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "same page_id"):
            self.conn.execute(
                """
                INSERT INTO IMAGE_IMPORT_REVIEW_ITEMS
                    (page_id,item_type,related_page_student_id,created_at)
                VALUES (2,'STUDENT_MATCH',1,'2026-09-06T00:00:00Z')
                """
            )

    def test_target_and_created_at_are_immutable_but_no_op_updates_work(self):
        self.migrate_and_seed_review_item()
        self.conn.execute(
            "UPDATE IMAGE_IMPORT_REVIEW_ITEMS SET page_id=page_id WHERE review_item_id=1"
        )
        for column, value in (("page_id", 2), ("created_at", "changed")):
            with self.subTest(column=column):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    self.conn.execute(
                        f"UPDATE IMAGE_IMPORT_REVIEW_ITEMS SET {column}=? WHERE review_item_id=1",
                        (value,),
                    )

    def test_pending_content_can_change_only_before_resolution(self):
        self.migrate_and_seed_review_item()
        self.conn.execute(
            "UPDATE IMAGE_IMPORT_REVIEW_ITEMS SET crop_image_path='new.png' WHERE review_item_id=1"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "only be changed"):
            self.conn.execute(
                """
                UPDATE IMAGE_IMPORT_REVIEW_ITEMS
                SET resolution='APPROVED', resolved_by_instructor_id=1,
                    resolved_at='2026-09-06T01:00:00Z', candidate_value_text='late change'
                WHERE review_item_id=1
                """
            )

        self.conn.execute(
            """
            UPDATE IMAGE_IMPORT_REVIEW_ITEMS
            SET resolution='APPROVED', resolved_by_instructor_id=1,
                resolved_at='2026-09-06T01:00:00Z'
            WHERE review_item_id=1
            """
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "only be changed"):
            self.conn.execute(
                "UPDATE IMAGE_IMPORT_REVIEW_ITEMS SET crop_image_path='late.png' WHERE review_item_id=1"
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot be reopened"):
            self.conn.execute(
                """
                UPDATE IMAGE_IMPORT_REVIEW_ITEMS
                SET resolution='PENDING', resolved_by_instructor_id=NULL, resolved_at=NULL
                WHERE review_item_id=1
                """
            )

    def test_audit_log_is_append_only(self):
        self.migrate_and_seed_review_item()
        self.conn.execute(
            """
            INSERT INTO IMAGE_IMPORT_AUDIT_LOG
                (batch_id,action_type,target_table,actor_type,created_at)
            VALUES (1,'RECOGNIZE','IMAGE_IMPORT_PAGES','SYSTEM','2026-09-06T00:00:00Z')
            """
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.conn.execute("UPDATE IMAGE_IMPORT_AUDIT_LOG SET target_id=1")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.conn.execute("DELETE FROM IMAGE_IMPORT_AUDIT_LOG")


if __name__ == "__main__":
    unittest.main()
