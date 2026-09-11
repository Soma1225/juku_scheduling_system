"""欠席から生じた未配置振替の一覧と、振替先を決める画面。"""

from __future__ import annotations

from datetime import date
import html
import sqlite3

from db import get_conn
from enrollment_calendar_grid import build_single_day_period_grid, evaluate_makeup_slots
from page_instructors import list_instructors


REASON_CATEGORIES = ("講師都合", "生徒都合", "冠婚葬祭")


def list_unscheduled_makeups(conn) -> list[dict]:
    cursor = conn.execute(
        """SELECT a.attendance_id,a.session_date,a.period_number,
                  st.last_name||st.first_name AS student_name,
                  sub.subject_name,
                  i.last_name||i.first_name AS instructor_name
           FROM ATTENDANCE_RECORDS a
           JOIN STUDENTS st ON st.student_id=a.student_id
           JOIN SUBJECTS sub ON sub.subject_id=a.subject_id
           JOIN INSTRUCTORS i ON i.instructor_id=a.instructor_id
           LEFT JOIN MAKEUP_SESSIONS m ON m.attendance_id=a.attendance_id
           WHERE a.status='欠席' AND m.makeup_id IS NULL
           ORDER BY a.session_date,a.period_number,st.last_name_kana,st.first_name_kana"""
    )
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _get_absence(conn, attendance_id: int) -> dict | None:
    cursor = conn.execute(
        """SELECT a.attendance_id,a.session_date,a.period_number,a.student_id,
                  a.subject_id,a.instructor_id,a.status,
                  st.last_name||st.first_name AS student_name,
                  sub.subject_name,
                  i.last_name||i.first_name AS original_instructor_name
           FROM ATTENDANCE_RECORDS a
           JOIN STUDENTS st ON st.student_id=a.student_id
           JOIN SUBJECTS sub ON sub.subject_id=a.subject_id
           JOIN INSTRUCTORS i ON i.instructor_id=a.instructor_id
           WHERE a.attendance_id=?""",
        (attendance_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip((item[0] for item in cursor.description), row))


def create_makeup_session(
    conn,
    *,
    attendance_id: int,
    makeup_date: str,
    period_number: int,
    instructor_id: int,
    reason_category: str,
    reason_detail: str = "",
) -> int:
    absence = _get_absence(conn, attendance_id)
    if absence is None or absence["status"] != "欠席":
        raise ValueError("振替元となる欠席記録が見つかりません")
    if reason_category not in REASON_CATEGORIES:
        raise ValueError("振替理由を選択してください")

    _term_id, _term_name, day_of_week, decisions = evaluate_makeup_slots(
        conn, absence["student_id"], instructor_id, makeup_date
    )
    decision = decisions.get((day_of_week, period_number))
    if decision is None:
        raise ValueError("振替先の限が不正です")
    if decision.disabled:
        raise ValueError(
            f"{makeup_date} {period_number}限には振替できません（{decision.reason}）"
        )

    try:
        cursor = conn.execute(
            """INSERT INTO MAKEUP_SESSIONS(
                   attendance_id,makeup_date,period_number,instructor_id,
                   reason_category,reason_detail)
               VALUES(?,?,?,?,?,?)""",
            (
                attendance_id,
                makeup_date,
                period_number,
                instructor_id,
                reason_category,
                reason_detail.strip() or None,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError("この欠席には既に振替先が登録されています") from exc
    return int(cursor.lastrowid)


def render_unscheduled(qs: dict, message_html: str = "") -> str:
    conn = get_conn()
    rows = list_unscheduled_makeups(conn)
    conn.close()
    rows_html = "".join(
        f"""<tr>
          <td>{html.escape(row['student_name'])}</td>
          <td>{html.escape(row['subject_name'])}</td>
          <td>{html.escape(row['session_date'])}・{row['period_number']}限</td>
          <td>{html.escape(row['instructor_name'])}</td>
          <td><a href="/makeup-schedule?attendance_id={row['attendance_id']}">振替先を決める</a></td>
        </tr>"""
        for row in rows
    ) or '<tr><td colspan="5" class="hint">振替先未定の欠席はありません</td></tr>'
    return f"""
    <h1>未配置振替一覧</h1>
    <div class="hint">欠席になった授業のうち、振替先がまだ決まっていないものです。</div>
    {message_html}
    <table>
      <tr><th>生徒</th><th>科目</th><th>元の日付・限</th><th>元の担当講師</th><th></th></tr>
      {rows_html}
    </table>
    """


def _options(rows, selected: str) -> str:
    return "".join(
        f'<option value="{item_id}"{" selected" if str(item_id) == selected else ""}>'
        f'{html.escape(str(label))}</option>'
        for item_id, label in rows
    )


def render_schedule(qs: dict, message_html: str = "") -> str:
    attendance_text = qs.get("attendance_id", [""])[0]
    if not attendance_text:
        return '<h1>振替先を決める</h1><div class="msg error">欠席記録を指定してください</div>'
    try:
        attendance_id = int(attendance_text)
    except ValueError:
        return '<h1>振替先を決める</h1><div class="msg error">欠席記録の指定が不正です</div>'

    conn = get_conn()
    absence = _get_absence(conn, attendance_id)
    if absence is None or absence["status"] != "欠席":
        conn.close()
        return '<h1>振替先を決める</h1><div class="msg error">振替元となる欠席記録が見つかりません</div>'

    existing = conn.execute(
        """SELECT m.makeup_date,m.period_number,m.reason_category,m.reason_detail,
                  i.last_name||i.first_name
           FROM MAKEUP_SESSIONS m
           JOIN INSTRUCTORS i ON i.instructor_id=m.instructor_id
           WHERE m.attendance_id=?""",
        (attendance_id,),
    ).fetchone()
    if existing:
        conn.close()
        detail = f"（{html.escape(existing[3])}）" if existing[3] else ""
        return f"""
        <h1>振替先を決める</h1>{message_html}
        <div class="msg success">振替先は {html.escape(existing[0])}・{existing[1]}限・
        {html.escape(existing[4])} に確定しています。理由：{html.escape(existing[2])}{detail}</div>
        <p><a href="/makeup-unscheduled">未配置振替一覧へ戻る</a></p>
        """

    instructors = list_instructors(conn)
    instructor_id = qs.get("instructor_id", [str(absence["instructor_id"])])[0]
    valid_instructor_ids = {str(item[0]) for item in instructors}
    if instructor_id not in valid_instructor_ids:
        instructor_id = str(instructors[0][0]) if instructors else ""
    makeup_date = qs.get("makeup_date", [date.today().isoformat()])[0]
    reason_category = qs.get("reason_category", ["生徒都合"])[0]
    if reason_category not in REASON_CATEGORIES:
        reason_category = "生徒都合"
    reason_detail = qs.get("reason_detail", [""])[0]

    grid_html = ""
    try:
        _term_id, term_name, day_of_week, decisions = evaluate_makeup_slots(
            conn, absence["student_id"], int(instructor_id), makeup_date
        )
        grid_html = (
            f'<div class="hint">{html.escape(makeup_date)}（{day_of_week}）・'
            f'対象学期：{html.escape(term_name)}。押せる限だけ振替できます。</div>'
            + build_single_day_period_grid(
                action_path="/makeup-schedule",
                day_of_week=day_of_week,
                decisions=decisions,
                hidden_fields={
                    "action": "schedule",
                    "attendance_id": attendance_id,
                    "instructor_id": instructor_id,
                    "makeup_date": makeup_date,
                    "reason_category": reason_category,
                    "reason_detail": reason_detail,
                },
            )
        )
    except (TypeError, ValueError) as exc:
        grid_html = f'<div class="msg error">{html.escape(str(exc))}</div>'
    conn.close()

    reason_options = "".join(
        f'<option value="{item}"{" selected" if item == reason_category else ""}>{item}</option>'
        for item in REASON_CATEGORIES
    )
    return f"""
    <h1>振替先を決める</h1>
    <div class="hint">元の授業：{html.escape(absence['session_date'])}・{absence['period_number']}限　
      {html.escape(absence['student_name'])}／{html.escape(absence['subject_name'])}／
      {html.escape(absence['original_instructor_name'])}</div>
    {message_html}
    <form method="GET" action="/makeup-schedule">
      <input type="hidden" name="attendance_id" value="{attendance_id}">
      <label>振替担当講師 <span class="req">*</span></label>
      <select name="instructor_id" required onchange="this.form.submit()">
        {_options(instructors, instructor_id)}
      </select>
      <label>振替理由 <span class="req">*</span></label>
      <select name="reason_category" required onchange="this.form.submit()">{reason_options}</select>
      <label>理由の詳細（任意）</label>
      <input id="makeup-reason-detail" type="text" name="reason_detail"
             value="{html.escape(reason_detail)}">
      <label>振替日 <span class="req">*</span></label>
      <input type="date" name="makeup_date" value="{html.escape(makeup_date)}"
             required onchange="this.form.submit()">
    </form>
    {grid_html}
    <script>
      document.querySelectorAll('.makeup-period-grid form').forEach(function(form) {{
        form.addEventListener('submit', function() {{
          const detail = document.getElementById('makeup-reason-detail');
          const hidden = form.querySelector('input[name="reason_detail"]');
          if (detail && hidden) hidden.value = detail.value;
        }});
      }});
    </script>
    <p><a href="/makeup-unscheduled">未配置振替一覧へ戻る</a></p>
    """


def handle_schedule_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    if get("action") != "schedule":
        raise ValueError(f"不明な action です: {get('action')}")
    makeup_id = create_makeup_session(
        conn,
        attendance_id=int(get("attendance_id")),
        makeup_date=get("makeup_date"),
        period_number=int(get("period_number")),
        instructor_id=int(get("instructor_id")),
        reason_category=get("reason_category"),
        reason_detail=get("reason_detail"),
    )
    return (
        f'<div class="msg success">振替を登録しました → makeup_id={makeup_id}</div>',
        {"attendance_id": [get("attendance_id")]},
    )
