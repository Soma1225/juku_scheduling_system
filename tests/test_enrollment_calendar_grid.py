import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from enrollment_calendar_grid import evaluate_enrollment_slots, resolve_term_for_date
from page_follow_enrollments import insert_follow_enrollment
from page_regular_enrollments import insert_regular_enrollment
import page_follow_enrollments
import page_regular_enrollments


ROOT = Path(__file__).resolve().parents[1]


class EnrollmentCalendarGridTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        self.conn.executemany(
            "INSERT INTO PERIODS(period_number,start_time,end_time) VALUES(?,?,?)",
            [(period, "14:00", "15:00") for period in range(1, 6)],
        )
        self.conn.execute(
            "INSERT INTO TERMS(term_id,term_name,start_date,end_date) VALUES(1,'2026年度前期','2026-03-01','2026-08-31')"
        )
        self.conn.executemany(
            """INSERT INTO STUDENTS(
                   student_id,last_name,first_name,last_name_kana,first_name_kana,
                   enrollment_year,base_grade,enrollment_status)
               VALUES(?,?,?,?,?,?,?,'在籍')""",
            [
                (1, "本人", "一郎", "ほんにん", "いちろう", 2026, 8),
                (2, "生徒", "二郎", "せいと", "じろう", 2026, 8),
                (3, "生徒", "三郎", "せいと", "さぶろう", 2026, 8),
                (4, "生徒", "四郎", "せいと", "しろう", 2026, 8),
            ],
        )
        self.conn.executemany(
            """INSERT INTO INSTRUCTORS(
                   instructor_id,last_name,first_name,last_name_kana,first_name_kana,
                   academic_year,status)
               VALUES(?,?,?,?,?,'B2','在籍')""",
            [
                (1, "田中", "一郎", "たなか", "いちろう"),
                (2, "佐藤", "二郎", "さとう", "じろう"),
            ],
        )
        self.conn.executemany(
            """INSERT INTO SUBJECTS(
                   subject_id,course_category,grade_band,subject_group,subject_name)
               VALUES(?,?,?,?,?)""",
            [
                (1, "個別指導", "中学生", "数学", "数学"),
                (2, "戦略指導", "中学生", "教科フォロー", "教科フォロー(文系)"),
            ],
        )
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
               VALUES(2,1,1,'月',1,'2026-03-01')"""
        )
        self.conn.execute(
            """INSERT INTO FOLLOW_COURSE_ENROLLMENTS(
                   student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
               VALUES(3,2,1,'月',1,'2026-03-01')"""
        )
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
               VALUES(1,1,2,'火',2,'2026-03-01')"""
        )
        self.conn.execute(
            """INSERT INTO STUDENT_WEEKLY_AVAILABILITY(
                   student_id,term_id,day_of_week,period_number,is_available)
               VALUES(1,1,'水',3,1)"""
        )
        self.conn.execute(
            """INSERT INTO INSTRUCTOR_WEEKLY_AVAILABILITY(
                   instructor_id,term_id,day_of_week,period_number,is_available)
               VALUES(1,1,'木',4,1)"""
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_term_is_selected_from_effective_start_date_including_boundaries(self):
        self.assertEqual(resolve_term_for_date(self.conn, "2026-03-01"), (1, "2026年度前期"))
        self.assertEqual(resolve_term_for_date(self.conn, "2026-08-31"), (1, "2026年度前期"))
        with self.assertRaisesRegex(ValueError, "学期が登録されていません"):
            resolve_term_for_date(self.conn, "2026-09-01")

    def test_cross_table_capacity_conflicts_and_unavailability_have_reasons(self):
        term_id, _term_name, decisions = evaluate_enrollment_slots(
            self.conn, 1, 1, "2026-04-01"
        )
        self.assertEqual(term_id, 1)
        self.assertEqual(decisions[("月", 1)].reason, "田中一郎先生：1:2の上限")
        self.assertEqual(decisions[("火", 2)].reason, "本人：授業あり")
        self.assertEqual(decisions[("水", 3)].reason, "本人：対応不可")
        self.assertEqual(decisions[("木", 4)].reason, "田中一郎先生：対応不可")
        self.assertFalse(decisions[("金", 5)].disabled)

    def test_direct_post_validation_blocks_disabled_slots_on_both_insert_paths(self):
        with self.assertRaisesRegex(ValueError, "1:2の上限"):
            insert_regular_enrollment(self.conn, 4, 1, 1, "月", 1, "2026-04-01")
        with self.assertRaisesRegex(ValueError, "本人：授業あり"):
            insert_follow_enrollment(self.conn, 1, 2, 1, "火", 2, "2026-04-01")
        with self.assertRaisesRegex(ValueError, "本人：対応不可"):
            insert_regular_enrollment(self.conn, 1, 1, 1, "水", 3, "2026-04-01")

    def test_regular_and_follow_pages_render_the_same_disabled_grid(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "render.db")
            file_conn = sqlite3.connect(db_path)
            self.conn.backup(file_conn)
            file_conn.close()

            def open_db():
                return sqlite3.connect(db_path)

            query = {
                "student_id": ["1"],
                "subject_id": ["1"],
                "instructor_id": ["1"],
                "effective_start_date": ["2026-04-01"],
            }
            with mock.patch.object(page_regular_enrollments, "get_conn", side_effect=open_db):
                regular_html = page_regular_enrollments.render(query)

            query["subject_id"] = ["2"]
            with mock.patch.object(page_follow_enrollments, "get_conn", side_effect=open_db):
                follow_html = page_follow_enrollments.render(query)

        for rendered in (regular_html, follow_html):
            self.assertIn('class="enrollment-grid"', rendered)
            self.assertIn("本人：授業あり", rendered)
            self.assertIn("1:2の上限", rendered)
            self.assertIn("本人：対応不可", rendered)
            self.assertIn("先生：対応不可", rendered)
            self.assertIn(" disabled", rendered)
            self.assertNotIn('<select name="day_of_week"', rendered)


if __name__ == "__main__":
    unittest.main()
