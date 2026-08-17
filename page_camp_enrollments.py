# -*- coding: utf-8 -*-
"""page_camp_enrollments.py: 講習会の受講契約(CAMP_COURSE_ENROLLMENTS)を登録するページ"""

from db import get_conn
from page_camps import list_camps
from page_instructors import list_instructors


def insert_camp_enrollment(conn, camp_id, student_id, subject_id, contracted_count,
                            format_, assigned_instructor_id=None) -> int:
    if contracted_count <= 0:
        raise ValueError("contracted_count は1以上を指定してください")
    if format_ not in ("1:1", "1:2"):
        raise ValueError(f"不正なformatです: {format_}")
    cur = conn.execute(
        """INSERT INTO CAMP_COURSE_ENROLLMENTS
           (camp_id, student_id, subject_id, contracted_count, format, assigned_instructor_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (camp_id, student_id, subject_id, contracted_count, format_, assigned_instructor_id or None),
    )
    conn.commit()
    return cur.lastrowid


def render(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)
    students = conn.execute(
        "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    subjects = conn.execute(
        "SELECT subject_id, subject_group || '/' || subject_name || '（' || grade_band || '）' FROM SUBJECTS "
        "ORDER BY course_category, grade_band, subject_group"
    ).fetchall()
    instructors = list_instructors(conn)

    rows_html = ""
    if camp_id:
        rows = conn.execute(
            """SELECT e.enrollment_id, s.last_name || s.first_name, sub.subject_group || '/' || sub.subject_name,
                      e.contracted_count, e.format, i.last_name || i.first_name
               FROM CAMP_COURSE_ENROLLMENTS e
               JOIN STUDENTS s ON s.student_id = e.student_id
               JOIN SUBJECTS sub ON sub.subject_id = e.subject_id
               LEFT JOIN INSTRUCTORS i ON i.instructor_id = e.assigned_instructor_id
               WHERE e.camp_id = ? ORDER BY s.last_name_kana""",
            (camp_id,),
        ).fetchall()
        rows_html = "".join(
            f"<tr><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}コマ</td><td>{r[4]}</td><td>{r[5] or '-'}</td></tr>"
            for r in rows
        )
    conn.close()

    def options(rows, selected=""):
        return "".join(
            f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
        )

    table_html = ""
    if camp_id:
        table_html = f"""
        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">この講習会の契約一覧</h1>
        <table><tr><th>生徒</th><th>科目</th><th>コマ数</th><th>形式</th><th>指定講師</th></tr>{rows_html}</table>
        """

    return f"""
    <h1>講習会 受講契約登録</h1>
    <div class="hint">紙の申込用紙に書かれたコマ数をそのまま入力してください</div>
    {message_html}
    <label>講習会</label>
    <select id="camp_select" onchange="location.href='/camp-enrollments?camp_id='+this.value">
      <option value="">選択してください</option>
      {options(camps, camp_id)}
    </select>
    {f'''
    <form method="POST" action="/camp-enrollments">
      <input type="hidden" name="camp_id" value="{camp_id}">
      <label>生徒 <span class="req">*</span></label>
      <select name="student_id" required><option value="">選択してください</option>{options(students)}</select>
      <label>科目 <span class="req">*</span></label>
      <select name="subject_id" required><option value="">選択してください</option>{options(subjects)}</select>
      <label>契約コマ数 <span class="req">*</span></label>
      <input type="number" name="contracted_count" min="1" value="1" required>
      <label>形式 <span class="req">*</span></label>
      <select name="format" required>
        <option value="1:2" selected>1:2</option>
        <option value="1:1">1:1</option>
      </select>
      <label>指定講師(例外対応が必要な場合のみ)</label>
      <select name="assigned_instructor_id"><option value="">指定なし</option>{options(instructors)}</select>
      <button type="submit">登録する</button>
    </form>
    {table_html}
    ''' if camp_id else '<div class="hint">先に講習会を選択してください</div>'}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    camp_id = get("camp_id")
    new_id = insert_camp_enrollment(
        conn, int(camp_id), int(get("student_id")), int(get("subject_id")),
        int(get("contracted_count") or 0), get("format"),
        int(get("assigned_instructor_id")) if get("assigned_instructor_id") else None,
    )
    message_html = f'<div class="msg success">登録しました → enrollment_id={new_id}</div>'
    return message_html, {"camp_id": [camp_id]}
