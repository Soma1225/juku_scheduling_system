# -*- coding: utf-8 -*-
"""
page_regular_enrollments.py

通常授業の固定契約(REGULAR_COURSE_ENROLLMENTS)を登録するページ。
履歴を残す設計(effective_end_dateがNULL=現在有効)のため、
「終了する」ボタンは物理削除ではなく、effective_end_dateに今日の日付を入れるだけ。
"""

from datetime import date
from db import get_conn
from page_instructors import list_instructors
from page_camp_enrollments import grade_band_for_grade

DAYS = ["月", "火", "水", "木", "金", "土", "日"]


def insert_regular_enrollment(conn, student_id, subject_id, instructor_id, day_of_week,
                               period_number, effective_start_date) -> int:
    if day_of_week not in DAYS:
        raise ValueError(f"不正なday_of_weekです: {day_of_week}")
    try:
        date.fromisoformat(effective_start_date)
    except ValueError:
        raise ValueError("effective_start_date は YYYY-MM-DD 形式で入力してください")

    cur = conn.execute(
        """INSERT INTO REGULAR_COURSE_ENROLLMENTS
           (student_id, subject_id, instructor_id, day_of_week, period_number, effective_start_date, effective_end_date)
           VALUES (?, ?, ?, ?, ?, ?, NULL)""",
        (student_id, subject_id, instructor_id, day_of_week, period_number, effective_start_date),
    )
    conn.commit()
    return cur.lastrowid


def end_regular_enrollment(conn, enrollment_id, end_date: str | None = None) -> None:
    end_date = end_date or date.today().isoformat()
    conn.execute(
        "UPDATE REGULAR_COURSE_ENROLLMENTS SET effective_end_date = ? WHERE enrollment_id = ?",
        (end_date, enrollment_id),
    )
    conn.commit()


