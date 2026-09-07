import sqlite3
import unittest
from unittest.mock import patch

import scheduler
import page_run_scheduler

try:
    from ortools.sat.python import cp_model  # noqa: F401
    HAS_ORTOOLS = True
except (ImportError, OSError):
    HAS_ORTOOLS = False


SCHEMA = """
CREATE TABLE CAMPS(camp_id INTEGER PRIMARY KEY, planned_start_date TEXT, planned_end_date TEXT);
CREATE TABLE STUDENTS(student_id INTEGER PRIMARY KEY, last_name TEXT, first_name TEXT);
CREATE TABLE SUBJECTS(subject_id INTEGER PRIMARY KEY, subject_name TEXT);
CREATE TABLE CAMP_COURSE_ENROLLMENTS(
    enrollment_id INTEGER PRIMARY KEY, camp_id INTEGER, student_id INTEGER,
    subject_id INTEGER, contracted_count INTEGER, format TEXT
);
CREATE TABLE REGULAR_COURSE_ENROLLMENTS(
    enrollment_id INTEGER PRIMARY KEY, student_id INTEGER, subject_id INTEGER,
    effective_start_date TEXT, effective_end_date TEXT
);
CREATE TABLE TIME_SLOTS(slot_id INTEGER PRIMARY KEY, session_date TEXT, period_number INTEGER);
CREATE TABLE SESSIONS(
    session_id INTEGER PRIMARY KEY, camp_id INTEGER, slot_id INTEGER, instructor_id INTEGER
);
CREATE TABLE ASSIGNMENTS(
    assignment_id INTEGER PRIMARY KEY, session_id INTEGER, student_id INTEGER, subject_id INTEGER
);
"""


class PartialSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO CAMPS VALUES(1, '2026-08-01', '2026-08-31')")
        self.conn.executemany(
            "INSERT INTO STUDENTS VALUES(?, ?, ?)",
            [(1, "対象", "生徒"), (2, "固定", "生徒"), (3, "限定", "生徒")],
        )
        self.conn.executemany(
            "INSERT INTO SUBJECTS VALUES(?, ?)",
            [(10, "数学"), (20, "英語"), (30, "理科")],
        )
        self.conn.executemany(
            "INSERT INTO CAMP_COURSE_ENROLLMENTS VALUES(?, 1, ?, ?, 1, '1:2')",
            [(101, 1, 10), (102, 2, 20), (103, 3, 30)],
        )
        self.conn.execute(
            "INSERT INTO REGULAR_COURSE_ENROLLMENTS VALUES(1, 2, 20, '2026-04-01', NULL)"
        )
        self.conn.executemany(
            "INSERT INTO TIME_SLOTS VALUES(?, ?, 1)",
            [(1, "2026-08-01"), (2, "2026-08-02"), (3, "2026-08-03")],
        )
        self.conn.executemany(
            "INSERT INTO SESSIONS VALUES(?, 1, ?, ?)",
            [(1, 1, 11), (2, 2, 22), (3, 3, 33)],
        )
        self.conn.executemany(
            "INSERT INTO ASSIGNMENTS VALUES(?, ?, ?, ?)",
            [(1, 1, 1, 10), (2, 2, 2, 20), (3, 3, 3, 30)],
        )

    def tearDown(self):
        self.conn.close()

    def test_regular_continuation_uses_start_inclusive_end_exclusive_boundaries(self):
        self.conn.execute(
            "INSERT INTO REGULAR_COURSE_ENROLLMENTS VALUES(2, 1, 10, '2026-08-05', '2026-08-10')"
        )
        self.assertFalse(scheduler.is_regular_continuation_subject(self.conn, 1, 10, "2026-08-04"))
        self.assertTrue(scheduler.is_regular_continuation_subject(self.conn, 1, 10, "2026-08-05"))
        self.assertTrue(scheduler.is_regular_continuation_subject(self.conn, 1, 10, "2026-08-09"))
        self.assertFalse(scheduler.is_regular_continuation_subject(self.conn, 1, 10, "2026-08-10"))

    def test_first_stage_fixes_every_non_target_enrollment(self):
        solved = {
            "status": "OPTIMAL",
            "assignments": {101: [(44, 2)], 102: [(22, 2)], 103: [(33, 3)]},
            "unfulfilled": {},
        }
        with patch.object(scheduler, "_solve_camp_core_once", return_value=solved) as solve_once:
            result = scheduler.solve_camp_core(self.conn, 1, 10, target_student_ids={1})

        kwargs = solve_once.call_args.kwargs
        self.assertEqual(kwargs["fixed_assignments"], {102: [(22, 2)], 103: [(33, 3)]})
        self.assertEqual(kwargs["required_counts"], {102: 1, 103: 1, 101: 1})
        self.assertFalse(result["relaxation_used"])
        self.assertEqual(result["changed_enrollment_ids"], [101])
        self.assertEqual(result["fixed_regular_enrollment_ids"], [102])

    def test_rescue_releases_only_non_target_limited_subjects(self):
        infeasible = {"status": "INFEASIBLE", "assignments": {}, "unfulfilled": {101: 1}}
        rescued = {
            "status": "FEASIBLE",
            "assignments": {101: [(44, 2)], 102: [(22, 2)], 103: [(55, 1)]},
            "unfulfilled": {},
        }
        with patch.object(
            scheduler, "_solve_camp_core_once", side_effect=[infeasible, rescued]
        ) as solve_once:
            result = scheduler.solve_camp_core(self.conn, 1, 10, target_student_ids={1})

        first = solve_once.call_args_list[0].kwargs
        second = solve_once.call_args_list[1].kwargs
        self.assertEqual(first["fixed_assignments"], {102: [(22, 2)], 103: [(33, 3)]})
        self.assertEqual(second["fixed_assignments"], {102: [(22, 2)]})
        self.assertEqual(second["required_counts"], {102: 1, 101: 1, 103: 1})
        self.assertTrue(result["relaxation_used"])
        self.assertEqual(result["moved_non_target_limited_enrollment_ids"], [103])
        self.assertNotIn(102, result["changed_enrollment_ids"])

    def test_omitted_targets_preserve_whole_schedule_call(self):
        expected = {"status": "FEASIBLE", "assignments": {}, "unfulfilled": {}}
        with patch.object(scheduler, "_solve_camp_core_once", return_value=expected) as solve_once:
            actual = scheduler.solve_camp_core(self.conn, 1, 25)
        self.assertIs(actual, expected)
        solve_once.assert_called_once_with(self.conn, 1, 25)

    def test_partial_infeasible_does_not_overwrite_existing_schedule(self):
        failed = {
            "status": "INFEASIBLE",
            "assignments": {},
            "unfulfilled": {101: 1},
            "changed_enrollment_ids": [],
            "unchanged_enrollment_ids": [101, 102, 103],
            "target_student_ids": [1],
            "relaxation_used": True,
            "moved_non_target_limited_enrollment_ids": [],
        }
        with patch.object(scheduler, "solve_camp_core", return_value=failed), patch.object(
            scheduler, "write_schedule_to_db"
        ) as write:
            result = scheduler.run_scheduler_for_camp(
                self.conn, 1, time_limit_seconds=10, target_student_ids={1}
            )
        write.assert_not_called()
        self.assertTrue(result["schedule_preserved"])
        self.assertEqual(result["n_assignments"], 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ASSIGNMENTS").fetchone()[0], 3)


class PartialSchedulerPageTests(unittest.TestCase):
    def test_partial_form_accepts_multiple_students(self):
        fields = {
            "camp_id": ["7"],
            "mode": ["partial"],
            "time_limit_seconds": ["300"],
            "target_student_ids": ["11", "12"],
        }
        with patch.object(page_run_scheduler, "start_scheduling") as start:
            message, query = page_run_scheduler.handle_post(fields, None)
        start.assert_called_once_with(7, 300.0, target_student_ids={11, 12})
        self.assertIn("部分再計算", message)
        self.assertEqual(query, {"camp_id": ["7"]})

    def test_partial_form_requires_at_least_one_student(self):
        with self.assertRaisesRegex(ValueError, "1人以上"):
            page_run_scheduler.handle_post(
                {"camp_id": ["7"], "mode": ["partial"], "target_student_ids": []}, None
            )


@unittest.skipUnless(HAS_ORTOOLS, "OR-Tools is required for the CP-SAT integration test")
class ActualPartialCpSatTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        with open("schema.sql", encoding="utf-8") as schema_file:
            self.conn.executescript(schema_file.read())
        self.conn.execute("INSERT INTO PERIODS VALUES(1, '10:00', '11:00')")
        self.conn.executemany(
            """INSERT INTO TIME_SLOTS(slot_id, session_date, period_number)
               VALUES(?, ?, 1)""",
            [(1, "2026-08-03"), (2, "2026-08-04"), (3, "2026-08-05"), (4, "2026-08-06")],
        )
        self.conn.execute(
            "INSERT INTO CAMPS(camp_id, camp_name, planned_start_date, planned_end_date) "
            "VALUES(1, '夏期', '2026-08-01', '2026-08-31')"
        )
        for student_id in (1, 2, 3):
            self.conn.execute(
                """INSERT INTO STUDENTS(
                       student_id, last_name, first_name, last_name_kana, first_name_kana,
                       enrollment_year, base_grade, enrollment_status)
                   VALUES(?, ?, '生徒', ?, 'せいと', 2026, 7, '在籍')""",
                (student_id, f"生徒{student_id}", f"せいと{student_id}"),
            )
        for instructor_id in (11, 22, 33, 44):
            self.conn.execute(
                """INSERT INTO INSTRUCTORS(
                       instructor_id, last_name, first_name, last_name_kana, first_name_kana,
                       academic_year, status)
                   VALUES(?, ?, '講師', ?, 'こうし', 'B2', '在籍')""",
                (instructor_id, f"講師{instructor_id}", f"こうし{instructor_id}"),
            )
        for subject_id, name in ((10, "数学"), (20, "英語"), (30, "理科")):
            self.conn.execute(
                """INSERT INTO SUBJECTS(
                       subject_id, course_category, grade_band, subject_group, subject_name)
                   VALUES(?, '個別指導', '中学生', ?, ?)""",
                (subject_id, name, name),
            )

    def tearDown(self):
        self.conn.close()

    def _add_availability(self, student_id, instructor_id, slot_id):
        self.conn.execute(
            "INSERT OR REPLACE INTO CAMP_STUDENT_AVAILABILITY(student_id, slot_id, is_available) VALUES(?, ?, 1)",
            (student_id, slot_id),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO CAMP_INSTRUCTOR_AVAILABILITY(instructor_id, slot_id, is_available) VALUES(?, ?, 1)",
            (instructor_id, slot_id),
        )

    def _add_current_assignment(self, session_id, student_id, subject_id, instructor_id, slot_id):
        self.conn.execute(
            "INSERT INTO SESSIONS(session_id, camp_id, slot_id, instructor_id) VALUES(?, 1, ?, ?)",
            (session_id, slot_id, instructor_id),
        )
        self.conn.execute(
            "INSERT INTO ASSIGNMENTS(session_id, student_id, subject_id) VALUES(?, ?, ?)",
            (session_id, student_id, subject_id),
        )

    def test_actual_solver_keeps_all_non_targets_fixed_in_first_stage(self):
        self.conn.executemany(
            """INSERT INTO CAMP_COURSE_ENROLLMENTS(
                   enrollment_id, camp_id, student_id, subject_id, contracted_count,
                   format, assigned_instructor_id)
               VALUES(?, 1, ?, ?, 1, '1:2', ?)""",
            [(101, 1, 10, 22), (102, 2, 20, 22), (103, 3, 30, 33)],
        )
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id, subject_id, instructor_id, day_of_week, period_number,
                   effective_start_date)
               VALUES(2, 20, 22, '月', 1, '2026-04-01')"""
        )
        self._add_current_assignment(1, 1, 10, 11, 1)
        self._add_current_assignment(2, 2, 20, 22, 2)
        self._add_current_assignment(3, 3, 30, 33, 3)
        self._add_availability(1, 22, 4)

        result = scheduler.solve_camp_core(self.conn, 1, 4, target_student_ids={1})

        self.assertIn(result["status"], {"OPTIMAL", "FEASIBLE"})
        self.assertEqual(result["assignments"][101], [(22, 4)])
        self.assertEqual(result["assignments"][102], [(22, 2)])
        self.assertEqual(result["assignments"][103], [(33, 3)])
        self.assertFalse(result["relaxation_used"])

    def test_actual_solver_rescue_moves_only_limited_subject(self):
        self.conn.executemany(
            """INSERT INTO CAMP_COURSE_ENROLLMENTS(
                   enrollment_id, camp_id, student_id, subject_id, contracted_count,
                   format, assigned_instructor_id)
               VALUES(?, 1, ?, ?, 1, ?, ?)""",
            [
                (101, 1, 10, "1:2", 11),
                (102, 2, 20, "1:2", 22),
                (103, 3, 30, "1:1", 33),
            ],
        )
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id, subject_id, instructor_id, day_of_week, period_number,
                   effective_start_date)
               VALUES(2, 20, 22, '日', 1, '2026-04-01')"""
        )
        self._add_current_assignment(1, 1, 10, 44, 4)
        self._add_current_assignment(2, 2, 20, 22, 2)
        self._add_current_assignment(3, 3, 30, 11, 1)
        self._add_availability(1, 11, 1)
        self._add_availability(3, 33, 3)

        result = scheduler.solve_camp_core(self.conn, 1, 4, target_student_ids={1})

        self.assertIn(result["status"], {"OPTIMAL", "FEASIBLE"})
        self.assertTrue(result["relaxation_used"])
        self.assertEqual(result["assignments"][101], [(11, 1)])
        self.assertEqual(result["assignments"][102], [(22, 2)])
        self.assertEqual(result["assignments"][103], [(33, 3)])
        self.assertEqual(result["moved_non_target_limited_enrollment_ids"], [103])


if __name__ == "__main__":
    unittest.main()
