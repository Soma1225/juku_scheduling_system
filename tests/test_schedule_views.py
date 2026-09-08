import sqlite3
import unittest
from unittest.mock import patch

import page_schedule_view


SCHEMA = """
CREATE TABLE PERIODS(period_number INTEGER PRIMARY KEY,start_time TEXT,end_time TEXT);
CREATE TABLE INSTRUCTORS(
    instructor_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,
    last_name_kana TEXT,first_name_kana TEXT,status TEXT
);
CREATE TABLE STUDENTS(
    student_id INTEGER PRIMARY KEY,last_name TEXT,first_name TEXT,
    last_name_kana TEXT,first_name_kana TEXT,base_grade INTEGER,
    enrollment_year INTEGER,track TEXT
);
CREATE TABLE SUBJECTS(subject_id INTEGER PRIMARY KEY,subject_group TEXT,subject_name TEXT);
CREATE TABLE CAMPS(
    camp_id INTEGER PRIMARY KEY,camp_name TEXT,planned_start_date TEXT,planned_end_date TEXT
);
CREATE TABLE TIME_SLOTS(slot_id INTEGER PRIMARY KEY,session_date TEXT,period_number INTEGER);
CREATE TABLE SESSIONS(
    session_id INTEGER PRIMARY KEY,camp_id INTEGER,slot_id INTEGER,instructor_id INTEGER
);
CREATE TABLE ASSIGNMENTS(
    assignment_id INTEGER PRIMARY KEY,session_id INTEGER,student_id INTEGER,subject_id INTEGER
);
CREATE TABLE REGULAR_COURSE_ENROLLMENTS(
    enrollment_id INTEGER PRIMARY KEY,student_id INTEGER,subject_id INTEGER,
    instructor_id INTEGER,day_of_week TEXT,period_number INTEGER,
    effective_start_date TEXT,effective_end_date TEXT
);
CREATE TABLE FOLLOW_COURSE_ENROLLMENTS(
    follow_enrollment_id INTEGER PRIMARY KEY,student_id INTEGER,subject_id INTEGER,
    instructor_id INTEGER,day_of_week TEXT,period_number INTEGER,
    effective_start_date TEXT,effective_end_date TEXT
);
"""


def record(*, period, instructor_id=1, instructor_name="講師一郎",
           student_id=10, student_name="生徒花子", subject_name="数学",
           subject_group="数学"):
    return {
        "period": period,
        "instructor_id": instructor_id,
        "instructor_name": instructor_name,
        "student_id": student_id,
        "student_name": student_name,
        "base_grade": 7,
        "track": None,
        "subject_id": 100,
        "subject_name": subject_name,
        "subject_group": subject_group,
    }


