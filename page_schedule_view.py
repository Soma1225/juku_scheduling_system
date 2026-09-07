# -*- coding: utf-8 -*-
"""
page_schedule_view.py

スケジューラーが組んだ結果(SESSIONS/ASSIGNMENTS)を確認するための、
3種類の閲覧専用ページ。

1. 日付単位の授業スケジュール(/schedule-by-day): ある1日の、全セッション一覧
2. 講師視点の時間割(/schedule-instructor): 講師を選ぶと、その人の日付×限グリッド
3. 生徒視点の時間割(/schedule-student): 生徒を選ぶと、その人の日付×限グリッド
"""

import datetime
from db import get_conn
from page_camps import list_camps
from page_instructors import list_instructors

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]
PERIOD_NUMBERS = [1, 2, 3, 4, 5]


def _weekday_jp(date_str: str) -> str:
    y, m, d = map(int, date_str.split("-"))
    return WEEKDAY_JP[datetime.date(y, m, d).weekday()]


def _options(rows, selected=""):
    return "".join(
        f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
    )


def _get_period_labels(conn) -> dict[int, str]:
    numerals = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}
    rows = conn.execute("SELECT period_number, start_time, end_time FROM PERIODS ORDER BY period_number").fetchall()
    return {p: f"{numerals.get(p, p)}{s}〜{e}" for p, s, e in rows}


def _camp_dates(conn, camp_id: int) -> list[str]:
    return [
        row[0] for row in conn.execute(
            """SELECT DISTINCT ts.session_date FROM SESSIONS s
               JOIN TIME_SLOTS ts ON ts.slot_id = s.slot_id
               WHERE s.camp_id = ? ORDER BY ts.session_date""",
            (camp_id,),
        ).fetchall()
    ]


# ---------------------------------------------------------
# 1. 日付単位の授業スケジュール
# ---------------------------------------------------------

