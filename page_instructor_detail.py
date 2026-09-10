# -*- coding: utf-8 -*-
"""講師の担当状況をまとめて確認する、編集機能を持たない詳細ページ。"""

import datetime
import html

from db import get_conn


def _rows_as_dicts(cursor) -> list[dict]:
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _get_subjects_for_instructor(conn, instructor_id: int) -> list[dict]:
    return _rows_as_dicts(
        conn.execute(
            """SELECT s.grade_band, s.subject_group, s.subject_name, isub.proficiency_level
               FROM INSTRUCTOR_SUBJECTS isub
               JOIN SUBJECTS s ON s.subject_id = isub.subject_id
               WHERE isub.instructor_id = ?
               ORDER BY s.grade_band, s.subject_group, s.subject_name""",
            (instructor_id,),
        )
    )


def _get_active_courses_for_instructor(conn, instructor_id: int, table_name: str) -> list[dict]:
    if table_name not in {"REGULAR_COURSE_ENROLLMENTS", "FOLLOW_COURSE_ENROLLMENTS"}:
        raise ValueError("不正な授業区分です")
    today = datetime.date.today().isoformat()
    return _rows_as_dicts(
        conn.execute(
            f"""SELECT st.last_name || st.first_name AS student_name,
                       sub.subject_group, sub.subject_name, e.day_of_week, e.period_number
                FROM {table_name} e
                JOIN STUDENTS st ON st.student_id = e.student_id
                JOIN SUBJECTS sub ON sub.subject_id = e.subject_id
                WHERE e.instructor_id = ?
                  AND e.effective_start_date <= ?
                  AND (e.effective_end_date IS NULL OR e.effective_end_date > ?)
                ORDER BY CASE e.day_of_week
                           WHEN '月' THEN 1 WHEN '火' THEN 2 WHEN '水' THEN 3
                           WHEN '木' THEN 4 WHEN '金' THEN 5 WHEN '土' THEN 6 ELSE 7 END,
                         e.period_number, st.last_name_kana, st.first_name_kana""",
            (instructor_id, today, today),
        )
    )


def _get_camp_enrollments_for_instructor(conn, instructor_id: int) -> list[dict]:
    """個別指定または現在の継続講師として関係する講習会契約を返す。"""
    return _rows_as_dicts(
        conn.execute(
            """SELECT c.camp_id, c.camp_name, c.planned_start_date,
                      st.last_name || st.first_name AS student_name,
                      sub.subject_group, sub.subject_name,
                      e.contracted_count, e.format,
                      CASE WHEN e.assigned_instructor_id = ? THEN '個別指定' ELSE '継続講師' END
                           AS relationship
               FROM CAMP_COURSE_ENROLLMENTS e
               JOIN CAMPS c ON c.camp_id = e.camp_id
               JOIN STUDENTS st ON st.student_id = e.student_id
               JOIN SUBJECTS sub ON sub.subject_id = e.subject_id
               WHERE e.assigned_instructor_id = ?
                  OR EXISTS (
                       SELECT 1 FROM REGULAR_COURSE_ENROLLMENTS regular
                       WHERE regular.student_id = e.student_id
                         AND regular.subject_id = e.subject_id
                         AND regular.instructor_id = ?
                         AND regular.effective_end_date IS NULL
                  )
               ORDER BY c.planned_start_date DESC, c.camp_id DESC,
                        st.last_name_kana, st.first_name_kana, sub.subject_group, sub.subject_name""",
            (instructor_id, instructor_id, instructor_id),
        )
    )


def _course_table(rows: list[dict], empty_message: str) -> str:
    body = "".join(
        f"<tr><td>{html.escape(row['student_name'])}</td>"
        f"<td>{html.escape(row['subject_group'])}/{html.escape(row['subject_name'])}</td>"
        f"<td>{row['day_of_week']}曜{row['period_number']}限</td></tr>"
        for row in rows
    )
    if not body:
        body = f'<tr><td colspan="3" class="hint">{html.escape(empty_message)}</td></tr>'
    return f"<table><tr><th>生徒</th><th>科目</th><th>曜日・限</th></tr>{body}</table>"


