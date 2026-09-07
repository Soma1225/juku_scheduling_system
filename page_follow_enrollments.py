# -*- coding: utf-8 -*-
"""
page_follow_enrollments.py

教科フォローの固定登録(FOLLOW_COURSE_ENROLLMENTS)を登録するページ。
通常授業(REGULAR_COURSE_ENROLLMENTS)とは別テーブルで管理するが、
同じ「世界線」の予定であるため、登録時に以下の衝突が無いことをチェックする:
  - その生徒が、同じ曜日・同じ限に、既に通常授業 or 別の教科フォローを持っていないか
  - その講師が、同じ曜日・同じ限に、既に通常授業 or 別の教科フォローを持っていないか
"""

from datetime import date
from db import get_conn
from page_instructors import list_instructors

DAYS = ["月", "火", "水", "木", "金", "土", "日"]


def _check_conflict(conn, person_type: str, person_id: int, day_of_week: str, period_number: int,
                     effective_start_date: str) -> str | None:
    """
    衝突があれば、その内容を説明する文字列を返す。無ければNone。
    person_type は 'student' または 'instructor'。
    """
    id_col = "student_id" if person_type == "student" else "instructor_id"
    label = "生徒" if person_type == "student" else "講師"

    for table in ("REGULAR_COURSE_ENROLLMENTS", "FOLLOW_COURSE_ENROLLMENTS"):
        row = conn.execute(
            f"""SELECT sub.subject_name FROM {table} e
                JOIN SUBJECTS sub ON sub.subject_id = e.subject_id
                WHERE e.{id_col} = ? AND e.day_of_week = ? AND e.period_number = ?
                  AND e.effective_start_date <= ?
                  AND (e.effective_end_date IS NULL OR e.effective_end_date > ?)""",
            (person_id, day_of_week, period_number, effective_start_date, effective_start_date),
        ).fetchone()
        if row:
            table_label = "通常授業" if table == "REGULAR_COURSE_ENROLLMENTS" else "教科フォロー"
            return f"この{label}は、同じ曜日・同じ限に既に{table_label}「{row[0]}」があります"
    return None


def insert_follow_enrollment(conn, student_id, subject_id, instructor_id, day_of_week,
                              period_number, effective_start_date) -> int:
    if day_of_week not in DAYS:
        raise ValueError(f"不正なday_of_weekです: {day_of_week}")
    try:
        date.fromisoformat(effective_start_date)
    except ValueError:
        raise ValueError("effective_start_date は YYYY-MM-DD 形式で入力してください")

    student_conflict = _check_conflict(conn, "student", student_id, day_of_week, period_number, effective_start_date)
    if student_conflict:
        raise ValueError(student_conflict)
    instructor_conflict = _check_conflict(conn, "instructor", instructor_id, day_of_week, period_number, effective_start_date)
    if instructor_conflict:
        raise ValueError(instructor_conflict)

    cur = conn.execute(
        """INSERT INTO FOLLOW_COURSE_ENROLLMENTS
           (student_id, subject_id, instructor_id, day_of_week, period_number, effective_start_date, effective_end_date)
           VALUES (?, ?, ?, ?, ?, ?, NULL)""",
        (student_id, subject_id, instructor_id, day_of_week, period_number, effective_start_date),
    )
    conn.commit()
    return cur.lastrowid


def end_follow_enrollment(conn, follow_enrollment_id, end_date: str | None = None) -> None:
    end_date = end_date or date.today().isoformat()
    conn.execute(
        "UPDATE FOLLOW_COURSE_ENROLLMENTS SET effective_end_date = ? WHERE follow_enrollment_id = ?",
        (end_date, follow_enrollment_id),
    )
    conn.commit()


# ---------------------------------------------------------
# 画面(GET)
# ---------------------------------------------------------

