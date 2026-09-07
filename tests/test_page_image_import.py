import sqlite3
import unittest

from page_image_import import handle_post


class ImageImportDuplicateCheckTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """CREATE TABLE CAMP_COURSE_ENROLLMENTS(
                   enrollment_id INTEGER PRIMARY KEY,
                   camp_id INTEGER NOT NULL,
                   student_id INTEGER NOT NULL,
                   subject_id INTEGER NOT NULL,
                   contracted_count INTEGER NOT NULL,
                   format TEXT NOT NULL,
                   assigned_instructor_id INTEGER,
                   enrollment_end_date TEXT
               )"""
        )

    def tearDown(self):
        self.conn.close()

    def test_reports_no_duplicates_without_writing(self):
        self.conn.execute(
            "INSERT INTO CAMP_COURSE_ENROLLMENTS VALUES(1,1,2,3,5,'1:2',NULL,NULL)"
        )
        before = self.conn.total_changes
        message, query = handle_post(
            {"action": ["check_enrollment_duplicates"]}, self.conn,
        )
        self.assertIn("重複はありません", message)
        self.assertIn("確認対象: 1件", message)
        self.assertEqual(query, {})
        self.assertEqual(self.conn.total_changes, before)

    def test_lists_duplicate_keys_and_enrollment_ids(self):
        self.conn.executemany(
            "INSERT INTO CAMP_COURSE_ENROLLMENTS VALUES(?,?,?,?,?,?,?,?)",
            [
                (10, 1, 2, 3, 5, "1:2", None, None),
                (11, 1, 2, 3, 6, "1:1", 9, None),
            ],
        )
        before = self.conn.total_changes
        message, _ = handle_post(
            {"action": ["check_enrollment_duplicates"]}, self.conn,
        )
        self.assertIn("重複が見つかりました", message)
        self.assertIn("講習会ID=1", message)
        self.assertIn("登録ID: 10, 11", message)
        self.assertEqual(self.conn.total_changes, before)


if __name__ == "__main__":
    unittest.main()
