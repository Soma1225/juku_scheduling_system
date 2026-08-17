# -*- coding: utf-8 -*-
"""
page_camp_availability.py

講習会期間中の対応可能時間を、日付×限のグリッドで登録するページ。
通常期(page_availability.py)は「曜日の繰り返しパターン」だったが、
講習会期間中は「特定の日付ごと」に集める点が異なる。
"""

from db import get_conn
from page_camps import list_camps
from page_instructors import list_instructors

PERIOD_NUMBERS = [1, 2, 3, 4, 5]


def _get_slot_ids_for_camp(conn, camp_id) -> list[tuple[int, str, int]]:
    """
    講習会の"計画期間"内(バッファを含まない)にあるTIME_SLOTSを、日付順・限順で返す。
    戻り値: [(slot_id, session_date, period_number), ...]
    """
    camp = conn.execute(
        "SELECT planned_start_date, planned_end_date FROM CAMPS WHERE camp_id = ?", (camp_id,)
    ).fetchone()
    if camp is None:
        return []
    start, end = camp
    return conn.execute(
        """SELECT slot_id, session_date, period_number FROM TIME_SLOTS
           WHERE session_date BETWEEN ? AND ?
           ORDER BY session_date, period_number""",
        (start, end),
    ).fetchall()


def _get_available_slot_ids(conn, table, id_col, entity_id, camp_id) -> set[int]:
    slot_rows = _get_slot_ids_for_camp(conn, camp_id)
    slot_ids = tuple(r[0] for r in slot_rows)
    if not slot_ids:
        return set()
    placeholders = ",".join("?" * len(slot_ids))
    rows = conn.execute(
        f"""SELECT slot_id FROM {table}
            WHERE {id_col} = ? AND is_available = 1 AND slot_id IN ({placeholders})""",
        (entity_id, *slot_ids),
    ).fetchall()
    return {r[0] for r in rows}


def _save_availability(conn, table, id_col, entity_id, camp_id, checked_slot_ids: set[int]) -> None:
    slot_rows = _get_slot_ids_for_camp(conn, camp_id)
    all_slot_ids = [r[0] for r in slot_rows]
    if not all_slot_ids:
        return
    placeholders = ",".join("?" * len(all_slot_ids))
    conn.execute(
        f"DELETE FROM {table} WHERE {id_col} = ? AND slot_id IN ({placeholders})",
        (entity_id, *all_slot_ids),
    )
    conn.executemany(
        f"INSERT INTO {table} ({id_col}, slot_id, is_available) VALUES (?, ?, 1)",
        [(entity_id, sid) for sid in checked_slot_ids],
    )
    conn.commit()


def _build_select_options(rows, selected_id) -> str:
    return "".join(
        f'<option value="{i}"{" selected" if str(i) == selected_id else ""}>{name}</option>' for i, name in rows
    )


def _build_grid(entity_id, camp_id, slot_rows, checked_slot_ids, form_action, id_field) -> str:
    dates = sorted(set(r[1] for r in slot_rows))
    slot_lookup = {(r[1], r[2]): r[0] for r in slot_rows}  # (date, period) -> slot_id

    day_headers = "".join(f"<th>{d[5:]}</th>" for d in dates)  # MM-DDだけ表示
    body_rows = ""
    for p in PERIOD_NUMBERS:
        cells = ""
        for d in dates:
            sid = slot_lookup.get((d, p))
            if sid is None:
                cells += "<td>-</td>"
                continue
            checked = "checked" if sid in checked_slot_ids else ""
            cells += f'<td><input type="checkbox" name="slot_{sid}" {checked}></td>'
        body_rows += f"<tr><td>{p}限</td>{cells}</tr>"

    return f"""
    <form method="POST" action="{form_action}">
      <input type="hidden" name="{id_field}" value="{entity_id}">
      <input type="hidden" name="camp_id" value="{camp_id}">
      <div style="overflow-x:auto;">
        <table class="grid"><tr><th>限＼日付</th>{day_headers}</tr>{body_rows}</table>
      </div>
      <button type="submit">保存する</button>
    </form>
    """


