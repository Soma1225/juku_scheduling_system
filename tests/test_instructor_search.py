import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
from instructor_search import search_instructors
import layout
import page_instructor_search


ROOT = Path(__file__).resolve().parents[1]


class InstructorSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        self.conn.executemany(
            "INSERT INTO PERIODS(period_number,start_time,end_time) VALUES(?,?,?)",
            [(period, "14:00", "15:00") for period in range(1, 6)],
        )
        self.conn.execute(
            "INSERT INTO TERMS(term_id,term_name,start_date,end_date) VALUES(1,'2026年度','2026-01-01','2026-12-31')"
        )
        self.conn.executemany(
            """INSERT INTO STUDENTS(
                   student_id,last_name,first_name,last_name_kana,first_name_kana,
                   enrollment_year,base_grade,enrollment_status)
               VALUES(?,?,?,?,?,?,?,'在籍')""",
            [
                (1, "生徒", "一郎", "せいと", "いちろう", 2026, 8),
                (2, "生徒", "二郎", "せいと", "じろう", 2026, 8),
                (3, "生徒", "三郎", "せいと", "さぶろう", 2026, 8),
            ],
        )
        self.conn.executemany(
            """INSERT INTO INSTRUCTORS(
                   instructor_id,last_name,first_name,last_name_kana,first_name_kana,
                   academic_year,status)
               VALUES(?,?,?,?,?,'B2',?)""",
            [
                (1, "田中", "一郎", "たなか", "いちろう", "在籍"),
                (2, "佐藤", "二郎", "さとう", "じろう", "在籍"),
                (3, "退職", "三郎", "たいしょく", "さぶろう", "辞職"),
            ],
        )
        self.conn.executemany(
            """INSERT INTO SUBJECTS(
                   subject_id,course_category,grade_band,subject_group,subject_name)
               VALUES(?,?,?,?,?)""",
            [
                (1, "個別指導", "中学生", "数学", "数学"),
                (2, "個別指導", "中学生", "英語", "英語"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO INSTRUCTOR_SUBJECTS(instructor_id,subject_id,proficiency_level) VALUES(?,?,?)",
            [(1, 1, 2), (2, 1, 1), (3, 1, 2)],
        )
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
               VALUES(1,1,1,'月',1,'2026-01-01')"""
        )
        self.conn.execute(
            """INSERT INTO FOLLOW_COURSE_ENROLLMENTS(
                   student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
               VALUES(2,1,1,'月',1,'2026-01-01')"""
        )
        self.conn.execute(
            """INSERT INTO INSTRUCTOR_WEEKLY_AVAILABILITY(
                   instructor_id,term_id,day_of_week,period_number,is_available)
               VALUES(1,1,'火',2,0)"""
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_level_filter_excludes_inactive_and_returns_free_slots(self):
        report = search_instructors(
            self.conn, subject_id=1, minimum_level=2, as_of_date="2026-09-12"
        )
        self.assertEqual(report["term_name"], "2026年度")
        self.assertEqual(len(report["instructors"]), 1)
        instructor = report["instructors"][0]
        self.assertEqual(instructor["instructor_name"], "田中一郎")
        self.assertEqual(instructor["proficiency_level"], 2)
        self.assertNotIn(("月", 1), instructor["free_slots"])
        self.assertNotIn(("火", 2), instructor["free_slots"])
        self.assertIn(("水", 3), instructor["free_slots"])

    def test_level_one_returns_both_active_instructors(self):
        report = search_instructors(
            self.conn, subject_id=1, minimum_level=1, as_of_date="2026-09-12"
        )
        self.assertEqual(
            {item["instructor_name"] for item in report["instructors"]},
            {"田中一郎", "佐藤二郎"},
        )

    def test_no_matching_instructor_is_not_an_error(self):
        report = search_instructors(
            self.conn, subject_id=2, minimum_level=1, as_of_date="2026-09-12"
        )
        self.assertEqual(report["instructors"], [])
        self.assertEqual(report["term_name"], "2026年度")

    def test_page_route_and_sidebar_render_search_results(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "search.db")
            file_conn = sqlite3.connect(db_path)
            self.conn.backup(file_conn)
            file_conn.close()

            def open_db():
                return sqlite3.connect(db_path)

            with mock.patch.object(page_instructor_search, "get_conn", side_effect=open_db):
                rendered = page_instructor_search.render(
                    {"subject_id": ["1"], "minimum_level": ["2"]}
                )
        self.assertIn("田中一郎", rendered)
        self.assertNotIn("退職三郎", rendered)
        self.assertIn("月2限", rendered)
        self.assertNotIn("月1限、", rendered)
        self.assertIn("対象学期：2026年度", rendered)
        self.assertEqual(app.ROUTES["/instructor-search"], (page_instructor_search.render, None))
        instructor_menu = dict(next(items for name, items in layout.MENU_GROUPS if name == "講師情報"))
        self.assertEqual(instructor_menu["/instructor-search"], "科目・レベルで講師検索")


if __name__ == "__main__":
    unittest.main()