def render_by_day(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    session_date = qs.get("session_date", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)

    body_html = ""
    if camp_id:
        dates = _camp_dates(conn, int(camp_id))
        if not session_date and dates:
            session_date = dates[0]
        date_options = "".join(
            f'<option value="{d}"{" selected" if d == session_date else ""}>{d}（{_weekday_jp(d)}）</option>'
            for d in dates
        )

        rows_html = ""
        if session_date:
            period_labels = _get_period_labels(conn)
            sessions = conn.execute(
                """SELECT s.session_id, ts.period_number, i.last_name || i.first_name AS instructor_name
                   FROM SESSIONS s
                   JOIN TIME_SLOTS ts ON ts.slot_id = s.slot_id
                   JOIN INSTRUCTORS i ON i.instructor_id = s.instructor_id
                   WHERE s.camp_id = ? AND ts.session_date = ?
                   ORDER BY ts.period_number, i.last_name_kana""",
                (camp_id, session_date),
            ).fetchall()

            for session_id, period_number, instructor_name in sessions:
                members = conn.execute(
                    """SELECT st.last_name || st.first_name, sub.subject_group || '/' || sub.subject_name
                       FROM ASSIGNMENTS a
                       JOIN STUDENTS st ON st.student_id = a.student_id
                       JOIN SUBJECTS sub ON sub.subject_id = a.subject_id
                       WHERE a.session_id = ?""",
                    (session_id,),
                ).fetchall()
                member_text = "、".join(f"{name}（{subj}）" for name, subj in members)
                rows_html += (
                    f"<tr><td>{period_labels.get(period_number, f'{period_number}限')}</td>"
                    f"<td>{instructor_name}</td><td>{member_text}</td></tr>"
                )

            table_html = (
                f"<table><tr><th>限</th><th>講師</th><th>生徒（科目）</th></tr>{rows_html}</table>"
                if rows_html else '<div class="hint">この日はセッションがありません</div>'
            )
        else:
            table_html = '<div class="hint">この講習会にはまだ時間割が組まれていません</div>'

        body_html = f"""
        <label>日付</label>
        <select onchange="location.href='/schedule-by-day?camp_id={camp_id}&session_date='+this.value">
          {date_options}
        </select>
        {table_html}
        """

    conn.close()
    return f"""
    <h1>日付単位の授業スケジュール</h1>
    <div class="hint">講習会を選ぶと、日付ごとの全セッション一覧を確認できます</div>
    {message_html}
    <label>講習会</label>
    <select onchange="location.href='/schedule-by-day?camp_id='+this.value">
      <option value="">選択してください</option>{_options(camps, camp_id)}
    </select>
    {body_html if camp_id else '<div class="hint">先に講習会を選択してください</div>'}
    """


# ---------------------------------------------------------
# 共通: 講師/生徒の時間割グリッド組み立て
# ---------------------------------------------------------

def _build_timetable_grid(conn, camp_id: int, role: str, entity_id: int) -> str:
    """role='instructor' または 'student' の、日付×限グリッドを組み立てる。"""
    dates = _camp_dates(conn, camp_id)
    if not dates:
        return '<div class="hint">この講習会にはまだ時間割が組まれていません</div>'

    period_labels = _get_period_labels(conn)

    if role == "instructor":
        cell_rows = conn.execute(
            """SELECT ts.session_date, ts.period_number, s.session_id
               FROM SESSIONS s JOIN TIME_SLOTS ts ON ts.slot_id = s.slot_id
               WHERE s.camp_id = ? AND s.instructor_id = ?""",
            (camp_id, entity_id),
        ).fetchall()
    else:
        cell_rows = conn.execute(
            """SELECT ts.session_date, ts.period_number, s.session_id
               FROM ASSIGNMENTS a
               JOIN SESSIONS s ON s.session_id = a.session_id
               JOIN TIME_SLOTS ts ON ts.slot_id = s.slot_id
               WHERE s.camp_id = ? AND a.student_id = ?""",
            (camp_id, entity_id),
        ).fetchall()

    cell_content: dict[tuple[str, int], list[str]] = {}
    for session_date, period_number, session_id in cell_rows:
        if role == "instructor":
            members = conn.execute(
                """SELECT st.last_name || st.first_name, sub.subject_group || '/' || sub.subject_name
                   FROM ASSIGNMENTS a JOIN STUDENTS st ON st.student_id = a.student_id
                   JOIN SUBJECTS sub ON sub.subject_id = a.subject_id WHERE a.session_id = ?""",
                (session_id,),
            ).fetchall()
            text = "<br>".join(f"{name}<br><small>{subj}</small>" for name, subj in members)
        else:
            row = conn.execute(
                """SELECT sub.subject_group || '/' || sub.subject_name, i.last_name || i.first_name
                   FROM ASSIGNMENTS a JOIN SUBJECTS sub ON sub.subject_id = a.subject_id
                   JOIN SESSIONS s ON s.session_id = a.session_id
                   JOIN INSTRUCTORS i ON i.instructor_id = s.instructor_id
                   WHERE a.session_id = ? AND a.student_id = ?""",
                (session_id, entity_id),
            ).fetchone()
            text = f"{row[0]}<br><small>{row[1]}</small>" if row else ""
        cell_content[(session_date, period_number)] = text

    day_headers = "".join(f"<th>{d[5:]}<br>（{_weekday_jp(d)}）</th>" for d in dates)
    body_rows = ""
    for p in PERIOD_NUMBERS:
        cells = ""
        for d in dates:
            content = cell_content.get((d, p), "")
            cells += f"<td>{content}</td>"
        body_rows += f'<tr><td class="period-label">{period_labels.get(p, f"{p}限")}</td>{cells}</tr>'

    return f"""
    <div style="overflow-x:auto;">
      <table class="grid"><tr><th>限＼日付</th>{day_headers}</tr>{body_rows}</table>
    </div>
    """


# ---------------------------------------------------------
# 2. 講師視点の時間割
# ---------------------------------------------------------

def render_instructor_view(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    instructor_id = qs.get("instructor_id", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)
    instructors = list_instructors(conn)

    grid_html = ""
    if camp_id and instructor_id:
        grid_html = _build_timetable_grid(conn, int(camp_id), "instructor", int(instructor_id))
    conn.close()

    return f"""
    <h1>講師視点の時間割</h1>
    <div class="hint">講習会と講師を選ぶと、その講師の時間割を確認できます</div>
    {message_html}
    <label>講習会</label>
    <select onchange="location.href='/schedule-instructor?camp_id='+this.value+'&instructor_id={instructor_id}'">
      <option value="">選択してください</option>{_options(camps, camp_id)}
    </select>
    <label>講師</label>
    <select onchange="location.href='/schedule-instructor?camp_id={camp_id}&instructor_id='+this.value">
      <option value="">選択してください</option>{_options(instructors, instructor_id)}
    </select>
    {grid_html if (camp_id and instructor_id) else '<div class="hint">講習会と講師を両方選択してください</div>'}
    """


# ---------------------------------------------------------
# 3. 生徒視点の時間割
# ---------------------------------------------------------

def render_student_view(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    student_id = qs.get("student_id", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)
    students = conn.execute(
        "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()

    grid_html = ""
    if camp_id and student_id:
        grid_html = _build_timetable_grid(conn, int(camp_id), "student", int(student_id))
    conn.close()

    return f"""
    <h1>生徒視点の時間割</h1>
    <div class="hint">講習会と生徒を選ぶと、その生徒の時間割を確認できます</div>
    {message_html}
    <label>講習会</label>
    <select onchange="location.href='/schedule-student?camp_id='+this.value+'&student_id={student_id}'">
      <option value="">選択してください</option>{_options(camps, camp_id)}
    </select>
    <label>生徒</label>
    <select onchange="location.href='/schedule-student?camp_id={camp_id}&student_id='+this.value">
      <option value="">選択してください</option>{_options(students, student_id)}
    </select>
    {grid_html if (camp_id and student_id) else '<div class="hint">講習会と生徒を両方選択してください</div>'}
    """
