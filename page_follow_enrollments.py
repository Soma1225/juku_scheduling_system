# -*- coding: utf-8 -*-
"""教科フォロー契約を、登録可能枠だけ押せるグリッドから登録する。"""

from datetime import date
import html

from db import get_conn
from enrollment_calendar_grid import (
    build_enrollment_calendar_grid,
    evaluate_enrollment_slots,
    validate_enrollment_slot,
)
from page_camp_enrollments import grade_band_for_grade
from page_instructors import list_instructors


DAYS = ["月", "火", "水", "木", "金", "土", "日"]


def insert_follow_enrollment(
    conn,
    student_id,
    subject_id,
    instructor_id,
    day_of_week,
    period_number,
    effective_start_date,
) -> int:
    if day_of_week not in DAYS:
        raise ValueError(f"不正なday_of_weekです: {day_of_week}")
    try:
        date.fromisoformat(effective_start_date)
    except ValueError as exc:
        raise ValueError("effective_start_date は YYYY-MM-DD 形式で入力してください") from exc

    validate_enrollment_slot(
        conn,
        int(student_id),
        int(instructor_id),
        effective_start_date,
        day_of_week,
        int(period_number),
    )
    cur = conn.execute(
        """INSERT INTO FOLLOW_COURSE_ENROLLMENTS
           (student_id,subject_id,instructor_id,day_of_week,period_number,
            effective_start_date,effective_end_date)
           VALUES(?,?,?,?,?,?,NULL)""",
        (
            student_id,
            subject_id,
            instructor_id,
            day_of_week,
            period_number,
            effective_start_date,
        ),
    )
    conn.commit()
    return cur.lastrowid


def end_follow_enrollment(conn, follow_enrollment_id, end_date: str | None = None) -> None:
    end_date = end_date or date.today().isoformat()
    conn.execute(
        "UPDATE FOLLOW_COURSE_ENROLLMENTS SET effective_end_date=? WHERE follow_enrollment_id=?",
        (end_date, follow_enrollment_id),
    )
    conn.commit()


def _options(rows, selected="") -> str:
    return "".join(
        f'<option value="{html.escape(str(item_id))}"'
        f'{" selected" if str(item_id) == selected else ""}>'
        f'{html.escape(str(name))}</option>'
        for item_id, name in rows
    )