def render(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    students = conn.execute(
        "SELECT student_id, last_name || first_name FROM STUDENTS WHERE enrollment_status = '在籍' "
        "ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    instructors = list_instructors(conn)
    follow_subjects = conn.execute(
        "SELECT subject_id, grade_band, subject_name FROM SUBJECTS WHERE subject_group = '教科フォロー' "
        "ORDER BY grade_band, subject_name"
    ).fetchall()

    active_rows = conn.execute(
        """SELECT f.follow_enrollment_id, st.last_name || st.first_name, sub.subject_name,
                  i.last_name || i.first_name, f.day_of_week, f.period_number
           FROM FOLLOW_COURSE_ENROLLMENTS f
           JOIN STUDENTS st ON st.student_id = f.student_id
           JOIN SUBJECTS sub ON sub.subject_id = f.subject_id
           JOIN INSTRUCTORS i ON i.instructor_id = f.instructor_id
           WHERE f.effective_end_date IS NULL
           ORDER BY CASE f.day_of_week WHEN '月' THEN 1 WHEN '火' THEN 2 WHEN '水' THEN 3
                    WHEN '木' THEN 4 WHEN '金' THEN 5 WHEN '土' THEN 6 ELSE 7 END, f.period_number"""
    ).fetchall()
    conn.close()

    def options(rows):
        return "".join(f'<option value="{r[0]}">{r[1]}</option>' for r in rows)

    subject_options = "".join(f'<option value="{sid}">{gb}/{name}</option>' for sid, gb, name in follow_subjects)
    day_options = "".join(f'<option value="{d}">{d}</option>' for d in DAYS)
    period_options = "".join(f'<option value="{p}">{p}限</option>' for p in range(1, 6))

    rows_html = ""
    for fid, student_name, subject_name, instructor_name, day, period in active_rows:
        rows_html += f"""
        <tr>
          <td>{student_name}</td><td>{subject_name}</td><td>{instructor_name}</td>
          <td>{day}曜{period}限</td>
          <td>
            <form method="POST" action="/follow-enrollments" style="display:inline;">
              <input type="hidden" name="action" value="end">
              <input type="hidden" name="follow_enrollment_id" value="{fid}">
              <button type="submit" style="width:auto;padding:6px 12px;font-size:12px;">終了</button>
            </form>
          </td>
        </tr>
        """

    return f"""
    <h1>教科フォロー登録</h1>
    {message_html}
    <form method="POST" action="/follow-enrollments">
      <input type="hidden" name="action" value="add">
      <label>生徒 <span class="req">*</span></label>
      <select name="student_id" required><option value="">選択してください</option>{options(students)}</select>
      <label>科目(教科フォロー) <span class="req">*</span></label>
      <select name="subject_id" required><option value="">選択してください</option>{subject_options}</select>
      <label>講師 <span class="req">*</span></label>
      <select name="instructor_id" required><option value="">選択してください</option>{options(instructors)}</select>
      <label>曜日 <span class="req">*</span></label>
      <select name="day_of_week" required>{day_options}</select>
      <label>限 <span class="req">*</span></label>
      <select name="period_number" required>{period_options}</select>
      <label>適用開始日 <span class="req">*</span></label>
      <input type="date" name="effective_start_date" required>
      <button type="submit">登録する</button>
    </form>
    <h1 style="font-size:14px;margin-top:24px;">現在有効な教科フォロー ({len(active_rows)}件)</h1>
    <table>
      <tr><th>生徒</th><th>科目</th><th>講師</th><th>曜日・限</th><th></th></tr>
      {rows_html}
    </table>
    """


# ---------------------------------------------------------
# 送信処理(POST)
# ---------------------------------------------------------

def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")

    if action == "add":
        new_id = insert_follow_enrollment(
            conn, int(get("student_id")), int(get("subject_id")), int(get("instructor_id")),
            get("day_of_week"), int(get("period_number")), get("effective_start_date"),
        )
        message_html = f'<div class="msg success">登録しました → follow_enrollment_id={new_id}</div>'
    elif action == "end":
        end_follow_enrollment(conn, int(get("follow_enrollment_id")))
        message_html = '<div class="msg success">終了しました</div>'
    else:
        raise ValueError(f"不明な action です: {action}")

    return message_html, {}
