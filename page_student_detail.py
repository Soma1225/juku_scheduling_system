# -*- coding: utf-8 -*-
"""
page_student_detail.py

生徒を1人選ぶと、その生徒が受けている科目(通常授業+講習会)を
一覧で確認できるページ。入力用ではなく、確認専用の画面。
"""

from db import get_conn, format_grade_label
from page_regular_enrollments import list_active_enrollments_for_student


def _get_camp_enrollments_for_student(conn, student_id) -> list[dict]:
    cur = conn.execute(
        """SELECT c.camp_name, sub.subject_group, sub.subject_name, e.contracted_count, e.format,
                  i.last_name || i.first_name AS instructor_name
           FROM CAMP_COURSE_ENROLLMENTS e
           JOIN CAMPS c ON c.camp_id = e.camp_id
           JOIN SUBJECTS sub ON sub.subject_id = e.subject_id
           LEFT JOIN INSTRUCTORS i ON i.instructor_id = e.assigned_instructor_id
           WHERE e.student_id = ?
           ORDER BY c.planned_start_date DESC""",
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

    body_html = ""
    if student_id:
        info = conn.execute(
            "SELECT last_name, first_name, base_grade, enrollment_status FROM STUDENTS WHERE student_id = ?",
            (student_id,),
        ).fetchone()

        regular_rows = list_active_enrollments_for_student(conn, int(student_id))
        regular_html = "".join(
            f"<tr><td>{r['subject_group']}/{r['subject_name']}</td><td>{r['instructor_name']}</td>"
            f"<td>{r['day_of_week']}{r['period_number']}限</td></tr>"
            for r in regular_rows
        ) or '<tr><td colspan="3" class="hint">通常授業の契約はありません</td></tr>'

        camp_rows = _get_camp_enrollments_for_student(conn, int(student_id))
        # 講習会ごとにグループ化して見せる
        by_camp: dict[str, list[dict]] = {}
        for r in camp_rows:
            by_camp.setdefault(r["camp_name"], []).append(r)

        camp_sections = ""
        for camp_name, items in by_camp.items():
            item_rows = "".join(
                f"<tr><td>{it['subject_group']}/{it['subject_name']}</td><td>{it['contracted_count']}コマ</td>"
                f"<td>{it['format']}</td><td>{it['instructor_name'] or '-'}</td></tr>"
                for it in items
            )
            camp_sections += f"""
            <h2 style="font-size:13px;color:#333;margin-top:16px;">{camp_name}</h2>
            <table><tr><th>科目</th><th>コマ数</th><th>形式</th><th>指定講師</th></tr>{item_rows}</table>
            """
        if not camp_sections:
            camp_sections = '<div class="hint">講習会の受講科目登録はありません</div>'

        status_line = f"{format_grade_label(info[2])} / {info[3]}" if info else ""

        body_html = f"""
        <div style="margin-top:8px;color:#666;font-size:13px;">{status_line}</div>

        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">通常授業</h1>
        <table><tr><th>科目</th><th>講師</th><th>曜日・限</th></tr>{regular_html}</table>

        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">講習会</h1>
        {camp_sections}
        """

    def options(rows, selected=""):
        return "".join(
            f'<option value="{i}"{" selected" if str(i) == selected else ""}>{name}</option>' for i, name in rows
        )

    conn.close()
    return f"""
    <h1>生徒詳細</h1>
    <div class="hint">生徒が現在受けている通常授業・講習会の科目を確認できます(確認専用、ここでは編集できません)</div>
    {message_html}
    <label>生徒</label>
    <select onchange="location.href='/student-detail?student_id='+this.value">
      <option value="">選択してください</option>{options(students, student_id)}
    </select>
    {body_html}
    """
