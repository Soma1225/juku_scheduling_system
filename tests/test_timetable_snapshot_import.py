from io import BytesIO
import sqlite3
import unittest

import openpyxl

from db import format_grade_label, get_grade_at_fiscal_year
import layout
import page_excel_import
from timetable_snapshot_import import (
    TimetableImportValidationError,
    import_timetable_snapshot,
    parse_student_text,
    parse_timetable_snapshot,
)


def build_timetable(*, invalid=False, unknown_instructor=False) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "時間割一覧"
    sheet["B2"] = "月"
    sheet["D3"] = "不明" if unknown_instructor else "田"
    sheet["E3"] = "x2 川口" if invalid else "c2 川口"
    sheet["F3"] = "数学"
    # 同じ1限の講師セルが結合相当で空欄でも、直前の略称を引き継ぐ。
    sheet["H3"] = "佐"
    sheet["I3"] = "高卒 山田"
    sheet["J3"] = "英語"
    sheet["I4"] = "c2 川口"
    sheet["J4"] = "英語"
    sheet["E5"] = "休校日"
    sheet["E17"] = "c1 範囲外"
    sheet["F17"] = "数学"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class TimetableSnapshotImportTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with open("schema.sql", encoding="utf-8") as schema_file:
            self.conn.executescript(schema_file.read())
        self.conn.executemany(
            "INSERT INTO PERIODS(period_number,start_time,end_time) VALUES (?,?,?)",
            [(number, "00:00", "00:00") for number in range(1, 6)],
        )
        self.conn.executemany(
            """INSERT INTO INSTRUCTORS
               (last_name,first_name,last_name_kana,first_name_kana,short_name,status)
               VALUES (?,?,?,?,?,?)""",
            [("田中", "太郎", "", "", "田", "在籍"),
             ("佐藤", "花子", "", "", "佐", "在籍")],
        )
        self.conn.executemany(
            """INSERT INTO SUBJECTS
               (course_category,grade_band,track,subject_group,subject_name)
               VALUES ('個別指導',?,?,?,?)""",
            [("中学生", None, "数学", "数学"),
             ("中学生", None, "英語", "英語"),
             ("高校生", None, "英語", "英語")],
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_student_parser_maps_all_grade_families_and_graduate(self):
        self.assertEqual(parse_student_text("s6 小学生")["base_grade"], 6)
        self.assertEqual(parse_student_text("c3 中学生")["base_grade"], 9)
        self.assertEqual(parse_student_text("k1 高校生")["base_grade"], 10)
        self.assertEqual(parse_student_text("高卒 山田")["base_grade"], 13)
        with self.assertRaises(ValueError):
            parse_student_text("中2 川口")
        with self.assertRaises(ValueError):
            parse_student_text("C2 川口")

    def test_parser_scans_fifteen_rows_skips_closed_day_and_fills_instructor(self):
        records = parse_timetable_snapshot(build_timetable())
        self.assertEqual(len(records), 3)
        self.assertEqual(records[2]["instructor_short_name"], "佐")
        self.assertNotIn("休校日", repr(records))
        self.assertNotIn("範囲外", repr(records))

    def test_import_creates_students_and_regular_enrollments(self):
        result = import_timetable_snapshot(
            self.conn, build_timetable(), effective_start_date="2026-04-01"
        )
        self.assertEqual(result, {
            "new_students": 2,
            "new_enrollments": 3,
            "skipped_enrollments": 0,
            "skipped_errors": [],
        })
        students = self.conn.execute(
            "SELECT last_name,first_name,last_name_kana,first_name_kana,base_grade FROM STUDENTS ORDER BY student_id"
        ).fetchall()
        self.assertEqual(students, [("川口", "", "", "", 8), ("山田", "", "", "", 13)])
        enrollments = self.conn.execute(
            """SELECT s.last_name,r.day_of_week,r.period_number,i.short_name,sub.subject_name
               FROM REGULAR_COURSE_ENROLLMENTS r
               JOIN STUDENTS s ON s.student_id=r.student_id
               JOIN INSTRUCTORS i ON i.instructor_id=r.instructor_id
               JOIN SUBJECTS sub ON sub.subject_id=r.subject_id
               ORDER BY r.enrollment_id"""
        ).fetchall()
        self.assertEqual(enrollments, [
            ("川口", "月", 1, "田", "数学"),
            ("山田", "月", 2, "佐", "英語"),
            ("川口", "月", 2, "佐", "英語"),
        ])

    def test_reimport_is_idempotent(self):
        snapshot = build_timetable()
        import_timetable_snapshot(self.conn, snapshot, effective_start_date="2026-04-01")
        result = import_timetable_snapshot(self.conn, snapshot, effective_start_date="2026-04-01")
        self.assertEqual(result["new_students"], 0)
        self.assertEqual(result["new_enrollments"], 0)
        self.assertEqual(result["skipped_enrollments"], 3)

    def test_existing_student_with_same_grade_and_name_is_reused(self):
        self.conn.execute(
            """INSERT INTO STUDENTS
               (last_name,first_name,last_name_kana,first_name_kana,
                enrollment_year,base_grade,enrollment_status)
               VALUES ('川口','','かわぐち','',2026,8,'在籍')"""
        )
        self.conn.commit()
        result = import_timetable_snapshot(
            self.conn, build_timetable(), effective_start_date="2026-04-01"
        )
        self.assertEqual(result["new_students"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM STUDENTS").fetchone()[0], 2)

    def test_empty_timetable_is_rejected(self):
        workbook = openpyxl.Workbook()
        workbook.active.title = "時間割一覧"
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        with self.assertRaisesRegex(TimetableImportValidationError, "1件も見つかりません"):
            import_timetable_snapshot(self.conn, output.getvalue())

    def test_invalid_grade_row_is_skipped_but_valid_rows_are_written(self):
        result = import_timetable_snapshot(
            self.conn,
            build_timetable(invalid=True),
            effective_start_date="2026-04-01",
        )
        self.assertEqual(result["new_students"], 2)
        self.assertEqual(result["new_enrollments"], 2)
        self.assertEqual(len(result["skipped_errors"]), 1)
        self.assertIn("判定できません", result["skipped_errors"][0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM STUDENTS").fetchone()[0], 2)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM REGULAR_COURSE_ENROLLMENTS").fetchone()[0], 2
        )

    def test_unmatched_instructor_row_is_skipped_but_valid_rows_are_written(self):
        result = import_timetable_snapshot(
            self.conn,
            build_timetable(unknown_instructor=True),
            effective_start_date="2026-04-01",
        )
        self.assertEqual(result["new_enrollments"], 2)
        self.assertEqual(len(result["skipped_errors"]), 1)
        self.assertIn("完全一致しません", result["skipped_errors"][0])

    def test_existing_schedule_conflict_skips_only_that_row(self):
        student_id = self.conn.execute(
            """INSERT INTO STUDENTS
               (last_name,first_name,last_name_kana,first_name_kana,
                enrollment_year,base_grade,enrollment_status)
               VALUES ('川口','','かわぐち','',2026,8,'在籍')"""
        ).lastrowid
        instructor_id = self.conn.execute(
            "SELECT instructor_id FROM INSTRUCTORS WHERE short_name='佐'"
        ).fetchone()[0]
        subject_id = self.conn.execute(
            "SELECT subject_id FROM SUBJECTS WHERE grade_band='中学生' AND subject_name='英語'"
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS
               (student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
               VALUES (?,?,?,?,?,?)""",
            (student_id, subject_id, instructor_id, "月", 1, "2026-03-01"),
        )
        self.conn.commit()

        result = import_timetable_snapshot(
            self.conn, build_timetable(), effective_start_date="2026-04-01"
        )
        self.assertEqual(result["new_enrollments"], 2)
        self.assertEqual(len(result["skipped_errors"]), 1)
        self.assertIn("既存の有効な通常授業", result["skipped_errors"][0])

    def test_graduate_grade_is_stable_and_displayed_as_graduate(self):
        self.assertEqual(get_grade_at_fiscal_year(2026, 13, 2031), 13)
        self.assertEqual(format_grade_label(13), "高卒生")

    def test_page_and_menu_use_new_import_purpose(self):
        rendered = page_excel_import.render({})
        self.assertIn("生徒・通常授業 Excel取り込み", rendered)
        self.assertIn("他の行は登録されます", rendered)
        self.assertNotIn("データは登録せず", rendered)
        self.assertIn("生徒・通常授業 Excel取り込み", repr(layout.MENU_GROUPS))

    def test_upload_handler_reports_result_counts(self):
        message, query = page_excel_import.handle_post(
            {
                "action": ["import"],
                "effective_start_date": ["2026-04-01"],
                "_files": {
                    "excel_file": {"filename": "info-copy.xlsx", "content": build_timetable()}
                },
            },
            self.conn,
        )
        self.assertIn("生徒の新規登録: 2名", message)
        self.assertIn("通常授業の登録: 3件", message)
        self.assertEqual(query, {"effective_start_date": ["2026-04-01"]})

    def test_upload_handler_reports_registered_and_error_counts_together(self):
        message, _ = page_excel_import.handle_post(
            {
                "action": ["import"],
                "effective_start_date": ["2026-04-01"],
                "_files": {
                    "excel_file": {
                        "filename": "info-copy.xlsx",
                        "content": build_timetable(invalid=True),
                    }
                },
            },
            self.conn,
        )
        self.assertIn("通常授業の登録: 2件", message)
        self.assertIn("1件はエラーのためスキップ", message)
        self.assertIn("判定できません", message)


if __name__ == "__main__":
    unittest.main()
