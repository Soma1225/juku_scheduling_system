import sqlite3
import unittest

import page_camp_enrollments
import page_student_instructor_preferences as preferences_page
import scheduler
from image_import_migrations import ensure_image_import_schema


class StudentInstructorPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        with open("schema.sql", encoding="utf-8") as schema_file:
            self.conn.executescript(schema_file.read())

        self.conn.execute(
            """INSERT INTO STUDENTS(
                   student_id, last_name, first_name, last_name_kana, first_name_kana,
                   enrollment_year, base_grade, enrollment_status)
               VALUES(1, '山田', '太郎', 'やまだ', 'たろう', 2026, 7, '在籍')"""
        )
        for instructor_id in range(1, 7):
            self.conn.execute(
                """INSERT INTO INSTRUCTORS(
                       instructor_id, last_name, first_name, last_name_kana, first_name_kana,
                       academic_year, status)
                   VALUES(?, ?, '講師', ?, 'こうし', 'B2', '在籍')""",
                (instructor_id, f"講師{instructor_id}", f"こうし{instructor_id}"),
            )
        self.conn.execute(
            """INSERT INTO SUBJECTS(
                   subject_id, course_category, grade_band, subject_group, subject_name)
               VALUES(10, '個別指導', '中学生', '数学', '数学')"""
        )
        self.conn.execute(
            "INSERT INTO PERIODS(period_number, start_time, end_time) VALUES(1, '10:00', '11:00')"
        )
        self.conn.executemany(
            "INSERT INTO CAMPS(camp_id, camp_name, planned_start_date, planned_end_date) VALUES(?, ?, '2026-07-01', '2026-08-31')",
            [(1, "夏期A"), (2, "夏期B")],
        )

    def tearDown(self):
        self.conn.close()

    def _add_enrollment(self, enrollment_id, camp_id, assigned_instructor_id=None):
        self.conn.execute(
            """INSERT INTO CAMP_COURSE_ENROLLMENTS(
                   enrollment_id, camp_id, student_id, subject_id, contracted_count,
                   format, assigned_instructor_id)
               VALUES(?, ?, 1, 10, 1, '1:2', ?)""",
            (enrollment_id, camp_id, assigned_instructor_id),
        )

    def test_preferred_instructors_follow_assigned_and_continuing_in_rank_order(self):
        self._add_enrollment(100, 1, assigned_instructor_id=1)
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id, subject_id, instructor_id, day_of_week, period_number,
                   effective_start_date)
               VALUES(1, 10, 2, '月', 1, '2026-04-01')"""
        )
        preferences_page.save_student_instructor_preferences(self.conn, 1, [3, 4], [])

        self.assertEqual(scheduler.get_instructor_candidates(self.conn, 100), [1, 2, 3, 4])

    def test_ng_instructors_are_removed_from_assigned_continuing_and_fallback_routes(self):
        self._add_enrollment(101, 1, assigned_instructor_id=1)
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id, subject_id, instructor_id, day_of_week, period_number,
                   effective_start_date)
               VALUES(1, 10, 2, '月', 1, '2026-04-01')"""
        )
        self.conn.executemany(
            "INSERT INTO INSTRUCTOR_SUBJECTS(instructor_id, subject_id, proficiency_level) VALUES(?, 10, 1)",
            [(1,), (2,), (3,)],
        )
        preferences_page.save_student_instructor_preferences(self.conn, 1, [], [1, 2])

        self.assertEqual(scheduler.get_instructor_candidates(self.conn, 101), [3])

    def test_preferences_apply_to_multiple_camps(self):
        self._add_enrollment(102, 1)
        self._add_enrollment(103, 2)
        preferences_page.save_student_instructor_preferences(self.conn, 1, [4], [5])

        self.assertEqual(scheduler.get_instructor_candidates(self.conn, 102), [4])
        self.assertEqual(scheduler.get_instructor_candidates(self.conn, 103), [4])
        rows = self.conn.execute(
            "SELECT student_id, instructor_id FROM STUDENT_INSTRUCTOR_PREFERENCES ORDER BY preference_id"
        ).fetchall()
        self.assertEqual(rows, [(1, 4), (1, 5)])

    def test_more_than_three_can_be_saved_then_edited_and_deleted(self):
        preferences_page.save_student_instructor_preferences(self.conn, 1, [1, 2, 3, 4], [5, 6])
        self.assertEqual(
            preferences_page.get_student_instructor_preferences(self.conn, 1),
            ([1, 2, 3, 4], [5, 6]),
        )

        preferences_page.save_student_instructor_preferences(self.conn, 1, [2], [])
        self.assertEqual(preferences_page.get_student_instructor_preferences(self.conn, 1), ([2], []))

    def test_same_instructor_cannot_be_preferred_and_ng_and_existing_data_is_preserved(self):
        preferences_page.save_student_instructor_preferences(self.conn, 1, [1], [2])
        with self.assertRaisesRegex(ValueError, "両方"):
            preferences_page.save_student_instructor_preferences(self.conn, 1, [3], [3])
        self.assertEqual(preferences_page.get_student_instructor_preferences(self.conn, 1), ([1], [2]))

    def test_database_constraints_reject_invalid_rank_and_cross_type_duplicate(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO STUDENT_INSTRUCTOR_PREFERENCES
                       (student_id, instructor_id, preference_type, priority_rank, created_at)
                   VALUES(1, 1, 'PREFERRED', NULL, '2026-09-08')"""
            )
        self.conn.execute(
            """INSERT INTO STUDENT_INSTRUCTOR_PREFERENCES
                   (student_id, instructor_id, preference_type, priority_rank, created_at)
               VALUES(1, 1, 'PREFERRED', 1, '2026-09-08')"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """INSERT INTO STUDENT_INSTRUCTOR_PREFERENCES
                       (student_id, instructor_id, preference_type, priority_rank, created_at)
                   VALUES(1, 1, 'NG', NULL, '2026-09-08')"""
            )

    def test_versioned_migration_is_recorded_for_existing_databases(self):
        self.assertTrue(ensure_image_import_schema(self.conn))
        applied = self.conn.execute(
            """SELECT 1 FROM APP_SCHEMA_MIGRATIONS
               WHERE migration_id = '002_student_instructor_preferences'"""
        ).fetchone()
        self.assertIsNotNone(applied)
        self.assertFalse(ensure_image_import_schema(self.conn))

    def test_manual_ng_assignment_warns_but_is_saved(self):
        self.conn.execute(
            "INSERT INTO INSTRUCTOR_SUBJECTS(instructor_id, subject_id, proficiency_level) VALUES(1, 10, 1)"
        )
        preferences_page.save_student_instructor_preferences(self.conn, 1, [], [1])
        fields = {
            "action": ["add"],
            "camp_id": ["1"],
            "student_id": ["1"],
            "subject_id": ["10"],
            "contracted_count": ["1"],
            "format": ["1:2"],
            "assigned_instructor_id": ["1"],
        }

        message, query = page_camp_enrollments.handle_post(fields, self.conn)

        self.assertIn("この生徒のNG講師", message)
        self.assertIn("登録自体はそのまま完了", message)
        self.assertEqual(query["student_id"], ["1"])
        saved = self.conn.execute(
            "SELECT assigned_instructor_id FROM CAMP_COURSE_ENROLLMENTS WHERE camp_id = 1"
        ).fetchone()
        self.assertEqual(saved[0], 1)

    def test_render_has_three_default_rows_and_add_buttons(self):
        class NoCloseConnection:
            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def close(self):
                pass

        original_get_conn = preferences_page.get_conn
        preferences_page.get_conn = lambda: NoCloseConnection(self.conn)
        try:
            output = preferences_page.render({"student_id": ["1"]})
        finally:
            preferences_page.get_conn = original_get_conn
        self.assertEqual(output.count('name="preferred_instructor_ids"'), 4)  # 3 rows + template
        self.assertEqual(output.count('name="ng_instructor_ids"'), 4)
        self.assertIn("＋ 推奨講師を追加", output)
        self.assertIn("＋ NG講師を追加", output)


if __name__ == "__main__":
    unittest.main()
