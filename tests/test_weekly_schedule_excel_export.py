import io
import sqlite3
import unittest
from pathlib import Path

import openpyxl
from timetable_snapshot_import import parse_timetable_snapshot

from weekly_schedule_excel_export import (
    TEMPLATE_PATH,
    WeeklyScheduleCapacityError,
    build_classroom_weekly_workbook,
    export_instructor_weekly_xlsx,
    export_student_weekly_xlsx,
)
import app
import layout


ROOT = Path(__file__).resolve().parents[1]


class WeeklyScheduleExcelExportTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        self.conn.executemany(
            "INSERT INTO PERIODS(period_number,start_time,end_time) VALUES(?,?,?)",
            [(1, "14:20", "15:40"), (2, "15:50", "17:10"), (3, "17:20", "18:40"),
             (4, "19:00", "20:20"), (5, "20:30", "21:50")],
        )
        self.conn.executemany(
            """INSERT INTO STUDENTS
               (student_id,last_name,first_name,last_name_kana,first_name_kana,
                enrollment_year,base_grade,enrollment_status,gender)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                (1, "山田", "花子", "やまだ", "はなこ", 2026, 8, "在籍", "女"),
                (2, "鈴木", "太郎", "すずき", "たろう", 2026, 7, "在籍", "男"),
            ],
        )
        self.conn.execute(
            """INSERT INTO INSTRUCTORS
               (instructor_id,last_name,first_name,last_name_kana,first_name_kana,short_name,academic_year,status)
               VALUES(1,'田中','一郎','たなか','いちろう','田中','B2','在籍')"""
        )
        self.conn.executemany(
            """INSERT INTO SUBJECTS
               (subject_id,course_category,grade_band,subject_group,subject_name)
               VALUES(?,?,?,?,?)""",
            [
                (1, "個別指導", "中学生", "数学", "数学"),
                (2, "戦略指導", "中学生", "教科フォロー", "教科フォロー(文系)"),
            ],
        )
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS
               (student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date,effective_end_date)
               VALUES(1,1,1,'月',1,'2026-04-01',NULL)"""
        )
        self.conn.executemany(
            """INSERT INTO FOLLOW_COURSE_ENROLLMENTS
               (student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date,effective_end_date)
               VALUES(?,?,?,?,?,'2026-04-01',NULL)""",
            [(1, 2, 1, "火", 2), (2, 2, 1, "月", 1)],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_student_export_uses_vba_coordinates_and_preserves_template(self):
        original = openpyxl.load_workbook(TEMPLATE_PATH)
        original_merges = {str(item) for item in original["5コマ"].merged_cells.ranges}
        body, filename = export_student_weekly_xlsx(self.conn, 1)
        workbook = openpyxl.load_workbook(io.BytesIO(body))
        sheet = workbook["生徒週間時間割"]

        self.assertEqual(filename, "山田花子_週間時間割.xlsx")
        self.assertEqual(workbook.sheetnames, ["生徒週間時間割"])
        self.assertEqual(sheet["B5"].value, "中2")
        self.assertEqual(sheet["I5"].value, "山田花子")
        self.assertEqual(sheet["S5"].value, "さん")
        self.assertEqual(sheet["B10"].value, "科目")
        self.assertEqual(sheet["B18"].value, "講師")
        self.assertEqual([sheet.cell(12, col).value for col in range(7, 13)], list("月火水木金土"))
        self.assertEqual([sheet.cell(20, col).value for col in range(7, 13)], list("月火水木金土"))
        self.assertEqual(sheet["G13"].value, "数学")
        self.assertEqual(sheet["G21"].value, "田中")
        self.assertEqual(sheet["H14"].value, "教科フォロー(文系)")
        self.assertEqual(sheet["H22"].value, "田中")
        self.assertEqual({str(item) for item in sheet.merged_cells.ranges}, original_merges)

    def test_instructor_export_groups_two_students_in_one_slot(self):
        body, filename = export_instructor_weekly_xlsx(self.conn, 1)
        workbook = openpyxl.load_workbook(io.BytesIO(body))
        sheet = workbook["講師週間時間割"]

        self.assertEqual(filename, "田中一郎_講師週間時間割.xlsx")
        self.assertEqual(sheet["Q1"].value, "田中一郎")
        self.assertEqual(sheet["G3"].value, "14:20～15:40")
        self.assertEqual(sheet["W3"].value, "20:30～21:50")
        self.assertEqual(sheet["B4"].value, "月")
        self.assertEqual((sheet["H4"].value, sheet["I4"].value, sheet["J4"].value),
                         ("中2", "山田花子", "数学"))
        self.assertEqual((sheet["H5"].value, sheet["I5"].value, sheet["J5"].value),
                         ("中1", "鈴木太郎", "教科フォロー(文系)"))

    def test_classroom_export_matches_importable_column_layout(self):
        workbook = build_classroom_weekly_workbook(self.conn)
        sheet = workbook["時間割一覧"]

        self.assertEqual(sheet["B4"].value, "月")
        self.assertEqual((sheet["D4"].value, sheet["E4"].value, sheet["F4"].value, sheet["G4"].value),
                         ("田中", "c2 山田花子", "数学", None))
        self.assertEqual((sheet["D5"].value, sheet["E5"].value, sheet["F5"].value, sheet["G5"].value),
                         ("田中", "c1 鈴木太郎", "教科フォロー(文系)", None))
        self.assertEqual(sheet["B19"].value, "火")
        self.assertEqual(sheet.print_area, "'時間割一覧'!$B$1:$W$93")
        output = io.BytesIO()
        workbook.save(output)
        parsed = parse_timetable_snapshot(output.getvalue())
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0]["student_text"], "c2 山田花子")

    def test_classroom_export_rejects_more_than_fifteen_rows(self):
        for student_id in range(3, 17):
            self.conn.execute(
                """INSERT INTO STUDENTS
                   (student_id,last_name,first_name,last_name_kana,first_name_kana,
                    enrollment_year,base_grade,enrollment_status)
                   VALUES(?,?,?,?,?,?,?,'在籍')""",
                (student_id, f"姓{student_id}", "名", f"せい{student_id}", "めい", 2026, 7),
            )
            self.conn.execute(
                """INSERT INTO REGULAR_COURSE_ENROLLMENTS
                   (student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
                   VALUES(?,1,1,'月',1,'2026-04-01')""",
                (student_id,),
            )
        with self.assertRaises(WeeklyScheduleCapacityError) as caught:
            build_classroom_weekly_workbook(self.conn)
        self.assertIn("上限15件", str(caught.exception))

    def test_student_export_rejects_overlapping_regular_and_follow(self):
        self.conn.execute(
            """INSERT INTO FOLLOW_COURSE_ENROLLMENTS
               (student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
               VALUES(1,2,1,'月',1,'2026-04-01')"""
        )
        with self.assertRaises(WeeklyScheduleCapacityError):
            export_student_weekly_xlsx(self.conn, 1)

    def test_export_page_is_routed_and_visible_in_sidebar(self):
        self.assertIn("/weekly-schedule-export", app.ROUTES)
        basic_menu = dict(next(items for name, items in layout.MENU_GROUPS if name == "基本設定"))
        self.assertEqual(basic_menu["/weekly-schedule-export"], "通常授業 週間Excel出力")


if __name__ == "__main__":
    unittest.main()