def render(qs: dict, message_html: str = "") -> str:
    student_id = qs.get("student_id", [""])[0]
    subject_id = qs.get("subject_id", [""])[0]
    instructor_id = qs.get("instructor_id", [""])[0]
    effective_start_date = qs.get("effective_start_date", [date.today().isoformat()])[0]

    conn = get_conn()
    students = conn.execute(
        """SELECT student_id,last_name||' '||first_name FROM STUDENTS
           WHERE enrollment_status='在籍'
           ORDER BY last_name_kana,first_name_kana"""
    ).fetchall()
    instructors = list_instructors(conn)
    subjects = []
    if student_id:
        student = conn.execute(
            "SELECT base_grade FROM STUDENTS WHERE student_id=?", (student_id,)
        ).fetchone()
        grade_band = grade_band_for_grade(student[0]) if student else None
        if grade_band:
            subjects = conn.execute(
                """SELECT subject_id,subject_name FROM SUBJECTS
                   WHERE subject_group='教科フォロー' AND grade_band=?
                   ORDER BY subject_name""",
                (grade_band,),
            ).fetchall()

    active_rows = conn.execute(
        """SELECT f.follow_enrollment_id,st.last_name||st.first_name,sub.subject_name,
                  i.last_name||i.first_name,f.day_of_week,f.period_number
           FROM FOLLOW_COURSE_ENROLLMENTS f
           JOIN STUDENTS st ON st.student_id=f.student_id
           JOIN SUBJECTS sub ON sub.subject_id=f.subject_id
           JOIN INSTRUCTORS i ON i.instructor_id=f.instructor_id
           WHERE f.effective_end_date IS NULL
           ORDER BY CASE f.day_of_week WHEN '月' THEN 1 WHEN '火' THEN 2 WHEN '水' THEN 3
                    WHEN '木' THEN 4 WHEN '金' THEN 5 WHEN '土' THEN 6 ELSE 7 END,
                    f.period_number"""
    ).fetchall()

    grid_html = ""
    if student_id and subject_id and instructor_id and effective_start_date:
        try:
            _term_id, term_name, decisions = evaluate_enrollment_slots(
                conn, int(student_id), int(instructor_id), effective_start_date
            )
            grid_html = (
                f'<div class="hint">対象学期：{html.escape(term_name)}。登録可能な枠だけ押せます。</div>'
                + build_enrollment_calendar_grid(
                    action_path="/follow-enrollments",
                    student_id=int(student_id),
                    subject_id=int(subject_id),
                    instructor_id=int(instructor_id),
                    effective_start_date=effective_start_date,
                    decisions=decisions,
                )
            )
        except ValueError as exc:
            grid_html = f'<div class="msg error">{html.escape(str(exc))}</div>'
    elif student_id:
        grid_html = '<div class="hint">科目・講師・適用開始日を選ぶと、登録可能な枠を表示します。</div>'

    subject_select = (
        f'<select name="subject_id" required onchange="this.form.submit()">'
        f'<option value="">選択してください</option>{_options(subjects, subject_id)}</select>'
        if student_id
        else '<select disabled><option>先に生徒を選択してください</option></select>'
    )
    selection_html = f"""
    <form method="GET" action="/follow-enrollments">
      <label>生徒 <span class="req">*</span></label>
      <select name="student_id" required onchange="this.form.submit()">
        <option value="">選択してください</option>{_options(students, student_id)}
      </select>
      <label>科目（教科フォロー） <span class="req">*</span></label>
      {subject_select}
      <label>講師 <span class="req">*</span></label>
      <select name="instructor_id" required onchange="this.form.submit()">
        <option value="">選択してください</option>{_options(instructors, instructor_id)}
      </select>
      <label>適用開始日 <span class="req">*</span></label>
      <input type="date" name="effective_start_date" value="{html.escape(effective_start_date)}"
             required onchange="this.form.submit()">
    </form>
    {grid_html}
    """

    rows_html = "".join(
        f"""<tr>
          <td>{html.escape(student_name)}</td><td>{html.escape(subject_name)}</td>
          <td>{html.escape(instructor_name)}</td><td>{html.escape(day)}曜{period}限</td>
          <td><form class="row-form" method="POST" action="/follow-enrollments">
            <input type="hidden" name="action" value="end">
            <input type="hidden" name="follow_enrollment_id" value="{fid}">
            <input type="hidden" name="student_id" value="{html.escape(student_id)}">
            <input type="hidden" name="subject_id" value="{html.escape(subject_id)}">
            <input type="hidden" name="instructor_id" value="{html.escape(instructor_id)}">
            <input type="hidden" name="effective_start_date" value="{html.escape(effective_start_date)}">
            <button class="btn-remove" type="submit">終了</button>
          </form></td>
        </tr>"""
        for fid, student_name, subject_name, instructor_name, day, period in active_rows
    ) or '<tr><td colspan="5" class="hint">現在有効な教科フォローはありません</td></tr>'
    conn.close()

    return f"""
    <h1>教科フォロー登録</h1>
    <div class="hint">生徒・科目・講師・開始日を選び、押せる曜日・限だけを登録できます。</div>
    {message_html}
    {selection_html}
    <h1 style="font-size:14px;margin-top:24px;">現在有効な教科フォロー（{len(active_rows)}件）</h1>
    <table><tr><th>生徒</th><th>科目</th><th>講師</th><th>曜日・限</th><th></th></tr>
      {rows_html}
    </table>
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")
    if action == "add":
        new_id = insert_follow_enrollment(
            conn,
            int(get("student_id")),
            int(get("subject_id")),
            int(get("instructor_id")),
            get("day_of_week"),
            int(get("period_number")),
            get("effective_start_date"),
        )
        message_html = (
            f'<div class="msg success">登録しました → follow_enrollment_id={new_id}</div>'
        )
    elif action == "end":
        end_follow_enrollment(conn, int(get("follow_enrollment_id")))
        message_html = '<div class="msg success">終了しました</div>'
    else:
        raise ValueError(f"不明な action です: {action}")

    return message_html, {
        "student_id": [get("student_id")],
        "subject_id": [get("subject_id")],
        "instructor_id": [get("instructor_id")],
        "effective_start_date": [get("effective_start_date", date.today().isoformat())],
    }
