# -*- coding: utf-8 -*-
"""page_camp_enrollments.py: 講習会の受講科目回数登録(CAMP_COURSE_ENROLLMENTS)を登録するページ"""

from datetime import date
from db import get_conn, DEFAULT_MAX_SESSIONS_PER_DAY
from page_camps import list_camps
from page_instructors import list_instructors


def get_max_sessions_per_day(conn, camp_id, student_id) -> tuple[int, bool]:
    """
    戻り値: (実際に適用される上限値, 個別に上書きされているか)
    個別設定が無ければ、塾全体の基本値を返す。
    """
    row = conn.execute(
        "SELECT max_sessions_per_day FROM CAMP_STUDENT_MAX_SESSIONS WHERE camp_id = ? AND student_id = ?",
        (camp_id, student_id),
    ).fetchone()
    if row:
        return row[0], True
    return DEFAULT_MAX_SESSIONS_PER_DAY, False


def set_max_sessions_per_day(conn, camp_id, student_id, value: int | None) -> None:
    """valueがNoneなら個別設定を解除し、基本値に戻す。"""
    conn.execute(
        "DELETE FROM CAMP_STUDENT_MAX_SESSIONS WHERE camp_id = ? AND student_id = ?",
        (camp_id, student_id),
    )
    if value is not None:
        if not (1 <= value <= 5):
            raise ValueError("1日の上限コマ数は1〜5で指定してください")
        conn.execute(
            "INSERT INTO CAMP_STUDENT_MAX_SESSIONS (camp_id, student_id, max_sessions_per_day) VALUES (?, ?, ?)",
            (camp_id, student_id, value),
        )
    conn.commit()


