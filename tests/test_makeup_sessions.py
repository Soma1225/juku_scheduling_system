import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
from core_migrations import MAKEUP_MIGRATION_ID, ensure_core_schema
from enrollment_calendar_grid import evaluate_makeup_slots
import layout
from page_home import _build_timetable_html, get_schedule_for_date
from page_makeup_sessions import create_makeup_session, list_unscheduled_makeups
import page_makeup_sessions


ROOT = Path(__file__).resolve().parents[1]


class MakeupSessionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        self.conn.executemany(
            "INSERT INTO PERIODS(period_number,start_time,end_time) VALUES(?,?,?)",
            [(period, "14:00", "15:00") for period in range(1, 6)],
        )
        self.conn.execute(
            "INSERT INTO TERMS(term_id,term_name,start_date,end_date) VALUES(1,'前期','2026-03-01','2026-08-31')"
        )
        self.conn.executemany(
            """INSERT INTO STUDENTS(
                   student_id,last_name,first_name,last_name_kana,first_name_kana,
                   enrollment_year,base_grade,enrollment_status)
               VALUES(?,?,?,?,?,?,?,'在籍')""",
            [
                (1, "山田", "花子", "やまだ", "はなこ", 2026, 8),
                (2, "鈴木", "太郎", "すずき", "たろう", 2026, 8),
                (3, "佐々木", "次郎", "ささき", "じろう", 2026, 8),
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
        self.conn.execute(
            """INSERT INTO SUBJECTS(
                   subject_id,course_category,grade_band,subject_group,subject_name)
               VALUES(1,'個別指導','中学生','数学','数学')"""
        )
        self.conn.execute(
            """INSERT INTO ATTENDANCE_RECORDS(
                   attendance_id,session_date,student_id,subject_id,instructor_id,period_number,status)
               VALUES(1,'2026-04-06',1,1,1,1,'欠席')"""
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_absence_is_unscheduled_until_makeup_is_created_and_reason_is_saved(self):
        pending = list_unscheduled_makeups(self.conn)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["student_name"], "山田花子")

        makeup_id = create_makeup_session(
            self.conn,
            attendance_id=1,
            makeup_date="2026-04-08",
            period_number=5,
            instructor_id=2,
            reason_category="生徒都合",
            reason_detail="発熱のため",
        )
        self.assertEqual(makeup_id, 1)
        self.assertEqual(list_unscheduled_makeups(self.conn), [])
        self.assertEqual(
            self.conn.execute(
                "SELECT reason_category,reason_detail FROM MAKEUP_SESSIONS WHERE makeup_id=1"
            ).fetchone(),
            ("生徒都合", "発熱のため"),
        )
        with self.assertRaisesRegex(ValueError, "既に振替先"):
            create_makeup_session(
                self.conn,
                attendance_id=1,
                makeup_date="2026-04-09",
                period_number=5,
                instructor_id=2,
                reason_category="生徒都合",
            )

    def test_makeup_grid_combines_regular_and_makeup_capacity_and_student_conflicts(self):
        # 水1限：田中講師の通常授業1件＋振替1件で1:2上限。
        self.conn.execute(
            """INSERT INTO REGULAR_COURSE_ENROLLMENTS(
                   student_id,subject_id,instructor_id,day_of_week,period_number,effective_start_date)
               VALUES(3,1,1,'水',1,'2026-03-01')"""
        )
        self.conn.execute(
            """INSERT INTO ATTENDANCE_RECORDS VALUES(
                   2,'2026-04-07',2,1,2,2,'欠席')"""
        )
        self.conn.execute(
            """INSERT INTO MAKEUP_SESSIONS(
                   attendance_id,makeup_date,period_number,instructor_id,reason_category)
               VALUES(2,'2026-04-08',1,1,'講師都合')"""
        )
        # 水2限：対象生徒自身に別の振替がある。
        self.conn.execute(
            """INSERT INTO ATTENDANCE_RECORDS VALUES(
                   3,'2026-04-01',1,1,2,3,'欠席')"""
        )
        self.conn.execute(
            """INSERT INTO MAKEUP_SESSIONS(
                   attendance_id,makeup_date,period_number,instructor_id,reason_category)
               VALUES(3,'2026-04-08',2,2,'生徒都合')"""
        )
        self.conn.execute(
            """INSERT INTO STUDENT_WEEKLY_AVAILABILITY(
                   student_id,term_id,day_of_week,period_number,is_available)
               VALUES(1,1,'水',3,0)"""
        )
        self.conn.execute(
            """INSERT INTO INSTRUCTOR_WEEKLY_AVAILABILITY(
                   instructor_id,term_id,day_of_week,period_number,is_available)
               VALUES(1,1,'水',4,0)"""
        )
        self.conn.commit()

        _term_id, _term_name, day, decisions = evaluate_makeup_slots(
            self.conn, 1, 1, "2026-04-08"
        )
        self.assertEqual(day, "水")
        self.assertIn("1:2の上限", decisions[("水", 1)].reason)
        self.assertEqual(decisions[("水", 2)].reason, "本人：別の振替あり")
        self.assertEqual(decisions[("水", 3)].reason, "本人：対応不可")
        self.assertIn("対応不可", decisions[("水", 4)].reason)
        self.assertFalse(decisions[("水", 5)].disabled)

    def test_makeup_appears_on_home_with_badge_and_attendance_control(self):
        create_makeup_session(
            self.conn,
            attendance_id=1,
            makeup_date="2026-04-08",
            period_number=5,
            instructor_id=2,
            reason_category="冠婚葬祭",
        )
        target = datetime.date(2026, 4, 8)
        records = get_schedule_for_date(self.conn, target)
        makeup = next(item for item in records if item.get("is_makeup"))
        self.assertEqual(makeup["student_name"], "山田花子")
        self.assertEqual(makeup["instructor_name"], "佐藤二郎")
        rendered = _build_timetable_html(self.conn, target, records)
        self.assertIn('class="makeup-badge"', rendered)
        self.assertIn("振替", rendered)
        self.assertIn('name="action" value="toggle_attendance"', rendered)

    def test_unscheduled_and_schedule_pages_render_disabled_period_buttons(self):
        self.conn.execute(
            """INSERT INTO STUDENT_WEEKLY_AVAILABILITY(
                   student_id,term_id,day_of_week,period_number,is_available)
               VALUES(1,1,'水',3,0)"""
        )
        self.conn.commit()
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = str(Path(tmp_dir) / "makeup.db")
            file_conn = sqlite3.connect(db_path)
            self.conn.backup(file_conn)
            file_conn.close()

            def open_db():
                return sqlite3.connect(db_path)

            with mock.patch.object(page_makeup_sessions, "get_conn", side_effect=open_db):
                unscheduled_html = page_makeup_sessions.render_unscheduled({})
                schedule_html = page_makeup_sessions.render_schedule(
                    {
                        "attendance_id": ["1"],
                        "instructor_id": ["1"],
                        "makeup_date": ["2026-04-08"],
                        "reason_category": ["生徒都合"],
                    }
                )
        self.assertIn("山田花子", unscheduled_html)
        self.assertIn("振替先を決める", unscheduled_html)
        self.assertIn("本人：対応不可", schedule_html)
        self.assertIn("3限", schedule_html)
        self.assertIn(" disabled", schedule_html)
        self.assertIn("makeup-reason-detail", schedule_html)

    def test_routes_menu_and_versioned_migration_exist(self):
        self.assertIn("/makeup-unscheduled", app.ROUTES)
        self.assertIn("/makeup-schedule", app.ROUTES)
        student_menu = dict(next(items for name, items in layout.MENU_GROUPS if name == "生徒情報"))
        self.assertEqual(student_menu["/makeup-unscheduled"], "未配置振替一覧")
        self.assertEqual(layout.ROUTE_TITLES["/makeup-schedule"], "振替先を決める")

        migrated = sqlite3.connect(":memory:")
        migrated.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        migrated.execute("DROP TABLE MAKEUP_SESSIONS")
        self.assertTrue(ensure_core_schema(migrated))
        self.assertIsNotNone(
            migrated.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='MAKEUP_SESSIONS'"
            ).fetchone()
        )
        self.assertIsNotNone(
            migrated.execute(
                "SELECT 1 FROM APP_SCHEMA_MIGRATIONS WHERE migration_id=?",
                (MAKEUP_MIGRATION_ID,),
            ).fetchone()
        )
        self.assertFalse(ensure_core_schema(migrated))
        migrated.close()


if __name__ == "__main__":
    unittest.main()
