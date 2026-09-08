import sqlite3
import unittest
from unittest.mock import patch

import app
import layout
import page_instructor_detail
import page_instructors
import page_students


class NoCloseConnection:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


class InstructorDetailQuickViewTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        with open("schema.sql", encoding="utf-8") as schema_file:
            self.conn.executescript(schema_file.read())
        self.conn.execute("INSERT INTO PERIODS VALUES(1, '10:00', '11:00')")
        self.conn.executemany(
            """INSERT INTO STUDENTS(
                   student_id, last_name, first_name, last_name_kana, first_name_kana,
                   enrollment_year, base_grade, enrollment_status)
               VALUES(?, ?, '生徒', ?, 'せいと', 2026, 7, '在籍')""",
            [(1, "山田", "やまだ"), (2, "鈴木", "すずき"), (3, "佐藤", "さとう")],
        )
        self.conn.executemany(
            """INSERT INTO INSTRUCTORS(
                   instructor_id, last_name, first_name, last_name_kana, first_name_kana,
                   academic_year, status)
               VALUES(?, ?, '講師', ?, 'こうし', ?, ?)""",
            [(1, "田中", "たなか", "B3", "在籍"), (2, "高橋", "たかはし", "M1", "在籍")],
        )
        self.conn.executemany(
            """INSERT INTO SUBJECTS(
                   subject_id, course_category, grade_band, subject_group, subject_name)
               VALUES(?, '個別指導', '中学生', ?, ?)""",
            [(10, "数学", "数学"), (20, "英語", "英語")],
        )
        self.conn.execute(
            "INSERT INTO INSTRUCTOR_SUBJECTS(instructor_id, subject_id, proficiency_level) VALUES(1, 10, 2)"
        )
        self.conn.executemany(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id, subject_id, instructor_id, day_of_week, period_number,
                   effective_start_date, effective_end_date)
               VALUES(?, ?, ?, ?, 1, '2026-04-01', ?)""",
            [
                (1, 10, 1, "月", None),
                (2, 20, 1, "火", None),
                (3, 10, 1, "水", "2026-06-01"),
            ],
        )
        self.conn.execute(
            """INSERT INTO FOLLOW_COURSE_ENROLLMENTS(
                   student_id, subject_id, instructor_id, day_of_week, period_number,
                   effective_start_date)
               VALUES(1, 20, 1, '木', 1, '2026-04-01')"""
        )
        self.conn.executemany(
            "INSERT INTO CAMPS(camp_id, camp_name, planned_start_date, planned_end_date) VALUES(?, ?, ?, ?)",
            [
                (1, "夏期講習", "2026-07-20", "2026-08-31"),
                (2, "冬期講習", "2026-12-20", "2027-01-10"),
            ],
        )
        self.conn.executemany(
            """INSERT INTO CAMP_COURSE_ENROLLMENTS(
                   camp_id, student_id, subject_id, contracted_count, format, assigned_instructor_id)
               VALUES(?, ?, ?, 2, '1:2', ?)""",
            [
                (1, 1, 10, 1),
                (2, 2, 20, None),
                (2, 3, 10, 2),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_instructor_detail_collects_assigned_and_continuing_camps(self):
        rows = page_instructor_detail._get_camp_enrollments_for_instructor(self.conn, 1)
        self.assertEqual({row["camp_name"] for row in rows}, {"夏期講習", "冬期講習"})
        self.assertEqual({row["relationship"] for row in rows}, {"個別指定", "継続講師"})
        self.assertNotIn("佐藤生徒", {row["student_name"] for row in rows})

    def test_instructor_detail_renders_all_read_only_sections(self):
        with patch.object(
            page_instructor_detail, "get_conn", return_value=NoCloseConnection(self.conn)
        ):
            output = page_instructor_detail.render({"instructor_id": ["1"]})
        for expected in (
            "田中 講師",
            "学年: B3",
            "担当科目",
            "習熟度",
            "通常授業",
            "教科フォロー",
            "講習会の受講状況",
            "夏期講習",
            "冬期講習",
        ):
            self.assertIn(expected, output)
        self.assertNotIn("method=\"POST\"", output)

    def test_student_and_instructor_lists_use_common_quick_view_hook(self):
        with patch.object(page_students, "get_conn", return_value=NoCloseConnection(self.conn)):
            student_html = page_students.render({})
        with patch.object(page_instructors, "get_conn", return_value=NoCloseConnection(self.conn)):
            instructor_html = page_instructors.render({})

        self.assertIn('class="person-quick-view"', student_html)
        self.assertIn('/student-detail?student_id=1', student_html)
        self.assertIn("現在の通常授業: 1科目", student_html)
        self.assertIn('class="person-quick-view"', instructor_html)
        self.assertIn('/instructor-detail?instructor_id=1', instructor_html)
        self.assertIn("担当科目: 1科目", instructor_html)

    def test_common_layout_contains_single_double_click_behavior(self):
        rendered = layout.render_page("/students", "content").decode("utf-8")
        self.assertIn("function setupPersonQuickView", rendered)
        self.assertIn("setTimeout(function()", rendered)
        self.assertIn("location.href = detailUrl", rendered)
        self.assertIn("showQuickView(el, summaryHtml)", rendered)

    def test_route_and_sidebar_entry_are_registered(self):
        self.assertIn("/instructor-detail", app.ROUTES)
        instructor_menu = dict(next(items for name, items in layout.MENU_GROUPS if name == "講師情報"))
        self.assertEqual(instructor_menu["/instructor-detail"], "講師詳細")


if __name__ == "__main__":
    unittest.main()