def list_active_enrollments_for_student(conn, student_id) -> list[dict]:
    """他ページ(生徒詳細)からも参照される、共有の一覧取得関数。"""
    cur = conn.execute(
        """SELECT e.enrollment_id, sub.subject_group, sub.subject_name,
                  i.last_name || i.first_name AS instructor_name, e.day_of_week, e.period_number, e.effective_start_date
           FROM REGULAR_COURSE_ENROLLMENTS e
           JOIN SUBJECTS sub ON sub.subject_id = e.subject_id
           JOIN INSTRUCTORS i ON i.instructor_id = e.instructor_id
           WHERE e.student_id = ? AND e.effective_end_date IS NULL
           ORDER BY CASE e.day_of_week WHEN '月' THEN 1 WHEN '火' THEN 2 WHEN '水' THEN 3
                    WHEN '木' THEN 4 WHEN '金' THEN 5 WHEN '土' THEN 6 ELSE 7 END, e.period_number""",
        (student_id,),
    )
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def render(qs: dict, message_html: str = "") -> str:
    student_id = qs.get("student_id", [""])[0]
    conn = get_conn()
    students = conn.execute(
        "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    instructors = list_instructors(conn)
    periods = conn.execute("SELECT period_number, start_time, end_time FROM PERIODS ORDER BY period_number").fetchall()

    grade_band = None
    subjects = []
    active_rows = []
    if student_id:
        row = conn.execute("SELECT base_grade FROM STUDENTS WHERE student_id = ?", (student_id,)).fetchone()
        grade_band = grade_band_for_grade(row[0]) if row else None
        if grade_band:
            subjects = conn.execute(
                "SELECT subject_id, subject_group || '/' || subject_name FROM SUBJECTS "
                "WHERE grade_band = ? ORDER BY course_category, subject_group",
                (grade_band,),
            ).fetchall()
        active_rows = list_active_enrollments_for_student(conn, int(student_id))
    conn.close()

    def options(rows, selected=""):
        return "".join(
            f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
        )

    day_options = "".join(f'<option value="{d}">{d}</option>' for d in DAYS if d != "日")
    period_options = "".join(
        f'<option value="{p}">{p}限（{s}〜{e}）</option>' for p, s, e in periods
    )

    subject_select = (
        f'<select name="subject_id" required><option value="">選択してください</option>{options(subjects)}</select>'
        if grade_band else
        '<select disabled><option>先に生徒を選択してください</option></select>'
    )

    active_table = ""
    if student_id:
        if active_rows:
            rows_html = "".join(
                f"""<tr>
                    <td>{r['subject_group']}/{r['subject_name']}</td>
                    <td>{r['instructor_name']}</td>
                    <td>{r['day_of_week']}{r['period_number']}限</td>
                    <td>{r['effective_start_date']}〜</td>
                    <td>
                      <form class="row-form" method="POST" action="/regular-enrollments">
                        <input type="hidden" name="action" value="end">
                        <input type="hidden" name="student_id" value="{student_id}">
                        <input type="hidden" name="enrollment_id" value="{r['enrollment_id']}">
                        <button class="btn-remove" type="submit">終了する</button>
                      </form>
                    </td>
                </tr>"""
                for r in active_rows
            )
        else:
            rows_html = '<tr><td colspan="5" class="hint">現在有効な契約はありません</td></tr>'
        active_table = f"""
        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">現在の契約一覧</h1>
        <table><tr><th>科目</th><th>講師</th><th>曜日・限</th><th>開始日</th><th></th></tr>{rows_html}</table>
        """

    form_html = f"""
    <form method="POST" action="/regular-enrollments">
      <input type="hidden" name="action" value="add">
      <input type="hidden" name="student_id" value="{student_id}">
      <label>科目 <span class="req">*</span></label>
      {subject_select}
      <label>担当講師 <span class="req">*</span></label>
      <select name="instructor_id" required><option value="">選択してください</option>{options(instructors)}</select>
      <label>曜日 <span class="req">*</span></label>
      <select name="day_of_week" required>{day_options}</select>
      <label>限 <span class="req">*</span></label>
      <select name="period_number" required>{period_options}</select>
      <label>契約開始日 <span class="req">*</span></label>
      <input type="date" name="effective_start_date" required>
      <button type="submit">登録する</button>
    </form>
    """

    return f"""
    <h1>通常授業 契約登録</h1>
    <div class="hint">曜日・限固定の通常授業契約。終了した契約は削除せず「終了する」で日付を記録します</div>
    {message_html}
    <label>生徒</label>
    <select onchange="location.href='/regular-enrollments?student_id='+this.value">
      <option value="">選択してください</option>{options(students, student_id)}
    </select>
    {form_html if student_id else '<div class="hint">先に生徒を選択してください</div>'}
    {active_table}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")
    student_id = get("student_id")

    if action == "add":
        subject_id = int(get("subject_id"))
        instructor_id = int(get("instructor_id"))
        new_id = insert_regular_enrollment(
            conn, int(student_id), subject_id, instructor_id,
            get("day_of_week"), int(get("period_number")), get("effective_start_date"),
        )
        warning_html = ""
        from db import check_instructor_teaches_subject
        if not check_instructor_teaches_subject(conn, instructor_id, subject_id):
            instructor_name = conn.execute(
                "SELECT last_name || first_name FROM INSTRUCTORS WHERE instructor_id = ?", (instructor_id,)
            ).fetchone()
            warning_html = (
                f'<div class="msg error">⚠️ 警告: {instructor_name[0] if instructor_name else "選択した講師"} は、'
                f'この科目を担当科目として登録していません。選択に誤りがないか確認してください'
                f'（登録自体はそのまま完了しています）</div>'
            )
        message_html = f'<div class="msg success">登録しました → enrollment_id={new_id}</div>{warning_html}'
    elif action == "end":
        end_regular_enrollment(conn, int(get("enrollment_id")))
        message_html = '<div class="msg success">契約を終了しました</div>'
    else:
        raise ValueError(f"不明な action です: {action}")

    return message_html, {"student_id": [student_id]}