def render(qs: dict, message_html: str = "") -> str:
    instructor_id = qs.get("instructor_id", [""])[0]
    conn = get_conn()
    instructors = conn.execute(
        "SELECT instructor_id, last_name || ' ' || first_name FROM INSTRUCTORS ORDER BY last_name_kana, first_name_kana"
    ).fetchall()

    body_html = ""
    if instructor_id:
        info = conn.execute(
            """SELECT last_name, first_name, academic_year, status
               FROM INSTRUCTORS WHERE instructor_id = ?""",
            (int(instructor_id),),
        ).fetchone()
        if info is None:
            body_html = '<div class="msg error">選択された講師が見つかりません</div>'
        else:
            academic_year_label = info[2] or "未設定"
            subjects = _get_subjects_for_instructor(conn, int(instructor_id))
            subject_rows = "".join(
                f"<tr><td>{html.escape(row['grade_band'])}</td>"
                f"<td>{html.escape(row['subject_group'])}/{html.escape(row['subject_name'])}</td>"
                f"<td>{row['proficiency_level']}</td></tr>"
                for row in subjects
            ) or '<tr><td colspan="3" class="hint">担当科目の登録はありません</td></tr>'

            regular = _get_active_courses_for_instructor(conn, int(instructor_id), "REGULAR_COURSE_ENROLLMENTS")
            follow = _get_active_courses_for_instructor(conn, int(instructor_id), "FOLLOW_COURSE_ENROLLMENTS")
            camp_rows = _get_camp_enrollments_for_instructor(conn, int(instructor_id))
            camps: dict[tuple[int, str], list[dict]] = {}
            for row in camp_rows:
                camps.setdefault((row["camp_id"], row["camp_name"]), []).append(row)

            camp_sections = ""
            for (_camp_id, camp_name), rows in camps.items():
                rows_html = "".join(
                    f"<tr><td>{html.escape(row['student_name'])}</td>"
                    f"<td>{html.escape(row['subject_group'])}/{html.escape(row['subject_name'])}</td>"
                    f"<td>{row['contracted_count']}コマ</td><td>{html.escape(row['format'])}</td>"
                    f"<td>{html.escape(row['relationship'])}</td></tr>"
                    for row in rows
                )
                camp_sections += f"""
                <h2 style="font-size:13px;color:#333;margin-top:16px;">{html.escape(camp_name)}</h2>
                <table><tr><th>生徒</th><th>科目</th><th>コマ数</th><th>形式</th><th>関係</th></tr>
                  {rows_html}
                </table>
                """
            if not camp_sections:
                camp_sections = '<div class="hint">関係する講習会の受講科目登録はありません</div>'

            body_html = f"""
            <div style="margin-top:16px;padding:14px;background:#f7f8f7;border-radius:7px;">
              <strong>{html.escape(info[0])} {html.escape(info[1])}</strong><br>
              <span style="font-size:13px;color:#666;">学年: {html.escape(academic_year_label)} / ステータス: {html.escape(info[3])}</span>
            </div>

            <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">担当科目</h1>
            <table><tr><th>学年帯</th><th>科目</th><th>習熟度</th></tr>{subject_rows}</table>

            <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">通常授業</h1>
            {_course_table(regular, '現在担当している通常授業はありません')}

            <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">教科フォロー</h1>
            {_course_table(follow, '現在担当している教科フォローはありません')}

            <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">講習会の受講状況</h1>
            {camp_sections}
            """

    options = "".join(
        f'<option value="{iid}"{" selected" if str(iid) == instructor_id else ""}>{html.escape(name)}</option>'
        for iid, name in instructors
    )
    conn.close()
    return f"""
    <h1>講師詳細</h1>
    <div class="hint">講師の担当科目・通常授業・教科フォロー・講習会との関係を確認できます（確認専用）</div>
    {message_html}
    <label>講師</label>
    <select onchange="location.href='/instructor-detail?instructor_id='+this.value">
      <option value="">選択してください</option>{options}
    </select>
    {body_html}
    """
