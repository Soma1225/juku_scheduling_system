import sqlite3
import unittest

from db import get_grade_at_fiscal_year
from image_import_db import (
    classify_camp_enrollment_action,
    find_camp_enrollment_duplicates,
    get_matching_camp_enrollments,
)


class GradeAtFiscalYearTests(unittest.TestCase):
    def test_calculates_grade_for_form_fiscal_year(self):
        self.assertEqual(get_grade_at_fiscal_year(2022, 1, 2026), 5)

    def test_historical_form_does_not_depend_on_execution_date(self):
        self.assertEqual(get_grade_at_fiscal_year(2024, 7, 2025), 8)


class CampEnrollmentInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE CAMP_COURSE_ENROLLMENTS (
                enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                camp_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                contracted_count INTEGER NOT NULL,
                format TEXT NOT NULL DEFAULT '1:2',
                assigned_instructor_id INTEGER,
                enrollment_end_date TEXT
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def insert_enrollment(self, camp_id=1, student_id=2, subject_id=3, count=4):
        self.conn.execute(
            """
            INSERT INTO CAMP_COURSE_ENROLLMENTS
                (camp_id, student_id, subject_id, contracted_count)
            VALUES (?, ?, ?, ?)
            """,
            (camp_id, student_id, subject_id, count),
        )

    def test_duplicate_query_does_not_report_single_row(self):
        self.insert_enrollment()
        self.assertEqual(find_camp_enrollment_duplicates(self.conn), [])

    def test_duplicate_query_reports_group_and_count(self):
        self.insert_enrollment(count=4)
        self.insert_enrollment(count=6)

        duplicates = find_camp_enrollment_duplicates(self.conn)

        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].camp_id, 1)
        self.assertEqual(duplicates[0].student_id, 2)
        self.assertEqual(duplicates[0].subject_id, 3)
        self.assertEqual(duplicates[0].row_count, 2)

    def test_matching_rows_preserve_all_conflicting_values(self):
        self.insert_enrollment(count=4)
        self.insert_enrollment(count=6)

        rows = get_matching_camp_enrollments(self.conn, 1, 2, 3)

        self.assertEqual([row[4] for row in rows], [4, 6])

    def test_action_is_insert_for_no_existing_rows(self):
        self.assertEqual(classify_camp_enrollment_action([]), "INSERT")

    def test_action_is_update_for_one_existing_row(self):
        self.assertEqual(classify_camp_enrollment_action([object()]), "UPDATE")

    def test_action_is_conflict_for_multiple_existing_rows(self):
        self.assertEqual(
            classify_camp_enrollment_action([object(), object()]),
            "CONFLICT",
        )


if __name__ == "__main__":
    unittest.main()
