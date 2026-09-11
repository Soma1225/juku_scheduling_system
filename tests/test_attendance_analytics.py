import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from attendance_analytics import get_instructor_performance, get_utilization_report
import app
import layout
import page_attendance_analytics


ROOT = Path(__file__).resolve().parents[1]


class AttendanceAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
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
                (2, "個別指導", "中学生", "英語", "英語"),
            ],
        )
        self.conn.executemany(
            """INSERT INTO ATTENDANCE_RECORDS(
                   session_date,student_id,subject_id,instructor_id,period_number,status)
               VALUES(?,?,?,?,?,?)""",
            [
                # 9/1 田中1限：1:2（同じ数学なので科目別では1コマ）
                ("2026-09-01", 1, 1, 1, 1, "出席"),
                ("2026-09-01", 2, 1, 1, 1, "出席"),
                # 9/1 田中2限：1:1
                ("2026-09-01", 3, 2, 1, 2, "出席"),
                # 9/1 佐藤1限：想定外の3人＝その他
                ("2026-09-01", 1, 1, 2, 1, "出席"),
                ("2026-09-01", 2, 1, 2, 1, "出席"),
                ("2026-09-01", 3, 2, 2, 1, "出席"),
                # 9/2 田中1限：異なる2科目の1:2
                ("2026-09-02", 1, 1, 1, 1, "出席"),
                ("2026-09-02", 2, 2, 1, 1, "出席"),
                # 欠席と別月は対象外
                ("2026-09-03", 1, 1, 1, 1, "欠席"),
                ("2026-10-01", 1, 1, 1, 1, "出席"),
            ],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_month_utilization_and_daily_breakdown(self):
        report = get_utilization_report(self.conn, "2026-09")
        self.assertEqual(report["total_sessions"], 4)
        self.assertEqual(report["one_to_one_sessions"], 1)
        self.assertEqual(report["one_to_two_sessions"], 2)
        self.assertEqual(report["other_sessions"], 1)
        self.assertEqual(report["utilization_rate"], 50.0)
        self.assertEqual(
            [
                (item["session_date"], item["total_sessions"], item["one_to_two_sessions"], item["utilization_rate"])
                for item in report["daily"]
            ],
            [("2026-09-01", 3, 1, 33.3), ("2026-09-02", 1, 1, 100.0)],
        )

    def test_instructor_totals_subjects_and_one_to_two_breakdown(self):
        report = get_instructor_performance(self.conn, 1, "2026-09")
        self.assertEqual(report["total_sessions"], 3)
        self.assertEqual(report["one_to_one_sessions"], 1)
        self.assertEqual(report["one_to_two_sessions"], 2)
        self.assertEqual(report["other_sessions"], 0)
        self.assertEqual(
            {item["subject_name"]: item["session_count"] for item in report["subjects"]},
            {"数学": 2, "英語": 2},
        )

    def test_month_without_attendance_returns_zero_instead_of_error(self):
        utilization = get_utilization_report(self.conn, "2025-01")
        performance = get_instructor_performance(self.conn, 1, "2025-01")
        for report in (utilization, performance):
            self.assertEqual(report["total_sessions"], 0)
            self.assertEqual(report["one_to_two_sessions"], 0)
            self.assertEqual(report["utilization_rate"], 0.0)
        self.assertEqual(utilization["daily"], [])
        self.assertEqual(performance["subjects"], [])

    def test_pages_routes_and_sidebar_render_reports(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "analytics.db")
            file_conn = sqlite3.connect(db_path)
            self.conn.backup(file_conn)
            file_conn.close()

            def open_db():
                return sqlite3.connect(db_path)

            with mock.patch.object(page_attendance_analytics, "get_conn", side_effect=open_db):
                utilization_html = page_attendance_analytics.render_utilization(
                    {"month": ["2026-09"]}
                )
                performance_html = page_attendance_analytics.render_instructor_performance(
                    {"month": ["2026-09"], "instructor_id": ["1"]}
                )
        self.assertIn("50.0%", utilization_html)
        self.assertIn("2026-09-01", utilization_html)
        self.assertIn("田中 一郎の実績", performance_html)
        self.assertIn("科目ごとのコマ数", performance_html)
        self.assertIn("支払い金額は計算しません", performance_html)
        self.assertEqual(app.ROUTES["/utilization"], (page_attendance_analytics.render_utilization, None))
        self.assertEqual(
            app.ROUTES["/instructor-performance"],
            (page_attendance_analytics.render_instructor_performance, None),
        )
        analytics_menu = dict(next(items for name, items in layout.MENU_GROUPS if name == "集計"))
        self.assertEqual(analytics_menu["/utilization"], "稼働率")
        self.assertEqual(analytics_menu["/instructor-performance"], "講師実績確認")


if __name__ == "__main__":
    unittest.main()
