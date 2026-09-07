# -*- coding: utf-8 -*-
"""page_instructor_subjects.py: 講師の担当科目(INSTRUCTOR_SUBJECTS)を管理するページ"""

from db import get_conn
from page_instructors import list_instructors


def get_subject_ids_by_group(conn, subject_group, grade_band=None, course_category=None) -> list[int]:
    query = "SELECT subject_id FROM SUBJECTS WHERE subject_group = ?"
    params = [subject_group]
    if grade_band is not None:
        query += " AND grade_band = ?"
        params.append(grade_band)
    if course_category is not None:
        query += " AND course_category = ?"
        params.append(course_category)
    return [row[0] for row in conn.execute(query, params).fetchall()]


def bulk_assign_subjects(conn, instructor_id, subject_group, proficiency_level, grade_band=None, course_category=None) -> int:
    if not (1 <= proficiency_level <= 2):
        raise ValueError("proficiency_level は 1〜2 で指定してください")
    subject_ids = get_subject_ids_by_group(conn, subject_group, grade_band, course_category)
    if not subject_ids:
        raise ValueError(f"subject_group='{subject_group}' に該当する科目が見つかりません")
    before = conn.execute("SELECT COUNT(*) FROM INSTRUCTOR_SUBJECTS WHERE instructor_id = ?", (instructor_id,)).fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO INSTRUCTOR_SUBJECTS (instructor_id, subject_id, proficiency_level) VALUES (?, ?, ?)",
        [(instructor_id, sid, proficiency_level) for sid in subject_ids],
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM INSTRUCTOR_SUBJECTS WHERE instructor_id = ?", (instructor_id,)).fetchone()[0]
    return after - before


def unassign_subject(conn, instructor_id, subject_id) -> bool:
    cur = conn.execute(
        "DELETE FROM INSTRUCTOR_SUBJECTS WHERE instructor_id = ? AND subject_id = ?", (instructor_id, subject_id)
    )
    conn.commit()
    return cur.rowcount > 0


def update_subject_proficiency(conn, instructor_id, subject_id, new_level) -> bool:
    if not (1 <= new_level <= 2):
        raise ValueError("proficiency_level は 1〜2 で指定してください")
    cur = conn.execute(
        "UPDATE INSTRUCTOR_SUBJECTS SET proficiency_level = ? WHERE instructor_id = ? AND subject_id = ?",
        (new_level, instructor_id, subject_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_instructor_subjects(conn, instructor_id) -> list[dict]:
    cur = conn.execute(
        """SELECT s.subject_id, s.subject_group, s.subject_name, s.grade_band, isub.proficiency_level
           FROM INSTRUCTOR_SUBJECTS isub JOIN SUBJECTS s ON s.subject_id = isub.subject_id
           WHERE isub.instructor_id = ? ORDER BY s.subject_group, s.subject_name""",
        (instructor_id,),
    )
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def render(qs: dict, message_html: str = "") -> str:
    instructor_id = qs.get("instructor_id", [""])[0]
    conn = get_conn()
    instructors = list_instructors(conn)

    body_html = ""
    if instructor_id:
        groups = conn.execute(
            "SELECT DISTINCT course_category, grade_band, subject_group FROM SUBJECTS "
            "ORDER BY course_category, grade_band, subject_group"
        ).fetchall()
        group_options = "".join(f'<option value="{cc}||{gb}||{sg}">[{cc}/{gb}] {sg}</option>' for cc, gb, sg in groups)

        current = list_instructor_subjects(conn, int(instructor_id))
        if current:
            rows = ""
            for row in current:
                prof_options = "".join(
                    f'<option value="{lv}"{" selected" if lv == row["proficiency_level"] else ""}>{lv}</option>'
                    for lv in range(1, 3)
                )
                rows += f"""
                <tr>
                  <td>{row['subject_group']} / {row['subject_name']}<br><small style="color:#999">{row['grade_band']}</small></td>
                  <td>
                    <form class="row-form" method="POST" action="/instructor-subjects">
                      <input type="hidden" name="action" value="update_proficiency">
                      <input type="hidden" name="instructor_id" value="{instructor_id}">
                      <input type="hidden" name="subject_id" value="{row['subject_id']}">
                      <select name="proficiency_level">{prof_options}</select>
                      <button class="btn-update" type="submit">更新</button>
                    </form>
                  </td>
                  <td>
                    <form class="row-form" method="POST" action="/instructor-subjects">
                      <input type="hidden" name="action" value="remove">
                      <input type="hidden" name="instructor_id" value="{instructor_id}">
                      <input type="hidden" name="subject_id" value="{row['subject_id']}">
                      <button class="btn-remove" type="submit">解除</button>
                    </form>
                  </td>
                </tr>
                """
            table_html = f"<table><tr><th>科目</th><th>習熟度</th><th></th></tr>{rows}</table>"
        else:
            table_html = '<div class="hint">まだ担当科目が登録されていません</div>'

        body_html = f"""
        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">上位グループで一括登録</h1>
        <form method="POST" action="/instructor-subjects">
          <input type="hidden" name="action" value="bulk_assign">
          <input type="hidden" name="instructor_id" value="{instructor_id}">
          <label>上位グループ</label>
          <select name="group_key">{group_options}</select>
          <label>習熟度(1〜2、自己申告)</label>
          <select name="proficiency_level">
            <option value="1">1</option><option value="2" selected>2</option>
          </select>
          <button type="submit" style="background:#534AB7;">このグループを一括登録する</button>
        </form>
        <h1 style="font-size:14px;color:#534AB7;margin-top:26px;">現在の担当科目 ({len(current)}件)</h1>
        {table_html}
        """

    conn.close()
    return f"""
    <h1>講師 担当科目</h1>
    {message_html}
    <form method="GET" action="/instructor-subjects">
      <label>講師</label>
      <select name="instructor_id"><option value="">選択してください</option>{_build_select_options(instructors, instructor_id)}</select>
      <button type="submit" style="background:#5F5E5A;">読み込む</button>
    </form>
    {body_html}
    """


def _build_select_options(rows, selected_id) -> str:
    return "".join(
        f'<option value="{i}"{" selected" if str(i) == selected_id else ""}>{name}</option>' for i, name in rows
    )


def handle_post(fields: dict, conn) -> tuple[str, dict]:
    def get(key, default=""):
        return fields.get(key, [default])[0]

    action = get("action")
    instructor_id = get("instructor_id")

    if action == "bulk_assign":
        course_category, grade_band, subject_group = get("group_key").split("||")
        n = bulk_assign_subjects(conn, int(instructor_id), subject_group, int(get("proficiency_level")),
                                  grade_band=grade_band, course_category=course_category)
        message_html = f'<div class="msg success">{n}件 新規登録しました</div>'
    elif action == "remove":
        unassign_subject(conn, int(instructor_id), int(get("subject_id")))
        message_html = '<div class="msg success">解除しました</div>'
    elif action == "update_proficiency":
        update_subject_proficiency(conn, int(instructor_id), int(get("subject_id")), int(get("proficiency_level")))
        message_html = '<div class="msg success">習熟度を更新しました</div>'
    else:
        raise ValueError(f"不明な action です: {action}")

    return message_html, {"instructor_id": [instructor_id]}
