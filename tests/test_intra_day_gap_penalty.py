import sqlite3
import unittest

import scheduler

try:
    from ortools.sat.python import cp_model  # noqa: F401

    HAS_ORTOOLS = True
except (ImportError, OSError):
    HAS_ORTOOLS = False


@unittest.skipUnless(HAS_ORTOOLS, "OR-Tools is required for the CP-SAT integration test")
class IntraDayGapPenaltyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        with open("schema.sql", encoding="utf-8") as schema_file:
            self.conn.executescript(schema_file.read())
        self.conn.executemany(
            "INSERT INTO PERIODS(period_number, start_time, end_time) VALUES(?, ?, ?)",
            [(1, "10:00", "11:00"), (2, "11:00", "12:00"), (3, "12:00", "13:00")],
        )
        self.conn.executemany(
            "INSERT INTO TIME_SLOTS(slot_id, session_date, period_number) VALUES(?, '2026-08-03', ?)",
            [(1, 1), (2, 2), (3, 3)],
        )
        self.conn.execute(
            "INSERT INTO CAMPS(camp_id, camp_name, planned_start_date, planned_end_date) "
            "VALUES(1, '夏期', '2026-08-03', '2026-08-03')"
        )
        for student_id in (1, 2):
            self.conn.execute(
                """INSERT INTO STUDENTS(
                       student_id, last_name, first_name, last_name_kana, first_name_kana,
                       enrollment_year, base_grade, enrollment_status)
                   VALUES(?, ?, '生徒', ?, 'せいと', 2026, 7, '在籍')""",
                (student_id, f"生徒{student_id}", f"せいと{student_id}"),
            )
        for instructor_id in (11, 22):
            self.conn.execute(
                """INSERT INTO INSTRUCTORS(
                       instructor_id, last_name, first_name, last_name_kana, first_name_kana,
                       academic_year, status)
                   VALUES(?, ?, '講師', ?, 'こうし', 'B2', '在籍')""",
                (instructor_id, f"講師{instructor_id}", f"こうし{instructor_id}"),
            )
        for subject_id, name in ((10, "数学"), (20, "英語")):
            self.conn.execute(
                """INSERT INTO SUBJECTS(
                       subject_id, course_category, grade_band, subject_group, subject_name)
                   VALUES(?, '個別指導', '中学生', ?, ?)""",
                (subject_id, name, name),
            )

    def tearDown(self):
        self.conn.close()

    def _add_enrollment(self, enrollment_id, student_id, subject_id, instructor_id):
        self.conn.execute(
            """INSERT INTO CAMP_COURSE_ENROLLMENTS(
                   enrollment_id, camp_id, student_id, subject_id, contracted_count,
                   format, assigned_instructor_id)
               VALUES(?, 1, ?, ?, 1, '1:2', ?)""",
            (enrollment_id, student_id, subject_id, instructor_id),
        )

    def _allow(self, student_id, instructor_id, *slot_ids):
        for slot_id in slot_ids:
            self.conn.execute(
                "INSERT INTO CAMP_STUDENT_AVAILABILITY(student_id, slot_id, is_available) VALUES(?, ?, 1)",
                (student_id, slot_id),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO CAMP_INSTRUCTOR_AVAILABILITY(instructor_id, slot_id, is_available) VALUES(?, ?, 1)",
                (instructor_id, slot_id),
            )

    def _assigned_periods(self, result):
        slot_ids = [slot_id for pairs in result["assignments"].values() for _iid, slot_id in pairs]
        placeholders = ",".join("?" * len(slot_ids))
        return [
            row[0]
            for row in self.conn.execute(
                f"SELECT period_number FROM TIME_SLOTS WHERE slot_id IN ({placeholders}) ORDER BY period_number",
                slot_ids,
            ).fetchall()
        ]

    def test_student_sessions_prefer_adjacent_periods(self):
        self._add_enrollment(101, 1, 10, 11)
        self._add_enrollment(102, 1, 20, 22)
        self._allow(1, 11, 1)
        self._allow(1, 22, 2, 3)

        result = scheduler.solve_camp_core(self.conn, 1, 5)

        self.assertIn(result["status"], {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(self._assigned_periods(result), [1, 2])

    def test_instructor_sessions_prefer_adjacent_periods(self):
        self._add_enrollment(101, 1, 10, 11)
        self._add_enrollment(102, 2, 20, 11)
        self._allow(1, 11, 1)
        self._allow(2, 11, 2, 3)

        result = scheduler.solve_camp_core(self.conn, 1, 5)

        self.assertIn(result["status"], {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(self._assigned_periods(result), [1, 2])


if __name__ == "__main__":
    unittest.main()