def insert_camp_enrollment(conn, camp_id, student_id, subject_id, contracted_count,
                            format_, assigned_instructor_id=None, enrollment_end_date=None) -> int:
    if contracted_count <= 0:
        raise ValueError("contracted_count は1以上を指定してください")
    if format_ not in ("1:1", "1:2"):
        raise ValueError(f"不正なformatです: {format_}")
    if enrollment_end_date:
        try:
            date.fromisoformat(enrollment_end_date)
        except ValueError:
            raise ValueError("受講期限は YYYY-MM-DD 形式で入力してください")

    cur = conn.execute(
        """INSERT INTO CAMP_COURSE_ENROLLMENTS
           (camp_id, student_id, subject_id, contracted_count, format, assigned_instructor_id, enrollment_end_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (camp_id, student_id, subject_id, contracted_count, format_,
         assigned_instructor_id or None, enrollment_end_date or None),
    )
    conn.commit()
    return cur.lastrowid


def grade_band_for_grade(base_grade: int) -> str | None:
    """base_grade(1〜12の整数)から、SUBJECTS.grade_bandに対応する区分を返す。"""
    if base_grade is None:
        return None
    if 1 <= base_grade <= 3:
        return "小学生低学年"
    if 4 <= base_grade <= 6:
        return "小学生高学年"
    if 7 <= base_grade <= 9:
        return "中学生"
    if 10 <= base_grade <= 12:
        return "高校生"
    return None


def _get_student_grade_band(conn, student_id: str) -> str | None:
    if not student_id:
        return None
    row = conn.execute("SELECT base_grade FROM STUDENTS WHERE student_id = ?", (student_id,)).fetchone()
    return grade_band_for_grade(row[0]) if row else None


def render(qs: dict, message_html: str = "") -> str:
    camp_id = qs.get("camp_id", [""])[0]
    student_id = qs.get("student_id", [""])[0]
    conn = get_conn()
    camps = list_camps(conn)
    students = conn.execute(
        "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    instructors = list_instructors(conn)

    camp_end_date = None
    if camp_id:
        row = conn.execute("SELECT planned_end_date FROM CAMPS WHERE camp_id = ?", (camp_id,)).fetchone()
        camp_end_date = row[0] if row else None

    max_sessions_html = ""
    if camp_id and student_id:
        current_max, is_override = get_max_sessions_per_day(conn, int(camp_id), int(student_id))
        max_sessions_html = f"""
        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">1日の上限コマ数(この生徒・この講習会のみ)</h1>
        <div class="hint">{'個別に上書き中' if is_override else f'塾全体の基本値({DEFAULT_MAX_SESSIONS_PER_DAY}コマ)を使用中'}</div>
        <form method="POST" action="/camp-enrollments" class="row-form" style="margin-top:8px;">
          <input type="hidden" name="action" value="set_max_sessions">
          <input type="hidden" name="camp_id" value="{camp_id}">
          <input type="hidden" name="student_id" value="{student_id}">
          <select name="max_sessions_per_day" style="width:120px;">
            <option value="">基本値を使う</option>
            {"".join(f'<option value="{n}"{" selected" if is_override and n == current_max else ""}>{n}コマまで</option>' for n in range(1, 6))}
          </select>
          <button type="submit">保存</button>
        </form>
        """

    # 生徒が選ばれていれば、その学年に対応する科目だけに絞る
    grade_band = _get_student_grade_band(conn, student_id)
    if grade_band:
        subjects = conn.execute(
            "SELECT subject_id, subject_group || '/' || subject_name FROM SUBJECTS "
            "WHERE grade_band = ? ORDER BY course_category, subject_group",
            (grade_band,),
        ).fetchall()
    else:
        subjects = []

    rows_html = ""
    if camp_id:
        rows = conn.execute(
            """SELECT e.enrollment_id, s.last_name || s.first_name, sub.subject_group || '/' || sub.subject_name,
                      e.contracted_count, e.format, i.last_name || i.first_name, e.enrollment_end_date
               FROM CAMP_COURSE_ENROLLMENTS e
               JOIN STUDENTS s ON s.student_id = e.student_id
               JOIN SUBJECTS sub ON sub.subject_id = e.subject_id
               LEFT JOIN INSTRUCTORS i ON i.instructor_id = e.assigned_instructor_id
               WHERE e.camp_id = ? ORDER BY s.last_name_kana""",
            (camp_id,),
        ).fetchall()
        rows_html = "".join(
            f"<tr><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}コマ</td><td>{r[4]}</td><td>{r[5] or '-'}</td>"
            f"<td>{r[6] or '講習会終了日まで'}</td></tr>"
            for r in rows
        )
    conn.close()

    def options(rows, selected=""):
        return "".join(
            f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
        )

    subject_select = (
        f'<select name="subject_id" required><option value="">選択してください</option>{options(subjects)}</select>'
        if grade_band else
        '<select disabled><option>先に生徒を選択してください</option></select>'
    )

    form_html = f"""
    <form method="POST" action="/camp-enrollments">
      <input type="hidden" name="action" value="add">
      <input type="hidden" name="camp_id" value="{camp_id}">
      <label>生徒 <span class="req">*</span></label>
      <select name="student_id" required onchange="location.href='/camp-enrollments?camp_id={camp_id}&student_id='+this.value">
        <option value="">選択してください</option>{options(students, student_id)}
      </select>
      <label>科目 <span class="req">*</span></label>
      {subject_select}
      <label>契約コマ数 <span class="req">*</span></label>
      <input type="number" name="contracted_count" min="1" value="1" required>
      <label>形式 <span class="req">*</span></label>
      <select name="format" required>
        <option value="1:2" selected>1:2</option>
        <option value="1:1">1:1</option>
      </select>
      <label>指定講師(例外対応が必要な場合のみ)</label>
      <select name="assigned_instructor_id"><option value="">指定なし</option>{options(instructors)}</select>
      <label>受講期限(任意。空欄なら講習会終了日「{camp_end_date}」まで)</label>
      <input type="date" name="enrollment_end_date">
      <div class="hint" style="margin-top:4px;">共通テストのみで使う科目、私立入試前に退塾予定の生徒などは、それより前の日付を指定してください</div>
      <button type="submit">登録する</button>
    </form>
    """

    table_html = ""
    if camp_id:
        table_html = f"""
        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">この講習会の契約一覧</h1>
        <table><tr><th>生徒</th><th>科目</th><th>コマ数</th><th>形式</th><th>指定講師</th><th>受講期限</th></tr>{rows_html}</table>
        """

    return f"""
    <h1>講習会 受講科目回数登録</h1>
    <div class="hint">紙の申込用紙に書かれたコマ数をそのまま入力してください。生徒を選ぶと、その学年に対応する科目だけ選べます</div>
    {message_html}
    <label>講習会</label>
    <select onchange="location.href='/camp-enrollments?camp_id='+this.value">
      <option value="">選択してください</option>
      {options(camps, camp_id)}
    </select>
    {form_html if camp_id else '<div class="hint">先に講習会を選択してください</div>'}
    {max_sessions_html}
    {table_html}
    """


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action", "add")
    camp_id = get("camp_id")

    if action == "set_max_sessions":
        student_id = get("student_id")
        value = get("max_sessions_per_day")
        set_max_sessions_per_day(conn, int(camp_id), int(student_id), int(value) if value else None)
        message_html = '<div class="msg success">1日の上限コマ数を保存しました</div>'
        return message_html, {"camp_id": [camp_id], "student_id": [student_id]}

    subject_id = int(get("subject_id"))
    assigned_instructor_id = int(get("assigned_instructor_id")) if get("assigned_instructor_id") else None
    new_id = insert_camp_enrollment(
        conn, int(camp_id), int(get("student_id")), subject_id,
        int(get("contracted_count") or 0), get("format"),
        assigned_instructor_id,
        get("enrollment_end_date") or None,
    )

    warning_html = ""
    if assigned_instructor_id is not None:
        from db import check_instructor_teaches_subject
        if not check_instructor_teaches_subject(conn, assigned_instructor_id, subject_id):
            instructor_name = conn.execute(
                "SELECT last_name || first_name FROM INSTRUCTORS WHERE instructor_id = ?", (assigned_instructor_id,)
            ).fetchone()
            warning_html = (
                f'<div class="msg error">⚠️ 警告: {instructor_name[0] if instructor_name else "選択した講師"} は、'
                f'この科目を担当科目として登録していません。選択に誤りがないか確認してください'
                f'（登録自体はそのまま完了しています）</div>'
            )

    message_html = f'<div class="msg success">登録しました → enrollment_id={new_id}</div>{warning_html}'
    return message_html, {"camp_id": [camp_id]}
