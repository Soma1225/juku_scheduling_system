# -*- coding: utf-8 -*-
"""
page_availability.py

生徒(STUDENT_WEEKLY_AVAILABILITY)・講師(INSTRUCTOR_WEEKLY_AVAILABILITY)の
対応可能時間を、曜日×限のグリッドで登録するページ。
テーブル名だけが違う、ほぼ同一のロジックなので、内部で共通化している。
"""

from db import get_conn
from page_terms import list_terms
from page_instructors import list_instructors

DAYS = ["月", "火", "水", "木", "金", "土", "日"]
PERIOD_NUMBERS = [1, 2, 3, 4, 5]


# ---------------------------------------------------------
# 共通DB操作(生徒/講師どちらのテーブルにも使う)
# ---------------------------------------------------------

def _get_available_cells(conn, table, id_col, entity_id, term_id) -> set:
    rows = conn.execute(
        f"SELECT day_of_week, period_number FROM {table} WHERE {id_col} = ? AND term_id = ? AND is_available = 1",
        (entity_id, term_id),
    ).fetchall()
    return {(d, p) for d, p in rows}


def _save_availability(conn, table, id_col, entity_id, term_id, checked_cells) -> None:
    conn.execute(f"DELETE FROM {table} WHERE {id_col} = ? AND term_id = ?", (entity_id, term_id))
    conn.executemany(
        f"INSERT INTO {table} ({id_col}, term_id, day_of_week, period_number, is_available) VALUES (?, ?, ?, ?, 1)",
        [(entity_id, term_id, d, p) for d, p in checked_cells],
    )
    conn.commit()


def _build_select_options(rows, selected_id) -> str:
    return "".join(
        f'<option value="{i}"{" selected" if str(i) == selected_id else ""}>{name}</option>' for i, name in rows
    )


def _build_grid(entity_id, term_id, checked, form_action, id_field) -> str:
    day_headers = "".join(f"<th>{d}</th>" for d in DAYS)
    body_rows = ""
    for p in PERIOD_NUMBERS:
        cells = "".join(
            f'<td><input type="checkbox" name="avail_{d}_{p}" {"checked" if (d, p) in checked else ""}></td>'
            for d in DAYS
        )
        body_rows += f"<tr><td>{p}限</td>{cells}</tr>"
    return f"""
    <form method="POST" action="{form_action}">
      <input type="hidden" name="{id_field}" value="{entity_id}">
      <input type="hidden" name="term_id" value="{term_id}">
      <table class="grid"><tr><th>限＼曜日</th>{day_headers}</tr>{body_rows}</table>
      <button type="submit">保存する</button>
    </form>
    """


def _extract_checked_cells(fields: dict) -> set:
    return {(k.split("_")[1], int(k.split("_")[2])) for k in fields if k.startswith("avail_")}


# ---------------------------------------------------------
# 生徒版: 外部公開インターフェース
# ---------------------------------------------------------

def render_student(qs: dict, message_html: str = "") -> str:
    student_id = qs.get("student_id", [""])[0]
    term_id = qs.get("term_id", [""])[0]
    conn = get_conn()
    students = conn.execute(
        "SELECT student_id, last_name || ' ' || first_name FROM STUDENTS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()
    terms = list_terms(conn)
    grid_html = ""
    if student_id and term_id:
        checked = _get_available_cells(conn, "STUDENT_WEEKLY_AVAILABILITY", "student_id", int(student_id), int(term_id))
        grid_html = _build_grid(student_id, term_id, checked, "/student-availability", "student_id")
    conn.close()
    return f"""
    <h1>生徒 対応可能時間</h1>
    {message_html}
    <form method="GET" action="/student-availability">
      <label>生徒</label>
      <select name="student_id"><option value="">選択してください</option>{_build_select_options(students, student_id)}</select>
      <label>学期</label>
      <select name="term_id"><option value="">選択してください</option>{_build_select_options(terms, term_id)}</select>
      <button type="submit" style="background:#5F5E5A;">読み込む</button>
    </form>
    {grid_html}
    """


def handle_post_student(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    student_id, term_id = get("student_id"), get("term_id")
    if not student_id or not term_id:
        raise ValueError("生徒と学期を選択してください")
    checked = _extract_checked_cells(fields)
    _save_availability(conn, "STUDENT_WEEKLY_AVAILABILITY", "student_id", int(student_id), int(term_id), checked)
    message_html = f'<div class="msg success">保存しました({len(checked)}コマ 対応可能)</div>'
    return message_html, {"student_id": [student_id], "term_id": [term_id]}


# ---------------------------------------------------------
# 講師版: 外部公開インターフェース
# ---------------------------------------------------------

def render_instructor(qs: dict, message_html: str = "") -> str:
    instructor_id = qs.get("instructor_id", [""])[0]
    term_id = qs.get("term_id", [""])[0]
    conn = get_conn()
    instructors = list_instructors(conn)
    terms = list_terms(conn)
    grid_html = ""
    if instructor_id and term_id:
        checked = _get_available_cells(conn, "INSTRUCTOR_WEEKLY_AVAILABILITY", "instructor_id", int(instructor_id), int(term_id))
        grid_html = _build_grid(instructor_id, term_id, checked, "/instructor-availability", "instructor_id")
    conn.close()
    return f"""
    <h1>講師 対応可能時間</h1>
    {message_html}
    <form method="GET" action="/instructor-availability">
      <label>講師</label>
      <select name="instructor_id"><option value="">選択してください</option>{_build_select_options(instructors, instructor_id)}</select>
      <label>学期</label>
      <select name="term_id"><option value="">選択してください</option>{_build_select_options(terms, term_id)}</select>
      <button type="submit" style="background:#5F5E5A;">読み込む</button>
    </form>
    {grid_html}
    """


def handle_post_instructor(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    instructor_id, term_id = get("instructor_id"), get("term_id")
    if not instructor_id or not term_id:
        raise ValueError("講師と学期を選択してください")
    checked = _extract_checked_cells(fields)
    _save_availability(conn, "INSTRUCTOR_WEEKLY_AVAILABILITY", "instructor_id", int(instructor_id), int(term_id), checked)
    message_html = f'<div class="msg success">保存しました({len(checked)}コマ 対応可能)</div>'
    return message_html, {"instructor_id": [instructor_id], "term_id": [term_id]}