def _extract_checked_slot_ids(fields: dict) -> set[int]:
    return {int(k.split("_")[1]) for k in fields if k.startswith("slot_")}


# ---------------------------------------------------------
# 生徒版
# ---------------------------------------------------------

def render_student(qs: dict, message_html: str = "") -> str:
    student_id = qs.get("student_id", [""])[0]
    camp_id = qs.get("camp_id", [""])[0]
    conn = get_conn()
    students = conn.execute(
        "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    camps = list_camps(conn)
    grid_html = ""
    if student_id and camp_id:
        slot_rows = _get_slot_ids_for_camp(conn, int(camp_id))
        checked = _get_available_slot_ids(conn, "CAMP_STUDENT_AVAILABILITY", "student_id", int(student_id), int(camp_id))
        if slot_rows:
            grid_html = _build_grid(student_id, camp_id, slot_rows, checked, "/camp-availability-student", "student_id")
        else:
            grid_html = '<div class="hint">この講習会の日付枠がまだありません</div>'
    conn.close()
    return f"""
    <h1>生徒 講習会中の対応可能時間</h1>
    {message_html}
    <form method="GET" action="/camp-availability-student">
      <label>生徒</label>
      <select name="student_id"><option value="">選択してください</option>{_build_select_options(students, student_id)}</select>
      <label>講習会</label>
      <select name="camp_id"><option value="">選択してください</option>{_build_select_options(camps, camp_id)}</select>
      <button type="submit" style="background:#5F5E5A;">読み込む</button>
    </form>
    {grid_html}
    """


def handle_post_student(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    student_id, camp_id = get("student_id"), get("camp_id")
    if not student_id or not camp_id:
        raise ValueError("生徒と講習会を選択してください")
    checked = _extract_checked_slot_ids(fields)
    _save_availability(conn, "CAMP_STUDENT_AVAILABILITY", "student_id", int(student_id), int(camp_id), checked)
    message_html = f'<div class="msg success">保存しました({len(checked)}コマ 対応可能)</div>'
    return message_html, {"student_id": [student_id], "camp_id": [camp_id]}


# ---------------------------------------------------------
# 講師版
# ---------------------------------------------------------

def render_instructor(qs: dict, message_html: str = "") -> str:
    instructor_id = qs.get("instructor_id", [""])[0]
    camp_id = qs.get("camp_id", [""])[0]
    conn = get_conn()
    instructors = list_instructors(conn)
    camps = list_camps(conn)
    grid_html = ""
    if instructor_id and camp_id:
        slot_rows = _get_slot_ids_for_camp(conn, int(camp_id))
        checked = _get_available_slot_ids(conn, "CAMP_INSTRUCTOR_AVAILABILITY", "instructor_id", int(instructor_id), int(camp_id))
        if slot_rows:
            grid_html = _build_grid(instructor_id, camp_id, slot_rows, checked, "/camp-availability-instructor", "instructor_id")
        else:
            grid_html = '<div class="hint">この講習会の日付枠がまだありません</div>'
    conn.close()
    return f"""
    <h1>講師 講習会中の対応可能時間</h1>
    {message_html}
    <form method="GET" action="/camp-availability-instructor">
      <label>講師</label>
      <select name="instructor_id"><option value="">選択してください</option>{_build_select_options(instructors, instructor_id)}</select>
      <label>講習会</label>
      <select name="camp_id"><option value="">選択してください</option>{_build_select_options(camps, camp_id)}</select>
      <button type="submit" style="background:#5F5E5A;">読み込む</button>
    </form>
    {grid_html}
    """


def handle_post_instructor(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    instructor_id, camp_id = get("instructor_id"), get("camp_id")
    if not instructor_id or not camp_id:
        raise ValueError("講師と講習会を選択してください")
    checked = _extract_checked_slot_ids(fields)
    _save_availability(conn, "CAMP_INSTRUCTOR_AVAILABILITY", "instructor_id", int(instructor_id), int(camp_id), checked)
    message_html = f'<div class="msg success">保存しました({len(checked)}コマ 対応可能)</div>'
    return message_html, {"instructor_id": [instructor_id], "camp_id": [camp_id]}