class ScheduleViewTests(unittest.TestCase):
    def make_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO PERIODS VALUES(?,?,?)",
            [(p, f"{13 + p}:00", f"{14 + p}:20") for p in range(1, 6)],
        )
        conn.executemany(
            "INSERT INTO INSTRUCTORS VALUES(?,?,?,?,?,?)",
            [
                (1, "講師", "一郎", "こうし", "いちろう", "在籍"),
                (2, "別", "講師", "べつ", "こうし", "在籍"),
            ],
        )
        conn.executemany(
            "INSERT INTO STUDENTS VALUES(?,?,?,?,?,?,?,?)",
            [
                (10, "生徒", "花子", "せいと", "はなこ", 7, 2026, None),
                (20, "別", "生徒", "べつ", "せいと", 8, 2026, None),
            ],
        )
        return conn

    def test_instructor_view_uses_date_and_shows_regular_camp_and_follow_separately(self):
        conn = self.make_conn()
        combined = [
            record(period=1, student_name="通常生徒", subject_name="通常数学"),
            record(period=2, student_name="講習生徒", subject_name="講習英語", subject_group="英語"),
            record(period=3, instructor_id=2, student_name="他講師の生徒"),
        ]
        follow = [record(period=4, student_name="フォロー生徒", subject_name="教科フォロー", subject_group="教科フォロー")]
        with (
            patch.object(page_schedule_view, "get_conn", return_value=conn),
            patch.object(page_schedule_view, "get_schedule_for_date", return_value=combined) as get_schedule,
            patch.object(page_schedule_view, "get_follow_schedule_for_date", return_value=follow) as get_follow,
        ):
            result = page_schedule_view.render_instructor_view(
                {"date": ["2026-09-07"], "instructor_id": ["1"]}
            )
        self.assertIn("通常生徒", result)
        self.assertIn("講習生徒", result)
        self.assertNotIn("他講師の生徒", result)
        self.assertIn("教科フォロー", result)
        self.assertIn("フォロー生徒", result)
        self.assertIn("date=2026-09-06", result)
        self.assertIn("date=2026-09-08", result)
        self.assertNotIn("camp_id", result)
        get_schedule.assert_called_once()
        get_follow.assert_called_once()
        self.assertEqual(get_schedule.call_args.args[1].isoformat(), "2026-09-07")

    def test_student_view_filters_by_student_and_preserves_date_in_selector(self):
        conn = self.make_conn()
        combined = [
            record(period=1, instructor_name="通常講師"),
            record(period=2, instructor_name="講習講師", subject_name="英語", subject_group="英語"),
            record(period=3, student_id=20, instructor_name="別生徒の講師"),
        ]
        follow = [record(period=5, instructor_name="フォロー講師", subject_name="教科フォロー", subject_group="教科フォロー")]
        with (
            patch.object(page_schedule_view, "get_conn", return_value=conn),
            patch.object(page_schedule_view, "get_schedule_for_date", return_value=combined),
            patch.object(page_schedule_view, "get_follow_schedule_for_date", return_value=follow),
        ):
            result = page_schedule_view.render_student_view(
                {"date": ["2026-09-07"], "student_id": ["10"]}
            )
        self.assertIn("通常講師", result)
        self.assertIn("講習講師", result)
        self.assertIn("フォロー講師", result)
        self.assertNotIn("別生徒の講師", result)
        self.assertIn("/schedule-student?date=2026-09-07&amp;student_id", result.replace("&", "&amp;"))
        self.assertNotIn("camp_id", result)

    def test_real_home_queries_mix_regular_camp_and_follow_on_one_date(self):
        conn = self.make_conn()
        conn.executemany(
            "INSERT INTO SUBJECTS VALUES(?,?,?)",
            [
                (100, "数学", "通常数学"),
                (101, "英語", "講習英語"),
                (102, "教科フォロー", "教科フォロー"),
            ],
        )
        conn.execute(
            "INSERT INTO REGULAR_COURSE_ENROLLMENTS VALUES(1,10,100,1,'月',1,'2026-04-01',NULL)"
        )
        conn.execute(
            "INSERT INTO FOLLOW_COURSE_ENROLLMENTS VALUES(1,10,102,1,'月',3,'2026-04-01',NULL)"
        )
        conn.execute("INSERT INTO CAMPS VALUES(1,'夏期','2026-09-01','2026-09-30')")
        conn.execute("INSERT INTO TIME_SLOTS VALUES(1,'2026-09-07',2)")
        conn.execute("INSERT INTO SESSIONS VALUES(1,1,1,1)")
        conn.execute("INSERT INTO ASSIGNMENTS VALUES(1,1,10,101)")
        conn.commit()
        with patch.object(page_schedule_view, "get_conn", return_value=conn):
            result = page_schedule_view.render_instructor_view(
                {"date": ["2026-09-07"], "instructor_id": ["1"]}
            )
        self.assertIn("通常数学", result)
        self.assertIn("講習英語", result)
        self.assertIn("教科フォロー", result)

    def test_schedule_by_day_remains_camp_based(self):
        conn = self.make_conn()
        conn.execute("INSERT INTO SUBJECTS VALUES(100,'数学','数学')")
        conn.execute("INSERT INTO CAMPS VALUES(1,'夏期','2026-07-01','2026-08-31')")
        conn.execute("INSERT INTO TIME_SLOTS VALUES(1,'2026-07-20',1)")
        conn.execute("INSERT INTO SESSIONS VALUES(1,1,1,1)")
        conn.execute("INSERT INTO ASSIGNMENTS VALUES(1,1,10,100)")
        conn.commit()
        with patch.object(page_schedule_view, "get_conn", return_value=conn):
            result = page_schedule_view.render_by_day(
                {"camp_id": ["1"], "session_date": ["2026-07-20"]}
            )
        self.assertIn("講師一郎", result)
        self.assertIn("生徒花子", result)
        self.assertIn("camp_id=1", result)


if __name__ == "__main__":
    unittest.main()
